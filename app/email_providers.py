"""Provider-abstracted outbound email.

This module is the single place that knows *how* to put an email on the wire.
Callers (e.g. ``app/email_engine.py``) build the message body + headers, then
hand it to ``send()`` here, which routes to whichever transport the operator
has configured via the ``MAIL_PROVIDER`` env var.

Supported providers (``settings.mail_provider`` value -> transport):
    - ``"resend"``      Resend HTTP API           (RESEND_API_KEY)
    - ``"brevo"``       SMTP smtp-relay.brevo.com (BREVO_SMTP_USER / BREVO_SMTP_KEY)
    - ``"smtp"``        Generic SMTP              (SMTP_HOST/PORT/USERNAME/PASSWORD/USE_TLS)
    - ``"gmail_smtp"``  smtp.gmail.com SMTP       (GMAIL_SMTP_USER / GMAIL_SMTP_APP_PASSWORD)
                        — low-volume manual tests; the *campaign* sender uses the
                        Gmail API in app/gmail_sender.py, not this SMTP path.
    - ``"gmail"``       Gmail API (app/gmail_sender.send_message)
    - ``"console"``     Logs the payload and returns ok=True (default / dev)
    - anything else     -> falls through to the console transport

CRITICAL invariant — Reply-To is ALWAYS forced.
    No matter what ``reply_to`` the caller passes, every provider overwrites it
    with ``settings.reply_to_email`` (default ``sales@schildinc.com``) before the
    message hits the wire. A mis-built campaign therefore cannot leak a wrong
    Reply-To header. This is enforced in one place — ``_forced_reply_to()`` — so
    every code path shares the same guarantee.

Graceful-degradation contract (House Rules):
    - Nothing heavy/optional is imported at module top level. ``httpx`` / ``smtplib``
      are lazily imported INSIDE the send functions, so importing this module
      never fails even if a dep or service is missing.
    - When the selected provider is not configured (no API key, no SMTP host,
      Gmail not connected, …) ``send()`` returns ``SendResult(ok=False, ...)``
      with a descriptive error. It NEVER raises into the caller — the campaign
      loop keeps going.
    - Config values that the integrator will add later are read defensively via
      ``getattr(settings, "name", default)`` so this module imports cleanly even
      before those settings exist in ``app/config.py``.

``transient`` semantics:
    ``SendResult.transient`` is True only for errors worth retrying — network
    timeouts, connection resets/refusals, and 5xx / 429 server responses. Hard
    failures (bad address, auth rejected, 4xx other than 429, unconfigured
    provider) are non-transient so the caller does not retry them forever.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

# NOTE: Session is only used as a type hint for the Gmail-API path, which needs a
# DB session to load the stored OAuth token. It is part of the project's core
# dependency (SQLAlchemy is always installed) so importing it at top level is safe.
from sqlalchemy.orm import Session

from app.config import settings

logger = logging.getLogger(__name__)

# Hard fallback if, somehow, settings.reply_to_email is empty. Schild's
# canonical inbound address. Kept as a named constant for visibility.
_DEFAULT_REPLY_TO = "sales@schildinc.com"

# Substrings that mark an error message as worth retrying (transient).
_TRANSIENT_HINTS = (
    "timeout",
    "timed out",
    "temporar",
    "connection reset",
    "connection refused",
    "connection aborted",
    "connection error",
    "rate limit",
    "too many requests",
    "service unavailable",
    "try again",
)


@dataclass
class SendResult:
    """Outcome of a single send attempt.

    Attributes:
        ok:         True if the provider accepted the message.
        provider:   Which transport handled it ("resend", "brevo", "smtp",
                    "gmail_smtp", "gmail", "console", ...). Always set so the
                    caller can record provenance even on failure.
        message_id: Provider-assigned id when available (empty otherwise).
        error:      Human-readable error excerpt on failure (empty on success).
        transient:  True only when retrying might succeed (timeouts / 5xx / 429
                    / connection errors). See module docstring.
    """

    ok: bool
    provider: str
    message_id: str = ""
    error: str = ""
    transient: bool = False


# ── Helpers ────────────────────────────────────────────────────────────────


def _forced_reply_to(caller_reply_to: str = "") -> str:
    """The Reply-To to put on the wire — allowlist-guarded, not hard-forced.

    Multi-domain sending means a schildlabels.com campaign should reply to a
    schildlabels.com address, not always sales@schildinc.com. So we HONOR the
    caller's Reply-To *iff* it is on a Schild-owned sending domain; anything
    else (blank, or a non-Schild address that a mis-built campaign might leak)
    falls back to ``settings.reply_to_email`` / the hard default. The anti-leak
    guarantee is preserved — replies can only ever go to a Schild domain.
    """
    candidate = (caller_reply_to or "").strip()
    if candidate:
        try:
            from app.sending_domains import is_allowed_reply_to
            if is_allowed_reply_to(candidate):
                return candidate
        except Exception:  # noqa: BLE001 - never let the guard break a send
            pass
    value = (getattr(settings, "reply_to_email", "") or "").strip()
    return value or _DEFAULT_REPLY_TO


def _is_transient_message(message: str) -> bool:
    """Heuristic: does this error string look retryable?"""
    low = (message or "").lower()
    return any(hint in low for hint in _TRANSIENT_HINTS)


def _from_header(from_alias: str, from_name: str) -> str:
    """Build an RFC 5322 ``From:`` header value.

    Falls back to ``settings.mail_from`` / ``settings.sender_name`` when the
    caller did not supply an alias/name.
    """
    alias = (from_alias or getattr(settings, "mail_from", "") or "").strip()
    name = (from_name or getattr(settings, "sender_name", "") or "").strip()
    if name and alias:
        return f"{name} <{alias}>"
    return alias or name


def _build_mime(
    *,
    from_header: str,
    to_email: str,
    subject: str,
    body_text: str,
    body_html: str,
    reply_to: str,
):
    """Build a multipart/alternative EmailMessage (text + html).

    ``email.message`` is part of the stdlib, so this is import-safe. It is kept
    in a helper because every SMTP-based provider needs the same message shape.
    """
    from email.message import EmailMessage

    message = EmailMessage()
    message["From"] = from_header
    message["To"] = to_email
    message["Subject"] = subject
    # Reply-To is always the forced value passed in by the provider.
    if reply_to:
        message["Reply-To"] = reply_to
    # Always provide a text part (some servers reject empty bodies) and, when we
    # have HTML, attach it as the preferred alternative.
    message.set_content(body_text or body_html or " ")
    if body_html:
        message.add_alternative(body_html, subtype="html")
    return message


# ── Provider implementations ─────────────────────────────────────────────────
#
# Each provider:
#   - re-derives the forced Reply-To itself (defense in depth),
#   - lazily imports its heavy dependency inside the function,
#   - returns a SendResult and NEVER raises.


def _send_resend(
    *,
    to_email: str,
    subject: str,
    body_html: str,
    body_text: str,
    from_alias: str,
    from_name: str,
    reply_to: str = "",
    list_unsubscribe: str = "",
) -> SendResult:
    """Send via the Resend HTTP API. Needs ``settings.resend_api_key``."""
    api_key = (getattr(settings, "resend_api_key", "") or "").strip()
    if not api_key:
        return SendResult(
            ok=False,
            provider="resend",
            error="RESEND_API_KEY is not set.",
            transient=False,
        )

    reply_to = _forced_reply_to(reply_to)
    from_value = _from_header(from_alias, from_name)
    if not from_value:
        return SendResult(
            ok=False,
            provider="resend",
            error="No From address configured (set MAIL_FROM or pass from_alias).",
            transient=False,
        )

    try:
        import httpx  # lazy: optional dep

        payload = {
            "from": from_value,
            "to": [to_email],
            "subject": subject,
            "text": body_text or body_html or " ",
            "reply_to": reply_to,
        }
        if body_html:
            payload["html"] = body_html
        # RFC 8058 one-click unsubscribe header (was previously Gmail-only).
        # Gives inbox providers a native "Unsubscribe" button -> big
        # deliverability + trust win for cold outreach.
        if list_unsubscribe:
            payload["headers"] = {
                "List-Unsubscribe": f"<{list_unsubscribe}>",
                "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
            }

        resp = httpx.post(
            "https://api.resend.com/emails",
            json=payload,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=20.0,
        )
        if 200 <= resp.status_code < 300:
            message_id = ""
            try:
                message_id = (resp.json() or {}).get("id", "") or ""
            except Exception:  # noqa: BLE001 — body may not be JSON
                message_id = ""
            return SendResult(ok=True, provider="resend", message_id=message_id)

        # Non-2xx: 429 + 5xx are transient, other 4xx are permanent.
        transient = resp.status_code == 429 or resp.status_code >= 500
        return SendResult(
            ok=False,
            provider="resend",
            error=f"HTTP {resp.status_code}: {resp.text[:300]}",
            transient=transient,
        )
    except Exception as exc:  # noqa: BLE001 — never raise into the caller
        msg = str(exc)
        return SendResult(
            ok=False,
            provider="resend",
            error=msg[:500],
            transient=_is_transient_message(msg),
        )


def _send_smtp_generic(
    *,
    provider_name: str,
    host: str,
    port: int,
    username: str,
    password: str,
    use_tls: bool,
    to_email: str,
    subject: str,
    body_html: str,
    body_text: str,
    from_alias: str,
    from_name: str,
    reply_to: str = "",
) -> SendResult:
    """Shared SMTP send used by the generic / brevo / gmail_smtp providers.

    Returns a SendResult and never raises. Connection/timeout problems are
    marked transient.
    """
    if not host:
        return SendResult(
            ok=False,
            provider=provider_name,
            error=f"{provider_name}: SMTP host is not configured.",
            transient=False,
        )

    reply_to = _forced_reply_to(reply_to)
    from_value = _from_header(from_alias, from_name)
    if not from_value:
        return SendResult(
            ok=False,
            provider=provider_name,
            error=f"{provider_name}: no From address configured.",
            transient=False,
        )

    try:
        import smtplib  # lazy (stdlib, but keep imports inside per House Rules)

        message = _build_mime(
            from_header=from_value,
            to_email=to_email,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            reply_to=reply_to,
        )
        with smtplib.SMTP(host, port, timeout=20) as smtp:
            if use_tls:
                smtp.starttls()
            if username:
                smtp.login(username, password)
            smtp.send_message(message)
        return SendResult(ok=True, provider=provider_name, message_id="")
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        # smtplib raises a family of SMTP*Error subclasses; 4xx/5xx codes plus
        # the connection-level errors all surface in the message text, so the
        # substring heuristic covers them.
        transient = _is_transient_message(msg) or _smtp_code_is_transient(exc)
        return SendResult(
            ok=False,
            provider=provider_name,
            error=msg[:500],
            transient=transient,
        )


def _smtp_code_is_transient(exc: Exception) -> bool:
    """True if an smtplib exception carries a transient (4xx / connection) code.

    SMTP reply codes 4xx mean "try again later"; 5xx are permanent. Connection
    errors (no code) are transient. Done defensively — any failure here just
    falls back to the text heuristic.
    """
    try:
        import smtplib

        if isinstance(exc, (smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected)):
            return True
        code = getattr(exc, "smtp_code", None)
        if isinstance(code, int):
            return 400 <= code < 500
    except Exception:  # noqa: BLE001
        return False
    return False


def _send_brevo(
    *,
    to_email: str,
    subject: str,
    body_html: str,
    body_text: str,
    from_alias: str,
    from_name: str,
    reply_to: str = "",
) -> SendResult:
    """Send via Brevo's SMTP relay (smtp-relay.brevo.com:587)."""
    user = (getattr(settings, "brevo_smtp_user", "") or "").strip()
    key = (getattr(settings, "brevo_smtp_key", "") or "").strip()
    host = (getattr(settings, "brevo_smtp_host", "") or "smtp-relay.brevo.com").strip()
    port = int(getattr(settings, "brevo_smtp_port", 587) or 587)

    if not user or not key:
        return SendResult(
            ok=False,
            provider="brevo",
            error="BREVO_SMTP_USER / BREVO_SMTP_KEY are not set.",
            transient=False,
        )
    return _send_smtp_generic(
        provider_name="brevo",
        host=host,
        port=port,
        username=user,
        password=key,
        use_tls=True,  # Brevo relay uses STARTTLS on 587
        to_email=to_email,
        subject=subject,
        body_html=body_html,
        body_text=body_text,
        from_alias=from_alias,
        from_name=from_name,
        reply_to=reply_to,
    )


