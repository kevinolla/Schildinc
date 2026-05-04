from __future__ import annotations

import csv
import json
import smtplib
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from io import StringIO
from urllib.request import Request, urlopen

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.discovery import ensure_prospect_contacts
from app.models import EmailLog, MatchStatus, OutreachQueueItem, Prospect, ProspectActivityLog, ProspectState, QueueState, SuppressionEntry
from app.outreach_templates import OutreachBundle, build_outreach_bundle
from app.utils import build_unsubscribe_token, email_domain, normalize_email, within_send_window


@dataclass
class SendResult:
    ok: bool
    provider: str
    response_excerpt: str
    transient: bool = False


@dataclass
class QueuePreviewItem:
    prospect: Prospect
    bundle: OutreachBundle


PRIORITY_ORDER = {
    "High": 0,
    "Medium": 1,
    "Low": 2,
    "Very Low": 3,
    "Manual Review": 4,
}


def queue_candidate_allowed(session: Session, prospect: Prospect, queue_day: date) -> tuple[bool, str]:
    if not settings.campaign_active:
        return False, "campaign_inactive"
    if not prospect.email:
        return False, "no_email"
    if prospect.review_status != ProspectState.approved or not prospect.approved_for_outreach:
        return False, "not_approved"
    if prospect.match_status != MatchStatus.new_prospect:
        return False, "existing_or_possible_customer"
    if prospect.headquarters_required:
        return False, "hq_required"
    if prospect.outreach_priority in {"Very Low", "Manual Review"}:
        return False, "low_priority"
    if prospect.bike_shop_tier in {"Low Tier", "Brand Store", "Low Fit"}:
        return False, "tier_excluded"
    suppressed, reason = is_suppressed(session, prospect.email, prospect.company_name)
    if suppressed:
        return False, reason
    if prospect.cooldown_until and queue_day <= prospect.cooldown_until.date():
        return False, "cooldown_active"
    return True, "eligible"


def is_suppressed(session: Session, email: str, company_name: str = "") -> tuple[bool, str]:
    normalized = normalize_email(email)
    domain = email_domain(email)
    suppression = session.scalar(
        select(SuppressionEntry).where(
            SuppressionEntry.active.is_(True),
            (SuppressionEntry.email == normalized) | (SuppressionEntry.domain == domain),
        )
    )
    if suppression:
        return True, suppression.reason or "Suppression list"
    return False, ""


def sent_count_for_day(session: Session, queue_day: date) -> int:
    return session.scalar(
        select(func.count(OutreachQueueItem.id)).where(
            OutreachQueueItem.queue_date == queue_day,
            OutreachQueueItem.state == QueueState.sent,
            OutreachQueueItem.channel == "email",
        )
    ) or 0


def build_queue_for_day(session: Session, queue_day: date, limit: int | None = None, dry_run: bool = False) -> int:
    limit = limit or settings.default_queue_size
    candidates = session.scalars(
        select(Prospect)
        .where(
            Prospect.review_status == ProspectState.approved,
            Prospect.approved_for_outreach.is_(True),
        )
        .order_by(Prospect.updated_at.desc())
    ).all()
    _auto_refresh_candidates(session, candidates)
    candidates = sorted(candidates, key=lambda item: (PRIORITY_ORDER.get(item.outreach_priority or "Manual Review", 9), item.company_name.lower()))

    created = 0
    for prospect in candidates:
        allowed, reason = queue_candidate_allowed(session, prospect, queue_day)
        if not allowed:
            session.add(
                ProspectActivityLog(
                    prospect=prospect,
                    action_type="queue_skip",
                    status=reason,
                    source_url=prospect.website or prospect.google_maps_url,
                    detail=f"Skipped on {queue_day.isoformat()}",
                )
            )
            continue
        existing = session.scalar(
            select(OutreachQueueItem).where(
                OutreachQueueItem.prospect_id == prospect.id,
                OutreachQueueItem.queue_date == queue_day,
                OutreachQueueItem.channel == "email",
            )
        )
        if existing:
            continue

        bundle = build_outreach_bundle(prospect)
        if not dry_run:
            session.add(
                OutreachQueueItem(
                    prospect=prospect,
                    queue_date=queue_day,
                    state=QueueState.ready,
                    channel="email",
                    campaign_name="schild-bike-outreach",
                    subject=bundle.subject,
                    body=bundle.text_body,
                    body_html=bundle.html_body,
                    dry_run=False,
                )
            )
        created += 1
        if created >= limit:
            break
    return created


