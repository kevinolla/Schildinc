"""Email engine — campaign build, render, tracking, and the send loop.

Pipeline:
    build_recipients()  → resolve an audience (KVK / leads / customers) into
                          EmailCampaignRecipient rows, skipping suppressed,
                          already-client, and email-less records.
    render_for_recipient() → fill {{merge_fields}}, inject the open pixel,
                          rewrite links for click tracking, fill the
                          unsubscribe URL.
    send_campaign_batch() → send pending recipients via Gmail, respecting the
                          daily cap + per-send spacing.
    start_email_sender_scheduler() → background daemon draining 'sending' and
                          due 'scheduled' campaigns.

Open tracking is a 1x1 pixel; note Gmail proxies/caches images so opens are
indicative, not exact. Click tracking (link rewrite + redirect) is reliable.
"""
from __future__ import annotations

import html as html_lib
import json
import re
import secrets
import time
from datetime import date, datetime
from threading import Lock, Thread
from urllib.parse import quote, urlencode

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.emailing import is_suppressed
from app.gmail_sender import GmailNotConnected, get_active_account, send_message
from app.models import (
    Customer,
    EmailCampaign,
    EmailCampaignRecipient,
    EmailEvent,
    EmailTemplate,
    FacebookLead,
    KvkCompany,
    SuppressionEntry,
)
from app.utils import normalize_email

_MERGE_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")
_HREF_RE = re.compile(r'href="(https?://[^"]+)"', re.IGNORECASE)


# ── Merge rendering ─────────────────────────────────────────────────────────


def render_merge(template_str: str, values: dict[str, str], *, escape: bool) -> str:
    """Replace {{ field }} tokens. Unknown fields render empty.

    When `escape` is True (HTML context) values are HTML-escaped to prevent
    a stray company name from breaking layout or injecting markup.
    """
    def _sub(match: re.Match) -> str:
        key = match.group(1)
        val = str(values.get(key, "") or "")
        return html_lib.escape(val) if escape else val

    return _MERGE_RE.sub(_sub, template_str)


def _tracking_base() -> str:
    return settings.app_base_url.rstrip("/")


def unsubscribe_url_for(token: str) -> str:
    return f"{_tracking_base()}/e/u/{token}"


def open_pixel_url_for(token: str) -> str:
    return f"{_tracking_base()}/e/o/{token}.gif"


def click_url_for(token: str, target: str) -> str:
    return f"{_tracking_base()}/e/c/{token}?{urlencode({'u': target})}"


def inject_tracking(html_body: str, token: str) -> str:
    """Rewrite outbound links for click tracking and append the open pixel.

    The unsubscribe link ({{unsubscribe_url}} → /e/u/) is left untouched so we
    don't double-count or break opt-out.
    """
    def _rewrite(match: re.Match) -> str:
        url = match.group(1)
        if "/e/u/" in url or "/e/o/" in url or "/e/c/" in url:
            return match.group(0)
        return f'href="{click_url_for(token, url)}"'

    rewritten = _HREF_RE.sub(_rewrite, html_body)
    pixel = (
        f'<img src="{open_pixel_url_for(token)}" width="1" height="1" '
        f'alt="" style="display:none;border:0;width:1px;height:1px;">'
    )
    if "</body>" in rewritten:
        return rewritten.replace("</body>", f"{pixel}</body>", 1)
    return rewritten + pixel


def render_for_recipient(
    campaign: EmailCampaign, recipient: EmailCampaignRecipient
) -> tuple[str, str, str]:
    """Return (subject, html_body, text_body) fully rendered + tracked."""
    try:
        values = json.loads(recipient.merge_data or "{}")
    except Exception:
        values = {}
    values.setdefault("company_name", recipient.company_name)
    values.setdefault("contact_name", recipient.contact_name)
    values.setdefault("sender_name", campaign.sender_name or settings.gmail_sender_name)
    values.setdefault("reply_to", campaign.reply_to or settings.reply_to_email)

    # Owner personalization with a graceful fallback so a cold email never
    # reads "Hi ," — uses owner first name → company name → "there".
    contact_name = (values.get("contact_name") or "").strip()
    first_name = contact_name.split()[0] if contact_name else ""
    values["first_name"] = first_name
    values["greeting_name"] = first_name or (values.get("company_name") or "").strip() or "there"

    # Signature + legal footer (brand-safe, compliant cold outreach).
    values.setdefault("sender_title", settings.sender_title)
    values.setdefault("company_legal_name", settings.company_legal_name)
    values.setdefault("company_address", settings.company_address)
    values.setdefault("company_phone", settings.company_phone)
    values.setdefault("company_website", settings.company_website)

    values["unsubscribe_url"] = unsubscribe_url_for(recipient.tracking_token)

    subject = render_merge(campaign.subject, values, escape=False)
    html_body = render_merge(campaign.body_html, values, escape=True)
    text_body = render_merge(campaign.body_text, values, escape=False)
    html_body = inject_tracking(html_body, recipient.tracking_token)
    return subject, html_body, text_body


