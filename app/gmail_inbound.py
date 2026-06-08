"""Two-way email — poll the connected Gmail inbox for replies and thread them
into the shared inbox.

Strategy: each cycle, list INBOX messages received since the last poll
(time-windowed, with a lookback on first run), fetch each, skip our own sends,
resolve the sender to a Contact (creating a lightweight one if unknown), and
append the message to the matching Conversation (by Gmail threadId).

Requires the `gmail.readonly` scope — if Gmail was connected before this scope
was added, the operator must reconnect Gmail once.
"""
from __future__ import annotations

import base64
import time
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr, parsedate_to_datetime
from threading import Lock, Thread

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import contacts as contacts_module
from app import inbox as inbox_module
from app.config import settings
from app.db import SessionLocal
from app.gmail_sender import GmailNotConnected, get_active_account, get_gmail_service
from app.models import Contact, ContactChannel, MessageAttachment
from app.utils import normalize_email

_scheduler_started = False
_scheduler_lock = Lock()


# ── Payload parsing ──────────────────────────────────────────────────────────


def _decode(data: str) -> str:
    try:
        return base64.urlsafe_b64decode(data.encode("utf-8")).decode("utf-8", "replace")
    except Exception:
        return ""


def _extract_bodies(payload: dict) -> tuple[str, str]:
    """Walk the MIME tree, return (text_plain, text_html)."""
    text, html = "", ""

    def walk(part: dict) -> None:
        nonlocal text, html
        mime = part.get("mimeType", "")
        body = part.get("body", {})
        data = body.get("data")
        if mime == "text/plain" and data and not text:
            text = _decode(data)
        elif mime == "text/html" and data and not html:
            html = _decode(data)
        for sub in part.get("parts", []) or []:
            walk(sub)

    walk(payload or {})
    return text, html


def _headers_map(payload: dict) -> dict[str, str]:
    return {h.get("name", "").lower(): h.get("value", "") for h in (payload.get("headers") or [])}


def _extract_attachments(payload: dict) -> list[dict]:
    """Walk the MIME tree for real attachments (filename + attachmentId)."""
    out: list[dict] = []

    def walk(part: dict) -> None:
        filename = part.get("filename") or ""
        body = part.get("body", {}) or {}
        att_id = body.get("attachmentId")
        if filename and att_id:
            out.append({
                "filename": filename,
                "mime_type": part.get("mimeType", "application/octet-stream"),
                "size": int(body.get("size") or 0),
                "attachment_id": att_id,
            })
        for sub in part.get("parts", []) or []:
            walk(sub)

    walk(payload or {})
    return out


# ── Contact resolution for inbound senders ──────────────────────────────────


def _resolve_or_create_contact(session: Session, email: str, display_name: str) -> Contact | None:
    norm = normalize_email(email)
    if not norm or "@" not in norm:
        return None
    ch = session.scalar(
        select(ContactChannel).where(
            ContactChannel.channel_type == "email", ContactChannel.value_normalized == norm
        )
    )
    if ch:
        return session.get(Contact, ch.contact_id)
    # Unknown sender — create a lightweight contact so nothing is lost.
    contact = Contact(
        display_name=display_name or norm, contact_person=display_name or "",
        primary_email=norm, source_summary="inbox", created_at=datetime.utcnow(),
    )
    session.add(contact)
    session.flush()
    contact.channels.append(ContactChannel(
        channel_type="email", value=email.strip(), value_normalized=norm, source="inbox",
    ))
    session.commit()
    return contact


# ── Poll cycle ───────────────────────────────────────────────────────────────