def preview_queue_for_day(session: Session, queue_day: date, limit: int | None = None) -> list[QueuePreviewItem]:
    limit = limit or settings.preview_contact_count
    candidates = session.scalars(
        select(Prospect)
        .where(
            Prospect.review_status == ProspectState.approved,
            Prospect.approved_for_outreach.is_(True),
        )
        .order_by(Prospect.updated_at.desc())
    ).all()
    _auto_refresh_candidates(session, candidates)
    ordered = sorted(candidates, key=lambda item: (PRIORITY_ORDER.get(item.outreach_priority or "Manual Review", 9), item.company_name.lower()))
    previews: list[QueuePreviewItem] = []
    for prospect in ordered:
        allowed, _ = queue_candidate_allowed(session, prospect, queue_day)
        if not allowed:
            continue
        previews.append(QueuePreviewItem(prospect=prospect, bundle=build_outreach_bundle(prospect)))
        if len(previews) >= limit:
            break
    return previews


def export_queue_csv(session: Session, queue_day: date) -> str:
    items = session.scalars(
        select(OutreachQueueItem)
        .options(selectinload(OutreachQueueItem.prospect))
        .where(OutreachQueueItem.queue_date == queue_day)
        .order_by(OutreachQueueItem.id.asc())
    ).all()
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["queue_date", "channel", "company_name", "email", "subject", "state", "tier", "priority"])
    for item in items:
        writer.writerow(
            [
                item.queue_date.isoformat(),
                item.channel,
                item.prospect.company_name,
                item.prospect.email,
                item.subject,
                item.state.value,
                item.prospect.bike_shop_tier,
                item.prospect.outreach_priority,
            ]
        )
    return output.getvalue()


def _auto_refresh_candidates(session: Session, candidates: list[Prospect]) -> int:
    if not settings.auto_contact_discovery_enabled:
        return 0
    refreshed = 0
    for prospect in candidates:
        if refreshed >= settings.auto_contact_refresh_batch_size:
            break
        if ensure_prospect_contacts(session, prospect):
            refreshed += 1
    return refreshed


def append_unsubscribe_footer(text_body: str, html_body: str, to_email: str) -> tuple[str, str, str]:
    token = build_unsubscribe_token(to_email)
    unsubscribe_url = f"{settings.app_base_url}/unsubscribe/{token}?email={to_email}"
    text_footer = f"\n\n--\nReplies go to {settings.reply_to_email}\nUnsubscribe: {unsubscribe_url}"
    html_footer = (
        f'<div style="margin-top:22px;padding-top:14px;border-top:1px solid #ddd;color:#6b7280;font-size:12px;">'
        f'Replies go to <a href="mailto:{settings.reply_to_email}">{settings.reply_to_email}</a><br>'
        f'<a href="{unsubscribe_url}">Unsubscribe</a>'
        f"</div>"
    )
    return f"{text_body.rstrip()}{text_footer}", f"{html_body.rstrip()}</body>".replace("</body>", f"{html_footer}</body>"), token