# ── Audience resolution ─────────────────────────────────────────────────────


def _kvk_merge(company: KvkCompany) -> dict[str, str]:
    return {
        "company_name": company.company_name or "",
        # Owner name (from the enrichment agent) drives the personalized greeting.
        "contact_name": company.owner_name or "",
        "city": company.primary_city or "",
        "country": company.country_code or "",
        "website": company.website or "",
    }


def _lead_merge(lead: FacebookLead) -> dict[str, str]:
    return {
        "company_name": lead.company_name or lead.full_name or "",
        "contact_name": lead.full_name or "",
        "city": "",
        "country": lead.country or "",
        "website": "",
    }


def _customer_merge(cust: Customer) -> dict[str, str]:
    return {
        "company_name": cust.canonical_company_name or "",
        "contact_name": cust.contact_person or "",
        "city": cust.city or "",
        "country": cust.country_code or "",
        "website": cust.website or "",
    }


def build_recipients(
    session: Session,
    campaign: EmailCampaign,
    *,
    kvk_ids: list[int] | None = None,
    lead_ids: list[int] | None = None,
    customer_ids: list[int] | None = None,
) -> dict[str, int]:
    """Materialize recipients for a campaign. Idempotent per (campaign,email).

    Skips: no email, suppressed/unsubscribed, already-existing recipient.
    Returns counts dict.
    """
    added = 0
    skipped_no_email = 0
    skipped_suppressed = 0
    skipped_dupe = 0

    seen_emails: set[str] = set(
        e for (e,) in session.execute(
            select(EmailCampaignRecipient.to_email).where(
                EmailCampaignRecipient.campaign_id == campaign.id
            )
        ).all()
    )

    def _try_add(email: str, company: str, contact: str, merge: dict, *,
                 source_type: str, kvk_id=None, lead_id=None, customer_id=None) -> None:
        nonlocal added, skipped_no_email, skipped_suppressed, skipped_dupe
        norm = normalize_email(email)
        if not norm or "@" not in norm:
            skipped_no_email += 1
            return
        if norm in seen_emails:
            skipped_dupe += 1
            return
        suppressed, _ = is_suppressed(session, norm, company)
        if suppressed:
            skipped_suppressed += 1
            return
        seen_emails.add(norm)
        merge = dict(merge)
        merge["country"] = merge.get("country", "")
        session.add(
            EmailCampaignRecipient(
                campaign_id=campaign.id,
                source_type=source_type,
                kvk_company_id=kvk_id,
                facebook_lead_id=lead_id,
                customer_id=customer_id,
                to_email=norm,
                company_name=company,
                contact_name=contact,
                merge_data=json.dumps(merge),
                status="pending",
                tracking_token=secrets.token_urlsafe(18),
            )
        )
        added += 1

    if kvk_ids:
        for c in session.scalars(select(KvkCompany).where(KvkCompany.id.in_(kvk_ids))).all():
            _try_add(c.email_public, c.company_name or "", "", _kvk_merge(c),
                     source_type="kvk", kvk_id=c.id)
    if lead_ids:
        for l in session.scalars(select(FacebookLead).where(FacebookLead.id.in_(lead_ids))).all():
            _try_add(l.email, l.company_name or l.full_name or "", l.full_name or "",
                     _lead_merge(l), source_type="lead", lead_id=l.id)
    if customer_ids:
        for cust in session.scalars(select(Customer).where(Customer.id.in_(customer_ids))).all():
            _try_add(cust.customer_email_primary, cust.canonical_company_name or "",
                     cust.contact_person or "", _customer_merge(cust),
                     source_type="customer", customer_id=cust.id)

    # Flush the pending INSERTs so the count query sees them (autoflush is off).
    session.flush()
    campaign.total_recipients = session.scalar(
        select(func.count(EmailCampaignRecipient.id)).where(
            EmailCampaignRecipient.campaign_id == campaign.id
        )
    ) or 0
    session.commit()
    return {
        "added": added,
        "skipped_no_email": skipped_no_email,
        "skipped_suppressed": skipped_suppressed,
        "skipped_duplicate": skipped_dupe,
        "total": campaign.total_recipients,
    }