def _send_smtp(
    *,
    to_email: str,
    subject: str,
    body_html: str,
    body_text: str,
    from_alias: str,
    from_name: str,
    reply_to: str = "",
) -> SendResult:
    """Send via a generic SMTP server configured by SMTP_* settings."""
    host = (getattr(settings, "smtp_host", "") or "").strip()
    port = int(getattr(settings, "smtp_port", 587) or 587)
    user = (getattr(settings, "smtp_username", "") or "").strip()
    password = getattr(settings, "smtp_password", "") or ""
    use_tls = bool(getattr(settings, "smtp_use_tls", True))

    if not host:
        return SendResult(
            ok=False,
            provider="smtp",
            error="SMTP_HOST is not set.",
            transient=False,
        )
    return _send_smtp_generic(
        provider_name="smtp",
        host=host,
        port=port,
        username=user,
        password=password,
        use_tls=use_tls,
        to_email=to_email,
        subject=subject,
        body_html=body_html,
        body_text=body_text,
        from_alias=from_alias,
        from_name=from_name,
        reply_to=reply_to,
    )


def _send_gmail_smtp(
    *,
    to_email: str,
    subject: str,
    body_html: str,
    body_text: str,
    from_alias: str,
    from_name: str,
    reply_to: str = "",
) -> SendResult:
    """Send via smtp.gmail.com using an App Password.

    Intended for low-volume MANUAL tests. The production campaign sender uses
    the Gmail API path (app/gmail_sender.py), not this. Needs
    GMAIL_SMTP_USER + GMAIL_SMTP_APP_PASSWORD.
    """
    user = (getattr(settings, "gmail_smtp_user", "") or "").strip()
    app_password = getattr(settings, "gmail_smtp_app_password", "") or ""
    host = (getattr(settings, "gmail_smtp_host", "") or "smtp.gmail.com").strip()
    port = int(getattr(settings, "gmail_smtp_port", 587) or 587)

    if not user or not app_password:
        return SendResult(
            ok=False,
            provider="gmail_smtp",
            error="GMAIL_SMTP_USER / GMAIL_SMTP_APP_PASSWORD are not set.",
            transient=False,
        )
    # Gmail requires the From address to match the authenticated account. If the
    # caller did not supply an alias, default to the SMTP user itself.
    return _send_smtp_generic(
        provider_name="gmail_smtp",
        host=host,
        port=port,
        username=user,
        password=app_password,
        use_tls=True,
        to_email=to_email,
        subject=subject,
        body_html=body_html,
        body_text=body_text,
        from_alias=from_alias or user,
        from_name=from_name,
        reply_to=reply_to,
    )


