"""Gmail OAuth + send module.

The connected Gmail account's refresh token lives in Postgres (the
`gmail_accounts` table) because Railway's container filesystem is
ephemeral. We use the Gmail API `users.messages.send` endpoint, which is
free and works fine on a consumer Gmail account (hard limit ~500
recipients/day; Workspace ~2000).

Sending FROM an alias (e.g. sales@schildinc.com) requires that alias to be
a verified "Send mail as" address on the authorized account
(Gmail → Settings → Accounts and Import → "Send mail as"). A plain
forwarding-only address is NOT enough — Gmail rejects the From header.

OAuth flow (driven from app/main.py):
    1. /emails/gmail/connect   → build_authorization_url() → Google consent
    2. /emails/gmail/callback  → exchange_code() → store creds in DB
    3. campaigns               → send_message() pulls creds from DB
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import GmailAccount

# Gmail send + the readonly settings scope (to enumerate send-as aliases) +
# basic profile (to learn which address authorized).
GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",  # two-way email: read inbound replies
    "https://www.googleapis.com/auth/gmail.settings.basic",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
]


class GmailNotConnected(Exception):
    """Raised when no active Gmail account is stored."""


class GmailConfigError(Exception):
    """Raised when OAuth client id/secret are missing."""


@dataclass
class GmailSendResult:
    ok: bool
    message_id: str = ""
    error: str = ""
    transient: bool = False


def _redirect_uri() -> str:
    return f"{settings.app_base_url}/emails/gmail/callback"


def _client_config() -> dict:
    if not settings.gmail_client_id or not settings.gmail_client_secret:
        raise GmailConfigError(
            "GMAIL_CLIENT_ID / GMAIL_CLIENT_SECRET are not set. Create an "
            "OAuth 2.0 Web application client in Google Cloud Console."
        )
    return {
        "web": {
            "client_id": settings.gmail_client_id,
            "client_secret": settings.gmail_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [_redirect_uri()],
        }
    }


# ── OAuth flow ─────────────────────────────────────────────────────────────


def build_authorization_url(state: str) -> str:
    """Return the Google consent URL to redirect the operator to."""
    from google_auth_oauthlib.flow import Flow

    flow = Flow.from_client_config(_client_config(), scopes=GMAIL_SCOPES)
    flow.redirect_uri = _redirect_uri()
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",  # force refresh_token every time
        state=state,
    )
    return auth_url


def exchange_code(session: Session, code: str) -> GmailAccount:
    """Exchange an authorization code for tokens and persist them."""
    from google_auth_oauthlib.flow import Flow

    flow = Flow.from_client_config(_client_config(), scopes=GMAIL_SCOPES)
    flow.redirect_uri = _redirect_uri()
    flow.fetch_token(code=code)
    creds = flow.credentials

    account_email = _fetch_account_email(creds)
    aliases = _fetch_send_as_aliases(creds)

    account = _get_account(session)
    if account is None:
        account = GmailAccount()
        session.add(account)
    account.account_email = account_email
    account.token_json = creds.to_json()
    account.scopes = " ".join(creds.scopes or GMAIL_SCOPES)
    account.send_as_aliases = json.dumps(aliases)
    account.is_active = True
    account.connected_at = datetime.utcnow()
    account.last_error = ""
    session.commit()
    session.refresh(account)
    return account


def _get_account(session: Session) -> GmailAccount | None:
    return session.scalar(select(GmailAccount).order_by(GmailAccount.id.asc()))


def get_active_account(session: Session) -> GmailAccount | None:
    account = _get_account(session)
    if account and account.is_active and account.token_json:
        return account
    return None


def disconnect(session: Session) -> None:
    account = _get_account(session)
    if account:
        account.is_active = False
        account.token_json = ""
        session.commit()


def _load_credentials(account: GmailAccount):
    from google.oauth2.credentials import Credentials

    data = json.loads(account.token_json)
    creds = Credentials.from_authorized_user_info(data, scopes=GMAIL_SCOPES)
    return creds


def _refresh_if_needed(session: Session, account: GmailAccount, creds) -> None:
    from google.auth.transport.requests import Request

    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        account.token_json = creds.to_json()
        account.updated_at = datetime.utcnow()
        session.commit()


def _fetch_account_email(creds) -> str:
    try:
        from googleapiclient.discovery import build

        service = build("oauth2", "v2", credentials=creds, cache_discovery=False)
        info = service.userinfo().get().execute()
        return info.get("email", "")
    except Exception:
        return ""


def _fetch_send_as_aliases(creds) -> list[str]:
    """List verified send-as addresses for the authorized account."""
    try:
        from googleapiclient.discovery import build

        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        resp = service.users().settings().sendAs().list(userId="me").execute()
        aliases = []
        for item in resp.get("sendAs", []):
            email = item.get("sendAsEmail", "")
            verified = item.get("isPrimary") or item.get("verificationStatus") == "accepted"
            if email and verified:
                aliases.append(email)
        return aliases
    except Exception:
        return []


# ── Sending ──────────────────────────────────────────────────────────────


def _build_raw_message(
    *,
    from_header: str,
    to_email: str,
    subject: str,
    body_text: str,
    body_html: str,
    reply_to: str,
    list_unsubscribe: str = "",
    in_reply_to: str = "",
) -> str:
    message = MIMEMultipart("alternative")
    message["From"] = from_header
    message["To"] = to_email
    message["Subject"] = subject
    if reply_to:
        message["Reply-To"] = reply_to
    if in_reply_to:
        # Proper RFC threading so the reply lands in the same Gmail thread.
        message["In-Reply-To"] = in_reply_to
        message["References"] = in_reply_to
    if list_unsubscribe:
        # RFC 8058 one-click unsubscribe — improves deliverability + compliance.
        message["List-Unsubscribe"] = f"<{list_unsubscribe}>"
        message["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    message.attach(MIMEText(body_text or " ", "plain", "utf-8"))
    message.attach(MIMEText(body_html or body_text or " ", "html", "utf-8"))
    return base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")


def get_gmail_service(session: Session):
    """Return an authorized Gmail API service (refreshing the token as needed).

    Used by the inbound poller (app/gmail_inbound.py). Raises GmailNotConnected
    if no active account.
    """
    from googleapiclient.discovery import build

    account = get_active_account(session)
    if account is None:
        raise GmailNotConnected("No Gmail account connected.")
    creds = _load_credentials(account)
    _refresh_if_needed(session, account, creds)
    return build("gmail", "v1", credentials=creds, cache_discovery=False), account


def send_message(
    session: Session,
    *,
    to_email: str,
    subject: str,
    body_html: str,
    body_text: str = "",
    from_alias: str = "",
    from_name: str = "",
    reply_to: str = "",
    list_unsubscribe: str = "",
    thread_id: str = "",
    in_reply_to: str = "",
) -> GmailSendResult:
    """Send one email via the connected Gmail account. Refreshes token as needed.

    Pass thread_id (+ in_reply_to message-id) to thread a reply into an
    existing Gmail conversation.
    """
    account = get_active_account(session)
    if account is None:
        raise GmailNotConnected("No Gmail account connected. Visit /emails to connect.")

    try:
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError

        creds = _load_credentials(account)
        _refresh_if_needed(session, account, creds)
        service = build("gmail", "v1", credentials=creds, cache_discovery=False)

        alias = from_alias or settings.gmail_send_as or account.account_email
        name = from_name or settings.gmail_sender_name
        from_header = f"{name} <{alias}>" if name else alias

        raw = _build_raw_message(
            from_header=from_header,
            to_email=to_email,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            reply_to=reply_to or alias,
            list_unsubscribe=list_unsubscribe,
            in_reply_to=in_reply_to,
        )
        send_body = {"raw": raw}
        if thread_id:
            send_body["threadId"] = thread_id
        try:
            sent = service.users().messages().send(userId="me", body=send_body).execute()
        except HttpError as exc:
            status = getattr(exc.resp, "status", 0)
            transient = status in (429, 500, 503)
            account.last_error = str(exc)[:500]
            session.commit()
            return GmailSendResult(ok=False, error=str(exc)[:500], transient=transient)

        return GmailSendResult(ok=True, message_id=sent.get("id", ""))
    except GmailNotConnected:
        raise
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        transient = any(t in msg.lower() for t in ["timeout", "temporar", "connection"])
        account.last_error = msg[:500]
        session.commit()
        return GmailSendResult(ok=False, error=msg[:500], transient=transient)


def connection_status(session: Session) -> dict:
    """Summary dict for the UI."""
    account = _get_account(session)
    configured = bool(settings.gmail_client_id and settings.gmail_client_secret)
    if account is None or not account.is_active or not account.token_json:
        return {
            "connected": False,
            "configured": configured,
            "redirect_uri": _redirect_uri(),
            "account_email": "",
            "send_as_aliases": [],
            "default_send_as": settings.gmail_send_as,
            "last_error": account.last_error if account else "",
        }
    try:
        aliases = json.loads(account.send_as_aliases or "[]")
    except Exception:
        aliases = []
    return {
        "connected": True,
        "configured": configured,
        "redirect_uri": _redirect_uri(),
        "account_email": account.account_email,
        "send_as_aliases": aliases,
        "default_send_as": settings.gmail_send_as,
        "connected_at": account.connected_at,
        "last_error": account.last_error,
    }