# ── Sending ─────────────────────────────────────────────────────────────────


def sent_today(session: Session) -> int:
    """Count emails sent across all campaigns since UTC midnight."""
    start = datetime.combine(date.today(), datetime.min.time())
    return session.scalar(
        select(func.count(EmailCampaignRecipient.id)).where(
            EmailCampaignRecipient.status == "sent",
            EmailCampaignRecipient.sent_at >= start,
        )
    ) or 0


def send_campaign_batch(session: Session, campaign: EmailCampaign, *, limit: int | None = None) -> dict:
    """Send up to `limit` pending recipients for one campaign.

    Honors the global daily cap and per-send spacing. Returns a result dict.
    """
    if get_active_account(session) is None:
        return {"ok": False, "error": "gmail_not_connected", "sent": 0}

    remaining_today = max(0, settings.gmail_daily_limit - sent_today(session))
    if remaining_today <= 0:
        return {"ok": True, "sent": 0, "note": "daily_limit_reached"}

    batch_limit = remaining_today if limit is None else min(limit, remaining_today)

    pending = session.scalars(
        select(EmailCampaignRecipient)
        .where(
            EmailCampaignRecipient.campaign_id == campaign.id,
            EmailCampaignRecipient.status == "pending",
        )
        .order_by(EmailCampaignRecipient.id.asc())
        .limit(batch_limit)
    ).all()

    if not pending:
        _finalize_if_done(session, campaign)
        return {"ok": True, "sent": 0, "note": "no_pending"}

    sender_alias = campaign.sender_alias or settings.gmail_send_as
    sender_name = campaign.sender_name or settings.gmail_sender_name
    reply_to = campaign.reply_to or settings.reply_to_email

    sent = 0
    failed = 0
    for recipient in pending:
        # Re-check suppression at send time (operator may have opted them out).
        suppressed, reason = is_suppressed(session, recipient.to_email, recipient.company_name)
        if suppressed:
            recipient.status = "suppressed"
            recipient.error = reason
            session.commit()
            continue

        subject, html_body, text_body = render_for_recipient(campaign, recipient)
        try:
            result = send_message(
                session,
                to_email=recipient.to_email,
                subject=subject,
                body_html=html_body,
                body_text=text_body,
                from_alias=sender_alias,
                from_name=sender_name,
                reply_to=reply_to,
                list_unsubscribe=unsubscribe_url_for(recipient.tracking_token),
            )
        except GmailNotConnected:
            return {"ok": False, "error": "gmail_not_connected", "sent": sent}

        if result.ok:
            recipient.status = "sent"
            recipient.gmail_message_id = result.message_id
            recipient.sent_at = datetime.utcnow()
            recipient.error = ""
            campaign.sent_count += 1
            sent += 1
        else:
            if not result.transient:
                recipient.status = "failed"
                recipient.error = result.error
                campaign.failed_count += 1
                failed += 1
            else:
                recipient.error = result.error  # stays pending for retry
        session.commit()

        if sent_today(session) >= settings.gmail_daily_limit:
            break
        if settings.gmail_send_spacing_seconds > 0:
            time.sleep(settings.gmail_send_spacing_seconds)

    _finalize_if_done(session, campaign)
    return {"ok": True, "sent": sent, "failed": failed}


def _finalize_if_done(session: Session, campaign: EmailCampaign) -> None:
    pending_left = session.scalar(
        select(func.count(EmailCampaignRecipient.id)).where(
            EmailCampaignRecipient.campaign_id == campaign.id,
            EmailCampaignRecipient.status == "pending",
        )
    ) or 0
    if pending_left == 0 and campaign.status == "sending":
        campaign.status = "sent"
        campaign.completed_at = datetime.utcnow()
        session.commit()


# ── Tracking event recording ────────────────────────────────────────────────