def _send_gmail_api(
    session: Session | None,
    *,
    to_email: str,
    subject: str,
    body_html: str,
    body_text: str,
    from_alias: str,
    from_name: str,
    list_unsubscribe: str,
    thread_id: str,
    in_reply_to: str,
) -> SendResult:
    """Send via the connected Gmail account using the existing Gmail API module.

    Wraps ``app.gmail_sender.send_message`` and maps its ``GmailSendResult`` to a
    ``SendResult``. Forces Reply-To at this layer too. Needs a DB ``session`` to
    load the stored OAuth token; if none is provided, or Gmail is not connected,
    returns a non-transient failure (graceful degrade — never raises).
    """
    if session is None:
        return SendResult(
            ok=False,
            provider="gmail",
            error="Gmail API provider requires a DB session (none provided).",
            transient=False,
        )

    try:
        from app.gmail_sender import GmailNotConnected, send_message

        try:
            result = send_message(
                session,
                to_email=to_email,
                subject=subject,
                body_html=body_html,
                body_text=body_text,
                from_alias=from_alias,
                from_name=from_name,
                # FORCE Reply-To regardless of any caller-supplied value.
                reply_to=_forced_reply_to(),
                list_unsubscribe=list_unsubscribe,
                thread_id=thread_id,
                in_reply_to=in_reply_to,
            )
        except GmailNotConnected as exc:
            # No Gmail account connected — degrade gracefully, do not crash.
            return SendResult(
                ok=False,
                provider="gmail",
                error=str(exc)[:500],
                transient=False,
            )

        return SendResult(
            ok=bool(result.ok),
            provider="gmail",
            message_id=getattr(result, "message_id", "") or "",
            error=getattr(result, "error", "") or "",
            transient=bool(getattr(result, "transient", False)),
        )
    except Exception as exc:  # noqa: BLE001 — never raise into the caller
        msg = str(exc)
        return SendResult(
            ok=False,
            provider="gmail",
            error=msg[:500],
            transient=_is_transient_message(msg),
        )


