"""Shared inbox logic — conversations, messages, assignment, canned replies.

A Conversation is a thread with one Contact on one channel. Inbound messages
(email replies, later WhatsApp) and outbound replies + internal notes are all
Messages. Mirrors the Trengo mental model: assign, note, canned-reply, label,
snooze, resolve.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import contacts as contacts_module
from app.models import (
    Agent,
    CannedReply,
    Contact,
    Conversation,
    Message,
)
from app.utils import normalize_email

CANNED_SEED_VERSION = 1

STARTER_CANNED = [
    {"title": "Thanks — sending samples", "category": "sales",
     "body": "Hi,\n\nThanks for getting back to us! I'll put together a few label samples with your logo and send them over shortly.\n\nBest regards,\nSchild Inc"},
    {"title": "Pricing follow-up", "category": "sales",
     "body": "Hi,\n\nHappy to help with pricing. Could you let me know your expected volume so I can send an accurate quote?\n\nBest regards,\nSchild Inc"},
    {"title": "Not interested — close politely", "category": "general",
     "body": "Hi,\n\nThanks for letting us know — no problem at all. If anything changes, we're here. Wishing you a great season!\n\nBest regards,\nSchild Inc"},
    {"title": "Schedule a call", "category": "sales",
     "body": "Hi,\n\nWould a short call work to go through the options? Let me know a time that suits you and I'll set it up.\n\nBest regards,\nSchild Inc"},
]


# ── Seeding ──────────────────────────────────────────────────────────────────


def seed_inbox_defaults(session: Session, admin_email: str = "", admin_name: str = "Schild Inc") -> None:
    """Ensure a default agent and starter canned replies exist (idempotent)."""
    if session.scalar(select(func.count(Agent.id))) == 0:
        session.add(Agent(
            name=admin_name or "Schild Inc",
            email=normalize_email(admin_email) or "sales@schildinc.com",
            role="admin", is_active=True,
        ))
    for spec in STARTER_CANNED:
        existing = session.scalar(
            select(CannedReply).where(CannedReply.title == spec["title"], CannedReply.is_starter.is_(True))
        )
        if existing is None:
            session.add(CannedReply(
                title=spec["title"], category=spec["category"], body=spec["body"],
                is_active=True, is_starter=True, seed_version=CANNED_SEED_VERSION,
            ))
    session.commit()


# ── Conversation helpers ─────────────────────────────────────────────────────


def get_or_create_email_conversation(
    session: Session, *, contact: Contact | None, contact_email: str,
    subject: str, external_thread_id: str = "",
) -> Conversation:
    """Find an existing email conversation by Gmail threadId (or contact+channel),
    else create one.
    """
    conv = None
    if external_thread_id:
        conv = session.scalar(
            select(Conversation).where(
                Conversation.channel == "email",
                Conversation.external_thread_id == external_thread_id,
            )
        )
    if conv is None and contact is not None:
        conv = session.scalar(
            select(Conversation).where(
                Conversation.channel == "email",
                Conversation.contact_id == contact.id,
                Conversation.external_thread_id == (external_thread_id or ""),
            )
        )
    if conv is None:
        conv = Conversation(
            contact_id=contact.id if contact else None,
            channel="email",
            subject=subject or "(no subject)",
            status="open",
            external_thread_id=external_thread_id,
            contact_email=normalize_email(contact_email),
            created_at=datetime.utcnow(),
        )
        session.add(conv)
        session.flush()
    return conv


def get_or_create_whatsapp_conversation(
    session: Session, *, contact: Contact | None, phone: str, external_thread_id: str,
) -> Conversation:
    """Find an existing WhatsApp conversation by wa_id (external_thread_id), else create."""
    conv = session.scalar(
        select(Conversation).where(
            Conversation.channel == "whatsapp",
            Conversation.external_thread_id == external_thread_id,
        )
    )
    if conv is None and contact is not None:
        conv = session.scalar(
            select(Conversation).where(
                Conversation.channel == "whatsapp",
                Conversation.contact_id == contact.id,
            )
        )
    if conv is None:
        conv = Conversation(
            contact_id=contact.id if contact else None,
            channel="whatsapp",
            subject="WhatsApp",
            status="open",
            external_thread_id=external_thread_id,
            contact_phone=phone,
            created_at=datetime.utcnow(),
        )
        session.add(conv)
        session.flush()
    return conv


def _touch(conv: Conversation, *, preview: str, direction: str, when: datetime, unread: bool) -> None:
    conv.last_message_at = when
    conv.last_message_preview = (preview or "")[:200]
    conv.last_direction = direction
    if unread:
        conv.unread = True
    conv.updated_at = datetime.utcnow()


def add_inbound_message(
    session: Session, conv: Conversation, *, from_addr: str, to_addr: str,
    subject: str, body_text: str, body_html: str, external_message_id: str,
    external_thread_id: str, occurred_at: datetime | None = None, channel: str = "email",
) -> Message | None:
    """Append an inbound message; dedupes on external_message_id."""
    if external_message_id and session.scalar(
        select(Message.id).where(Message.external_message_id == external_message_id)
    ):
        return None
    when = occurred_at or datetime.utcnow()
    msg = Message(
        conversation_id=conv.id, contact_id=conv.contact_id, direction="in",
        channel=channel, from_addr=from_addr, to_addr=to_addr, subject=subject,
        body_text=body_text, body_html=body_html, external_message_id=external_message_id,
        external_thread_id=external_thread_id, status="received", occurred_at=when,
    )
    session.add(msg)
    if conv.status == "closed":
        conv.status = "open"  # reopen on new customer reply
    _touch(conv, preview=body_text or subject, direction="in", when=when, unread=True)
    if conv.contact_id:
        act_type = "wa_in" if channel == "whatsapp" else "email_reply"
        label = "WhatsApp received" if channel == "whatsapp" else f"Email reply: {subject}"
        contacts_module.log_activity(
            session, conv.contact_id, act_type, channel=channel, direction="in",
            title=label[:200], body=(body_text or "")[:1000],
            ref_type="message", ref_id=msg.id, occurred_at=when, commit=False,
        )
    session.commit()
    return msg


def add_outbound_message(
    session: Session, conv: Conversation, *, agent: Agent | None, from_addr: str,
    to_addr: str, subject: str, body_text: str, body_html: str,
    external_message_id: str = "", external_thread_id: str = "",
    status: str = "sent", error: str = "", channel: str = "email",
) -> Message:
    when = datetime.utcnow()
    msg = Message(
        conversation_id=conv.id, contact_id=conv.contact_id, direction="out",
        channel=channel, from_addr=from_addr, to_addr=to_addr, subject=subject,
        body_text=body_text, body_html=body_html,
        agent_id=agent.id if agent else None, agent_name=agent.name if agent else "",
        external_message_id=external_message_id, external_thread_id=external_thread_id,
        status=status, error=error, occurred_at=when,
    )
    session.add(msg)
    conv.unread = False
    if status == "sent" and conv.status == "open":
        conv.status = "pending"  # waiting on customer
    _touch(conv, preview=body_text or subject, direction="out", when=when, unread=False)
    if conv.contact_id and status == "sent":
        act_type = "wa_out" if channel == "whatsapp" else "email_sent"
        label = "WhatsApp sent" if channel == "whatsapp" else f"Email reply sent: {subject}"
        contacts_module.log_activity(
            session, conv.contact_id, act_type, channel=channel, direction="out",
            title=label[:200], body=(body_text or "")[:1000],
            ref_type="message", ref_id=msg.id, occurred_at=when, commit=False,
        )
    session.commit()
    return msg


def add_internal_note(session: Session, conv: Conversation, *, agent: Agent | None, body: str) -> Message:
    when = datetime.utcnow()
    msg = Message(
        conversation_id=conv.id, contact_id=conv.contact_id, direction="out",
        channel="note", is_internal_note=True, body_text=body,
        agent_id=agent.id if agent else None, agent_name=agent.name if agent else "",
        status="note", occurred_at=when,
    )
    session.add(msg)
    conv.updated_at = when
    session.commit()
    return msg


# ── Mutations ────────────────────────────────────────────────────────────────


def assign(session: Session, conv: Conversation, agent_id: int | None) -> None:
    conv.assignee_agent_id = agent_id
    conv.updated_at = datetime.utcnow()
    session.commit()


def set_status(session: Session, conv: Conversation, status: str) -> None:
    if status in ("open", "pending", "snoozed", "closed"):
        conv.status = status
        if status != "open":
            conv.unread = False
        conv.updated_at = datetime.utcnow()
        session.commit()


def set_labels(session: Session, conv: Conversation, labels_csv: str) -> None:
    cleaned = ",".join(sorted({l.strip() for l in labels_csv.split(",") if l.strip()}))
    conv.labels = cleaned
    conv.updated_at = datetime.utcnow()
    session.commit()


def mark_read(session: Session, conv: Conversation) -> None:
    if conv.unread:
        conv.unread = False
        session.commit()


# ── Queries ──────────────────────────────────────────────────────────────────


def list_conversations(
    session: Session, *, status: str = "", channel: str = "", assignee_id: int | None = None,
    unassigned: bool = False, search: str = "", limit: int = 100, offset: int = 0,
) -> list[Conversation]:
    q = select(Conversation)
    if status:
        q = q.where(Conversation.status == status)
    if channel:
        q = q.where(Conversation.channel == channel)
    if unassigned:
        q = q.where(Conversation.assignee_agent_id.is_(None))
    elif assignee_id is not None:
        q = q.where(Conversation.assignee_agent_id == assignee_id)
    if search:
        like = f"%{search.lower()}%"
        q = q.where(
            func.lower(Conversation.subject).like(like)
            | func.lower(Conversation.contact_email).like(like)
            | func.lower(Conversation.last_message_preview).like(like)
        )
    q = q.order_by(Conversation.last_message_at.desc().nullslast(), Conversation.id.desc())
    return session.scalars(q.offset(offset).limit(limit)).all()


def status_counts(session: Session) -> dict:
    rows = session.execute(
        select(Conversation.status, func.count(Conversation.id)).group_by(Conversation.status)
    ).all()
    counts = {s: c for s, c in rows}
    counts["unread"] = session.scalar(
        select(func.count(Conversation.id)).where(Conversation.unread.is_(True))
    ) or 0
    counts["all"] = session.scalar(select(func.count(Conversation.id))) or 0
    return counts