def record_open(session: Session, token: str, *, user_agent: str = "", ip: str = "") -> None:
    recipient = session.scalar(
        select(EmailCampaignRecipient).where(EmailCampaignRecipient.tracking_token == token)
    )
    if not recipient:
        return
    now = datetime.utcnow()
    recipient.open_count += 1
    recipient.last_opened_at = now
    if recipient.first_opened_at is None:
        recipient.first_opened_at = now
        campaign = session.get(EmailCampaign, recipient.campaign_id)
        if campaign:
            campaign.open_count += 1
    session.add(EmailEvent(
        recipient_id=recipient.id, campaign_id=recipient.campaign_id,
        event_type="open", user_agent=user_agent[:300], ip_address=ip[:64],
    ))
    session.commit()


def record_click(session: Session, token: str, url: str, *, user_agent: str = "", ip: str = "") -> None:
    recipient = session.scalar(
        select(EmailCampaignRecipient).where(EmailCampaignRecipient.tracking_token == token)
    )
    if not recipient:
        return
    now = datetime.utcnow()
    recipient.click_count += 1
    if recipient.first_clicked_at is None:
        recipient.first_clicked_at = now
        campaign = session.get(EmailCampaign, recipient.campaign_id)
        if campaign:
            campaign.click_count += 1
    # A click is also a strong open signal.
    if recipient.first_opened_at is None:
        recipient.first_opened_at = now
        recipient.open_count += 1
        campaign = session.get(EmailCampaign, recipient.campaign_id)
        if campaign:
            campaign.open_count += 1
    session.add(EmailEvent(
        recipient_id=recipient.id, campaign_id=recipient.campaign_id,
        event_type="click", url=url[:1000], user_agent=user_agent[:300], ip_address=ip[:64],
    ))
    session.commit()


def record_unsubscribe(session: Session, token: str) -> EmailCampaignRecipient | None:
    recipient = session.scalar(
        select(EmailCampaignRecipient).where(EmailCampaignRecipient.tracking_token == token)
    )
    if not recipient:
        return None
    if recipient.unsubscribed_at is None:
        recipient.unsubscribed_at = datetime.utcnow()
        campaign = session.get(EmailCampaign, recipient.campaign_id)
        if campaign:
            campaign.unsubscribe_count += 1
        existing = session.scalar(
            select(SuppressionEntry).where(SuppressionEntry.email == recipient.to_email)
        )
        if not existing:
            session.add(SuppressionEntry(
                email=recipient.to_email, domain="", company_name=recipient.company_name,
                reason="recipient unsubscribe (campaign)", source="email_campaign", active=True,
            ))
        session.add(EmailEvent(
            recipient_id=recipient.id, campaign_id=recipient.campaign_id, event_type="unsubscribe",
        ))
        session.commit()
    return recipient


# ── Background sender daemon ─────────────────────────────────────────────────

_scheduler_started = False
_scheduler_lock = Lock()


def process_due_campaigns(session: Session) -> int:
    """Promote due scheduled campaigns to 'sending', then drain 'sending'."""
    now = datetime.utcnow()
    due = session.scalars(
        select(EmailCampaign).where(
            EmailCampaign.status == "scheduled",
            EmailCampaign.scheduled_at <= now,
        )
    ).all()
    for campaign in due:
        campaign.status = "sending"
        campaign.started_at = campaign.started_at or now
    if due:
        session.commit()

    total_sent = 0
    sending = session.scalars(
        select(EmailCampaign).where(EmailCampaign.status == "sending").order_by(EmailCampaign.id.asc())
    ).all()
    for campaign in sending:
        if sent_today(session) >= settings.gmail_daily_limit:
            break
        result = send_campaign_batch(session, campaign)
        total_sent += result.get("sent", 0)
        if result.get("error") == "gmail_not_connected":
            break
    return total_sent


def _sender_loop() -> None:
    while True:
        try:
            session = SessionLocal()
            try:
                process_due_campaigns(session)
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001
            print(f"[email_sender] loop error: {exc}")
        time.sleep(max(15, settings.email_sender_interval))


def start_email_sender_scheduler() -> None:
    global _scheduler_started
    if not settings.email_sender_enabled:
        return
    with _scheduler_lock:
        if _scheduler_started:
            return
        _scheduler_started = True
    Thread(target=_sender_loop, daemon=True, name="email-sender").start()
    print("[email_sender] scheduler started")
