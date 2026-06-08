"""Instagram Messaging (official Meta Graph API) — inbound DMs into the inbox.

Compliant scope: receive Instagram DMs via webhook and REPLY within the 24-hour
customer-service window. Meta does NOT allow cold/proactive DMs through the API,
so this module is inbound + in-window reply only (cold outreach stays on email).

Setup (Meta): IG Business/Creator account linked to a Facebook Page → app with
instagram_manage_messages → subscribe the Page to the `messages` webhook field →
callback {APP_BASE_URL}/webhooks/instagram with the verify token.

Reuses the shared conversations/messages tables (channel='instagram'), keyed by
the sender's Instagram-scoped ID (IGSID). Pure-Python (urllib), testable helpers.
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

from app import inbox as inbox_module
from app.config import settings
from app.models import Contact, ContactChannel, Conversation, Message


@dataclass
class IGResult:
    ok: bool
    message_id: str = ""
    error: str = ""


def is_configured() -> bool:
    return bool(settings.instagram_account_id and settings.instagram_access_token)


def status() -> dict:
    return {
        "configured": is_configured(),
        "account_id": settings.instagram_account_id,
        "has_app_secret": bool(settings.instagram_app_secret),
        "verify_token_set": bool(settings.instagram_verify_token),
        "webhook_url": f"{settings.app_base_url}/webhooks/instagram",
        "api_version": settings.instagram_api_version,
    }


# ── Webhook verification + signature ────────────────────────────────────────


def verify_webhook(mode: str, token: str, challenge: str) -> str | None:
    if mode == "subscribe" and token and token == settings.instagram_verify_token:
        return challenge
    return None


def verify_signature(raw_body: bytes, signature_header: str) -> bool:
    secret = settings.instagram_app_secret
    if not secret or not signature_header:
        return False
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    try:
        return hmac.compare_digest(expected, signature_header.strip())
    except Exception:
        return False


# ── Send (within 24h window) ─────────────────────────────────────────────────


def build_text_payload(igsid: str, text: str) -> dict:
    return {"recipient": {"id": igsid}, "message": {"text": text}}


def send_text(igsid: str, text: str) -> IGResult:
    if not is_configured():
        return IGResult(ok=False, error="instagram_not_configured")
    url = (f"https://graph.facebook.com/{settings.instagram_api_version}"
           f"/{settings.instagram_account_id}/messages")
    data = json.dumps(build_text_payload(igsid, text)).encode("utf-8")
    req = Request(url, data=data, method="POST", headers={
        "Authorization": f"Bearer {settings.instagram_access_token}",
        "Content-Type": "application/json",
    })
    try:
        with urlopen(req, timeout=20) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        return IGResult(ok=True, message_id=body.get("message_id", ""))
    except Exception as exc:  # noqa: BLE001
        detail = str(exc)
        try:
            detail = exc.read().decode("utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass
        return IGResult(ok=False, error=detail[:500])


def within_service_window(session: Session, conv: Conversation) -> bool:
    last_in = session.scalar(
        select(Message).where(Message.conversation_id == conv.id, Message.direction == "in")
        .order_by(Message.occurred_at.desc())
    )
    if not last_in or not last_in.occurred_at:
        return False
    return (datetime.utcnow() - last_in.occurred_at) < timedelta(hours=24)


# ── Inbound contact resolution (by IGSID) ───────────────────────────────────


def _resolve_or_create_contact(session: Session, igsid: str, username: str) -> Contact:
    ch = session.scalar(
        select(ContactChannel).where(
            ContactChannel.channel_type == "instagram_id",
            ContactChannel.value_normalized == igsid,
        )
    )
    if ch:
        return session.get(Contact, ch.contact_id)
    display = ("@" + username) if username else f"Instagram {igsid[:8]}"
    contact = Contact(
        display_name=display, contact_person=username or "", source_summary="instagram",
        created_at=datetime.utcnow(),
    )
    session.add(contact)
    session.flush()
    contact.channels.append(ContactChannel(
        channel_type="instagram_id", value=igsid, value_normalized=igsid, source="instagram",
    ))
    if username:
        contact.channels.append(ContactChannel(
            channel_type="instagram", value=f"https://instagram.com/{username}",
            value_normalized=f"https://instagram.com/{username}", source="instagram",
        ))
    session.commit()
    return contact


# ── Webhook processing ───────────────────────────────────────────────────────


def process_webhook(session: Session, payload: dict) -> dict:
    """Thread inbound IG DMs into the inbox. Echoes of our own sends are ignored."""
    threaded = 0
    for entry in payload.get("entry", []) or []:
        for ev in entry.get("messaging", []) or []:
            msg = ev.get("message", {}) or {}
            if msg.get("is_echo"):
                continue  # our own outbound, already logged
            sender = (ev.get("sender", {}) or {}).get("id", "")
            if not sender or sender == settings.instagram_account_id:
                continue
            text = msg.get("text", "")
            if not text:
                atts = msg.get("attachments") or []
                text = f"[{atts[0].get('type','media')} message]" if atts else "[non-text message]"
            mid = msg.get("mid", "")
            ts = ev.get("timestamp")
            try:
                occurred = datetime.utcfromtimestamp(int(ts) / 1000) if ts else datetime.utcnow()
            except Exception:
                occurred = datetime.utcnow()
            username = (ev.get("sender", {}) or {}).get("username", "")

            contact = _resolve_or_create_contact(session, sender, username)
            conv = inbox_module.get_or_create_instagram_conversation(
                session, contact=contact, igsid=sender,
            )
            created = inbox_module.add_inbound_message(
                session, conv, from_addr=sender, to_addr=settings.instagram_account_id,
                subject="Instagram", body_text=text, body_html="",
                external_message_id=mid, external_thread_id=sender,
                occurred_at=occurred, channel="instagram",
            )
            if created:
                threaded += 1
        session.commit()
    return {"threaded": threaded}
