"""Reporting aggregations for the CRM dashboard (Phase 6).

Read-only rollups across the email engine, shared inbox, and contact hub.
All windowed by a `days` lookback. Kept dependency-free and Postgres/SQLite-safe
(no DB-specific date functions — windowing uses simple >= comparisons).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Agent,
    Contact,
    Conversation,
    EmailCampaign,
    EmailCampaignRecipient,
    Message,
)


def _pct(part: int, whole: int) -> float:
    return round((part / whole * 100), 1) if whole else 0.0


def build_report(session: Session, days: int = 30) -> dict:
    since = datetime.utcnow() - timedelta(days=days)

    # ── Email ────────────────────────────────────────────────────────────
    sent = session.scalar(
        select(func.count(EmailCampaignRecipient.id)).where(
            EmailCampaignRecipient.status == "sent", EmailCampaignRecipient.sent_at >= since
        )
    ) or 0
    opened = session.scalar(
        select(func.count(EmailCampaignRecipient.id)).where(
            EmailCampaignRecipient.sent_at >= since, EmailCampaignRecipient.first_opened_at.is_not(None)
        )
    ) or 0
    clicked = session.scalar(
        select(func.count(EmailCampaignRecipient.id)).where(
            EmailCampaignRecipient.sent_at >= since, EmailCampaignRecipient.first_clicked_at.is_not(None)
        )
    ) or 0
    unsubbed = session.scalar(
        select(func.count(EmailCampaignRecipient.id)).where(
            EmailCampaignRecipient.sent_at >= since, EmailCampaignRecipient.unsubscribed_at.is_not(None)
        )
    ) or 0
    campaigns_total = session.scalar(select(func.count(EmailCampaign.id))) or 0

    email = {
        "campaigns_total": campaigns_total,
        "sent": sent, "opened": opened, "clicked": clicked, "unsubscribed": unsubbed,
        "open_rate": _pct(opened, sent), "click_rate": _pct(clicked, sent),
        "unsub_rate": _pct(unsubbed, sent),
    }

    # ── Inbox ────────────────────────────────────────────────────────────
    status_rows = session.execute(
        select(Conversation.status, func.count(Conversation.id)).group_by(Conversation.status)
    ).all()
    channel_rows = session.execute(
        select(Conversation.channel, func.count(Conversation.id)).group_by(Conversation.channel)
    ).all()
    msgs_in = session.scalar(
        select(func.count(Message.id)).where(
            Message.direction == "in", Message.is_internal_note.is_(False), Message.occurred_at >= since
        )
    ) or 0
    msgs_out = session.scalar(
        select(func.count(Message.id)).where(
            Message.direction == "out", Message.is_internal_note.is_(False), Message.occurred_at >= since
        )
    ) or 0

    # Per-agent outbound (replies) in window
    per_agent_rows = session.execute(
        select(Message.agent_name, func.count(Message.id)).where(
            Message.direction == "out", Message.is_internal_note.is_(False),
            Message.occurred_at >= since, Message.agent_name != "",
        ).group_by(Message.agent_name).order_by(func.count(Message.id).desc())
    ).all()

    # Avg first-response time over recently-active conversations (bounded loop)
    recent_convs = session.scalars(
        select(Conversation).where(Conversation.last_message_at >= since)
        .order_by(Conversation.last_message_at.desc()).limit(500)
    ).all()
    deltas = []
    for conv in recent_convs:
        first_in = session.scalar(
            select(func.min(Message.occurred_at)).where(
                Message.conversation_id == conv.id, Message.direction == "in"
            )
        )
        if not first_in:
            continue
        first_out = session.scalar(
            select(func.min(Message.occurred_at)).where(
                Message.conversation_id == conv.id, Message.direction == "out",
                Message.is_internal_note.is_(False), Message.occurred_at >= first_in,
            )
        )
        if first_out:
            deltas.append((first_out - first_in).total_seconds())
    avg_response_min = round(sum(deltas) / len(deltas) / 60, 1) if deltas else None

    inbox = {
        "by_status": {s: c for s, c in status_rows},
        "by_channel": {s: c for s, c in channel_rows},
        "messages_in": msgs_in, "messages_out": msgs_out,
        "per_agent": [{"agent": a or "—", "replies": c} for a, c in per_agent_rows],
        "avg_first_response_min": avg_response_min,
        "responded_conversations": len(deltas),
    }

    # ── Contacts ──────────────────────────────────────────────────────────
    total_contacts = session.scalar(select(func.count(Contact.id))) or 0
    customers = session.scalar(select(func.count(Contact.id)).where(Contact.is_customer.is_(True))) or 0
    by_source = {}
    for src in ["customer", "kvk", "lead", "prospect", "inbox", "whatsapp"]:
        by_source[src] = session.scalar(
            select(func.count(Contact.id)).where(Contact.source_summary.like(f"%{src}%"))
        ) or 0
    top_sectors = session.execute(
        select(Contact.sector, func.count(Contact.id)).where(Contact.sector != "")
        .group_by(Contact.sector).order_by(func.count(Contact.id).desc()).limit(8)
    ).all()
    top_countries = session.execute(
        select(Contact.country_code, func.count(Contact.id)).where(Contact.country_code != "")
        .group_by(Contact.country_code).order_by(func.count(Contact.id).desc()).limit(8)
    ).all()
    new_contacts = session.scalar(
        select(func.count(Contact.id)).where(Contact.created_at >= since)
    ) or 0

    contacts = {
        "total": total_contacts, "customers": customers, "new_in_window": new_contacts,
        "by_source": by_source,
        "top_sectors": [{"name": s, "count": c} for s, c in top_sectors],
        "top_countries": [{"name": s, "count": c} for s, c in top_countries],
    }

    return {"days": days, "since": since, "email": email, "inbox": inbox, "contacts": contacts}


def live_counts(session: Session) -> dict:
    """Tiny payload for the real-time SSE/badge."""
    unread = session.scalar(select(func.count(Conversation.id)).where(Conversation.unread.is_(True))) or 0
    open_convs = session.scalar(select(func.count(Conversation.id)).where(Conversation.status == "open")) or 0
    return {"unread": unread, "open": open_convs}
