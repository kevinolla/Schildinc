"""WhatsApp Business Cloud API (direct Meta) — send, receive, webhook.

Direct integration with Meta's Graph API (no BSP markup). Sends text inside
the 24-hour customer-service window and approved templates outside it. Inbound
messages + delivery/read statuses arrive via the webhook we register in Meta.

Setup (Meta → WhatsApp):
  1. Add a phone number → get WHATSAPP_PHONE_NUMBER_ID + a permanent access token.
  2. Configure the webhook callback URL = {APP_BASE_URL}/webhooks/whatsapp with
     WHATSAPP_VERIFY_TOKEN, and subscribe to the "messages" field.
  3. Set WHATSAPP_APP_SECRET (App → Settings → Basic) so we can verify signatures.

This module is pure-Python (urllib) — no extra dependency. The send + webhook
helpers are split so they can be unit-tested without network access.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.request import Request, urlopen

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import contacts as contacts_module
from app import inbox as inbox_module
from app.config import settings
from app.models import Contact, ContactChannel, Conversation, Message
from app.utils import normalize_email  # noqa: F401  (kept for parity of imports)


@dataclass
class WhatsAppResult:
    ok: bool
    message_id: str = ""
    error: str = ""


# ── Config / status ──────────────────────────────────────────────────────────


def is_configured() -> bool:
    return bool(settings.whatsapp_phone_number_id and settings.whatsapp_access_token)


def status() -> dict:
    return {
        "configured": is_configured(),
        "phone_number_id": settings.whatsapp_phone_number_id,
        "has_app_secret": bool(settings.whatsapp_app_secret),
        "verify_token_set": bool(settings.whatsapp_verify_token),
        "webhook_url": f"{settings.app_base_url}/webhooks/whatsapp",
        "api_version": settings.whatsapp_api_version,
    }


# ── Phone normalization (WhatsApp wants digits, no +) ───────────────────────


def to_wa_number(phone: str | None) -> str:
    """Meta expects the number in international format, digits only (no +)."""
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
    return digits


# ── Webhook verification (GET handshake) ────────────────────────────────────


def verify_webhook(mode: str, token: str, challenge: str) -> str | None:
    """Return the challenge string if the verify handshake is valid, else None."""
    if mode == "subscribe" and token and token == settings.whatsapp_verify_token:
        return challenge
    return None


def verify_signature(raw_body: bytes, signature_header: str) -> bool:
    """Validate Meta's X-Hub-Signature-256 header against the app secret.

    If no app secret is configured we cannot verify — return False so the caller
    can decide (we accept-but-log in that case to ease initial setup).
    """
    secret = settings.whatsapp_app_secret
    if not secret or not signature_header:
        return False
    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    try:
        return hmac.compare_digest(expected, signature_header.strip())
    except Exception:
        return False


# ── Payload builders (pure) ─────────────────────────────────────────────────


def build_text_payload(to_phone: str, text: str) -> dict:
    return {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_wa_number(to_phone),
        "type": "text",
        "text": {"preview_url": True, "body": text},
    }


def build_template_payload(to_phone: str, template_name: str, language: str = "", body_params: list[str] | None = None) -> dict:
    components = []
    if body_params:
        components.append({
            "type": "body",
            "parameters": [{"type": "text", "text": p} for p in body_params],
        })
    template: dict = {
        "name": template_name,
        "language": {"code": language or settings.whatsapp_default_lang},
    }
    if components:
        template["components"] = components
    return {
        "messaging_product": "whatsapp",
        "to": to_wa_number(to_phone),
        "type": "template",
        "template": template,
    }


# ── Send (network) ───────────────────────────────────────────────────────────


def _graph_post(payload: dict) -> WhatsAppResult:
    if not is_configured():
        return WhatsAppResult(ok=False, error="whatsapp_not_configured")
    url = (
        f"https://graph.facebook.com/{settings.whatsapp_api_version}"
        f"/{settings.whatsapp_phone_number_id}/messages"
    )
    data = json.dumps(payload).encode("utf-8")
    req = Request(url, data=data, method="POST", headers={
        "Authorization": f"Bearer {settings.whatsapp_access_token}",
        "Content-Type": "application/json",
    })
    try:
        with urlopen(req, timeout=20) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        mid = ""
        msgs = body.get("messages") or []
        if msgs:
            mid = msgs[0].get("id", "")
        return WhatsAppResult(ok=True, message_id=mid)
    except Exception as exc:  # noqa: BLE001
        detail = str(exc)
        try:
            detail = exc.read().decode("utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass
        return WhatsAppResult(ok=False, error=detail[:500])


def send_text(to_phone: str, text: str) -> WhatsAppResult:
    return _graph_post(build_text_payload(to_phone, text))


def send_template(to_phone: str, template_name: str, language: str = "", body_params: list[str] | None = None) -> WhatsAppResult:
    return _graph_post(build_template_payload(to_phone, template_name, language, body_params))


# ── 24-hour service window ───────────────────────────────────────────────────


def within_service_window(session: Session, conv: Conversation) -> bool:
    """Free-form WhatsApp text is only allowed within 24h of the last inbound
    customer message. Outside that, an approved template is required (Meta rule).
    """
    last_in = session.scalar(
        select(Message).where(
            Message.conversation_id == conv.id, Message.direction == "in"
        ).order_by(Message.occurred_at.desc())
    )
    if not last_in or not last_in.occurred_at:
        return False
    return (datetime.utcnow() - last_in.occurred_at) < timedelta(hours=24)


# ── Inbound contact resolution (by phone) ───────────────────────────────────


def _resolve_or_create_contact(session: Session, wa_id: str, name: str) -> Contact:
    norm = to_wa_number(wa_id)
    candidates = ["+" + norm, norm]
    ch = session.scalar(
        select(ContactChannel).where(
            ContactChannel.channel_type.in_(["whatsapp", "phone"]),
            ContactChannel.value_normalized.in_(candidates),
        )
    )
    if ch:
        return session.get(Contact, ch.contact_id)
    contact = Contact(
        display_name=name or ("+" + norm), contact_person=name or "",
        primary_phone="+" + norm, source_summary="whatsapp", created_at=datetime.utcnow(),
    )
    session.add(contact)
    session.flush()
    contact.channels.append(ContactChannel(
        channel_type="whatsapp", value="+" + norm, value_normalized="+" + norm, source="whatsapp",
    ))
    session.commit()
    return contact


# ── Webhook processing (POST body) ──────────────────────────────────────────


def process_webhook(session: Session, payload: dict) -> dict:
    """Handle an inbound WhatsApp webhook payload: thread new messages into the
    inbox and apply delivery/read status updates. Returns a small stats dict.
    """
    threaded = 0
    statuses = 0
    for entry in payload.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            value = change.get("value", {}) or {}
            # Map wa_id -> profile name from the contacts block.
            names: dict[str, str] = {}
            for c in value.get("contacts", []) or []:
                names[c.get("wa_id", "")] = (c.get("profile", {}) or {}).get("name", "")

            for m in value.get("messages", []) or []:
                wa_from = m.get("from", "")
                msg_id = m.get("id", "")
                msg_type = m.get("type", "")
                if msg_type == "text":
                    text = (m.get("text", {}) or {}).get("body", "")
                else:
                    # Non-text (image/audio/doc/etc.) — store a placeholder.
                    text = f"[{msg_type} message]"
                ts = m.get("timestamp")
                try:
                    occurred = datetime.utcfromtimestamp(int(ts)) if ts else datetime.utcnow()
                except Exception:
                    occurred = datetime.utcnow()

                contact = _resolve_or_create_contact(session, wa_from, names.get(wa_from, ""))
                conv = inbox_module.get_or_create_whatsapp_conversation(
                    session, contact=contact, phone="+" + to_wa_number(wa_from),
                    external_thread_id=to_wa_number(wa_from),
                )
                created = inbox_module.add_inbound_message(
                    session, conv, from_addr="+" + to_wa_number(wa_from), to_addr=settings.whatsapp_phone_number_id,
                    subject="WhatsApp", body_text=text, body_html="",
                    external_message_id=msg_id, external_thread_id=to_wa_number(wa_from),
                    occurred_at=occurred, channel="whatsapp",
                )
                if created:
                    threaded += 1

            for st in value.get("statuses", []) or []:
                statuses += 1
                mid = st.get("id", "")
                state = st.get("status", "")  # sent|delivered|read|failed
                if mid and state:
                    msg = session.scalar(select(Message).where(Message.external_message_id == mid))
                    if msg:
                        msg.status = state
                        if state == "failed":
                            errs = st.get("errors") or []
                            if errs:
                                msg.error = json.dumps(errs)[:500]
            session.commit()
    return {"threaded": threaded, "statuses": statuses}