def send_queue_item(session: Session, item: OutreachQueueItem) -> SendResult:
    prospect = item.prospect
    allowed, reason = queue_candidate_allowed(session, prospect, item.queue_date)
    if not allowed:
        item.state = QueueState.suppressed if "suppress" in reason.lower() else QueueState.skipped
        session.add(
            EmailLog(
                queue_item=item,
                prospect_id=prospect.id,
                to_email=prospect.email,
                subject=item.subject,
                channel=item.channel,
                provider=settings.mail_provider,
                status="blocked",
                response_excerpt=reason,
                reply_to=settings.reply_to_email,
            )
        )
        return SendResult(False, settings.mail_provider, reason, transient=False)

    if not within_send_window(datetime.utcnow(), settings.send_window_start, settings.send_window_end):
        item.state = QueueState.skipped
        session.add(
            ProspectActivityLog(
                prospect=prospect,
                action_type="email_send",
                status="blocked",
                source_url=prospect.website or prospect.google_maps_url,
                detail="outside_send_window",
            )
        )
        return SendResult(False, settings.mail_provider, "outside_send_window", transient=False)

    if sent_count_for_day(session, item.queue_date) >= settings.daily_send_limit:
        item.state = QueueState.skipped
        session.add(
            ProspectActivityLog(
                prospect=prospect,
                action_type="email_send",
                status="blocked",
                source_url=prospect.website or prospect.google_maps_url,
                detail="daily_send_limit_reached",
            )
        )
        return SendResult(False, settings.mail_provider, "daily_send_limit_reached", transient=False)

    text_body, html_body, token = append_unsubscribe_footer(item.body, item.body_html, prospect.email)
    result = _send_email(
        to_email=prospect.email,
        subject=item.subject,
        body=text_body,
        html_body=html_body,
        reply_to=settings.reply_to_email,
    )
    if result.ok:
        item.state = QueueState.sent
        item.sent_to = prospect.email
        item.sent_at = datetime.utcnow()
        prospect.last_contacted_at = item.sent_at
        prospect.cooldown_until = item.sent_at + timedelta(days=settings.outreach_cooldown_days)
    elif result.transient:
        item.state = QueueState.ready
    session.add(
        EmailLog(
            queue_item=item,
            prospect_id=prospect.id,
            to_email=prospect.email,
            subject=item.subject,
            channel=item.channel,
            provider=result.provider,
            status="sent" if result.ok else ("retryable_error" if result.transient else "failed"),
            response_excerpt=result.response_excerpt[:1000],
            html_excerpt=html_body[:1000],
            reply_to=settings.reply_to_email,
            unsubscribe_token=token,
            sent_at=datetime.utcnow() if result.ok else None,
        )
    )
    session.add(
        ProspectActivityLog(
            prospect=prospect,
            action_type="email_send",
            status="sent" if result.ok else ("retryable_error" if result.transient else "failed"),
            source_url=prospect.website or prospect.google_maps_url,
            detail=result.response_excerpt[:1000],
        )
    )
    return result


def send_ready_queue(session: Session, queue_day: date, limit: int | None = None) -> int:
    limit = limit or settings.daily_send_limit
    if not settings.campaign_active:
        return 0
    items = session.scalars(
        select(OutreachQueueItem)
        .options(selectinload(OutreachQueueItem.prospect))
        .where(
            OutreachQueueItem.queue_date == queue_day,
            OutreachQueueItem.channel == "email",
            OutreachQueueItem.state.in_([QueueState.ready, QueueState.queued]),
        )
        .order_by(OutreachQueueItem.id.asc())
    ).all()
    sent = 0
    for item in items:
        if sent_count_for_day(session, queue_day) >= settings.daily_send_limit or sent >= limit:
            break
        result = send_queue_item(session, item)
        if result.ok:
            sent += 1
    return sent


def _send_email(to_email: str, subject: str, body: str, html_body: str, reply_to: str) -> SendResult:
    provider = settings.mail_provider.lower()
    try:
        if provider == "resend":
            payload = json.dumps(
                {
                    "from": settings.mail_from,
                    "to": [to_email],
                    "subject": subject,
                    "text": body,
                    "html": html_body,
                    "reply_to": reply_to,
                }
            ).encode("utf-8")
            request = Request(
                "https://api.resend.com/emails",
                data=payload,
                method="POST",
                headers={
                    "Authorization": f"Bearer {settings.resend_api_key}",
                    "Content-Type": "application/json",
                },
            )
            with urlopen(request, timeout=20) as response:
                text = response.read().decode("utf-8")
            return SendResult(True, "resend", text)

        if provider == "smtp":
            message = EmailMessage()
            message["From"] = settings.mail_from
            message["To"] = to_email
            message["Subject"] = subject
            message["Reply-To"] = reply_to
            message.set_content(body)
            message.add_alternative(html_body, subtype="html")
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
                if settings.smtp_use_tls:
                    smtp.starttls()
                if settings.smtp_username:
                    smtp.login(settings.smtp_username, settings.smtp_password)
                smtp.send_message(message)
            return SendResult(True, "smtp", "SMTP send ok")
    except Exception as exc:  # noqa: BLE001
        excerpt = str(exc)
        transient = any(token in excerpt.lower() for token in ["timeout", "temporar", "rate limit", "connection reset", "connection refused"])
        return SendResult(False, provider or "unknown", excerpt, transient=transient)

    return SendResult(True, "console", f"Console mode only. Would send to {to_email}.")