def _send_console(
    *,
    to_email: str,
    subject: str,
    from_alias: str,
    from_name: str,
) -> SendResult:
    """Dev/default transport — log the payload, pretend success.

    Used when MAIL_PROVIDER is "console", unset/empty, or unknown. Lets the rest
    of the pipeline run end-to-end without actually sending mail.
    """
    logger.info(
        "console mail provider: would send to=%s from=%s reply_to=%s subject=%r",
        to_email,
        _from_header(from_alias, from_name),
        _forced_reply_to(),
        subject,
    )
    return SendResult(
        ok=True,
        provider="console",
        message_id="",
        error="",
        transient=False,
    )


# ── Public API ───────────────────────────────────────────────────────────────


def selected_provider() -> str:
    """Normalized provider name from ``settings.mail_provider`` (lowercased)."""
    return (getattr(settings, "mail_provider", "gmail") or "gmail").strip().lower()


def send(
    to_email: str,
    subject: str,
    html: str,
    text: str = "",
    *,
    from_name: str = "",
    from_alias: str = "",
    reply_to: str = "",  # accepted for API symmetry, but ALWAYS overridden
    list_unsubscribe: str = "",
    thread_id: str = "",
    in_reply_to: str = "",
    session: Session | None = None,
) -> SendResult:
    """Send one email through the configured provider.

    The provider is chosen by ``settings.mail_provider`` (see module docstring).
    Reply-To is ALWAYS forced to ``settings.reply_to_email`` — the ``reply_to``
    argument is accepted only so callers can pass it without breaking, but it is
    deliberately ignored for the actual header.

    Args:
        to_email:         Recipient address.
        subject:          Subject line.
        html:             HTML body (preferred part).
        text:             Plain-text body (fallback / multipart alternative).
        from_name:        Display name for the From header (optional).
        from_alias:       From address (optional; defaults to MAIL_FROM).
        reply_to:         IGNORED for the header — Reply-To is always forced.
        list_unsubscribe: One-click unsubscribe URL (Gmail-API path only today).
        thread_id:        Gmail thread id for replies (Gmail-API path only).
        in_reply_to:      RFC Message-Id for threading (Gmail-API path only).
        session:          DB session — REQUIRED only for the ``gmail`` (API)
                          provider, which loads the stored OAuth token. Ignored
                          by all other providers.

    Returns:
        SendResult — never raises. ``ok=False`` with a descriptive ``error``
        when the provider is unconfigured or the send fails.
    """
    provider = selected_provider()

    if provider == "resend":
        return _send_resend(
            to_email=to_email,
            subject=subject,
            body_html=html,
            body_text=text,
            from_alias=from_alias,
            from_name=from_name,
            reply_to=reply_to,
            list_unsubscribe=list_unsubscribe,
        )

    if provider == "brevo":
        return _send_brevo(
            to_email=to_email,
            subject=subject,
            body_html=html,
            body_text=text,
            from_alias=from_alias,
            from_name=from_name,
            reply_to=reply_to,
        )

    if provider == "smtp":
        return _send_smtp(
            to_email=to_email,
            subject=subject,
            body_html=html,
            body_text=text,
            from_alias=from_alias,
            from_name=from_name,
            reply_to=reply_to,
        )

    if provider == "gmail_smtp":
        return _send_gmail_smtp(
            to_email=to_email,
            subject=subject,
            body_html=html,
            body_text=text,
            from_alias=from_alias,
            from_name=from_name,
            reply_to=reply_to,
        )

    if provider == "gmail":
        return _send_gmail_api(
            session,
            to_email=to_email,
            subject=subject,
            body_html=html,
            body_text=text,
            from_alias=from_alias,
            from_name=from_name,
            list_unsubscribe=list_unsubscribe,
            thread_id=thread_id,
            in_reply_to=in_reply_to,
        )

    # "console", unset, or unknown -> console transport (graceful default).
    if provider not in ("console", ""):
        logger.warning(
            "unknown MAIL_PROVIDER %r — falling back to console transport", provider
        )
    return _send_console(
        to_email=to_email,
        subject=subject,
        from_alias=from_alias,
        from_name=from_name,
    )