def poll_inbound(session: Session, *, max_messages: int = 50) -> dict:
    """Fetch + thread new inbound emails. Returns a stats dict."""
    if get_active_account(session) is None:
        return {"ok": False, "error": "gmail_not_connected"}

    try:
        service, account = get_gmail_service(session)
    except GmailNotConnected:
        return {"ok": False, "error": "gmail_not_connected"}

    # Time window: since last poll, else lookback_days on first run.
    since = account.last_poll_at or (datetime.utcnow() - timedelta(days=settings.gmail_inbound_lookback_days))
    after_epoch = int(since.replace(tzinfo=timezone.utc).timestamp())
    query = f"in:inbox after:{after_epoch}"

    self_addresses = {normalize_email(account.account_email), normalize_email(settings.gmail_send_as)}

    threaded = 0
    skipped = 0
    try:
        resp = service.users().messages().list(userId="me", q=query, maxResults=max_messages).execute()
        message_refs = resp.get("messages", []) or []
    except Exception as exc:  # noqa: BLE001
        account.last_error = f"inbound list: {str(exc)[:300]}"
        session.commit()
        return {"ok": False, "error": str(exc)[:300]}

    for ref in message_refs:
        mid = ref.get("id")
        try:
            msg = service.users().messages().get(userId="me", id=mid, format="full").execute()
        except Exception:
            continue
        payload = msg.get("payload", {})
        headers = _headers_map(payload)
        from_name, from_email = parseaddr(headers.get("from", ""))
        from_email_norm = normalize_email(from_email)
        if not from_email_norm or from_email_norm in self_addresses:
            skipped += 1
            continue  # our own send / no sender

        subject = headers.get("subject", "(no subject)")
        thread_id = msg.get("threadId", "")
        rfc_message_id = headers.get("message-id", mid)
        text, html = _extract_bodies(payload)
        if not text and msg.get("snippet"):
            text = msg["snippet"]
        try:
            occurred = parsedate_to_datetime(headers.get("date", "")).astimezone(timezone.utc).replace(tzinfo=None)
        except Exception:
            occurred = datetime.utcnow()

        contact = _resolve_or_create_contact(session, from_email, from_name)
        conv = inbox_module.get_or_create_email_conversation(
            session, contact=contact, contact_email=from_email_norm,
            subject=subject, external_thread_id=thread_id,
        )
        created = inbox_module.add_inbound_message(
            session, conv, from_addr=from_email_norm, to_addr=headers.get("to", ""),
            subject=subject, body_text=text, body_html=html,
            external_message_id=rfc_message_id, external_thread_id=thread_id,
            occurred_at=occurred,
        )
        if created:
            threaded += 1
            # Store attachment metadata (bytes fetched on-demand later).
            atts = _extract_attachments(payload)
            for a in atts:
                session.add(MessageAttachment(
                    message_id=created.id, filename=a["filename"], mime_type=a["mime_type"],
                    size_bytes=a["size"], gmail_message_id=mid, gmail_attachment_id=a["attachment_id"],
                ))
            if atts:
                session.commit()
            # If Gmail flagged it SPAM, route the conversation to the Spam view.
            if "SPAM" in (msg.get("labelIds") or []):
                conv.status = "spam"
                conv.unread = False
                session.commit()
        else:
            skipped += 1

    account.last_poll_at = datetime.utcnow()
    account.last_error = ""
    session.commit()
    return {"ok": True, "threaded": threaded, "skipped": skipped, "scanned": len(message_refs)}


# ── Background daemon ────────────────────────────────────────────────────────


def _loop() -> None:
    while True:
        try:
            session = SessionLocal()
            try:
                if get_active_account(session) is not None:
                    poll_inbound(session)
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001
            print(f"[gmail_inbound] loop error: {exc}")
        time.sleep(max(30, settings.gmail_inbound_interval))


def start_gmail_inbound_scheduler() -> None:
    global _scheduler_started
    if not settings.gmail_inbound_enabled:
        return
    with _scheduler_lock:
        if _scheduler_started:
            return
        _scheduler_started = True
    Thread(target=_loop, daemon=True, name="gmail-inbound").start()
    print("[gmail_inbound] scheduler started")
