from __future__ import annotations

import json
import smtplib
from dataclasses import dataclass
from datetime import date, datetime
from email.message import EmailMessage
from urllib.request import Request, urlopen

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import EmailLog, MatchStatus, OutreachQueueItem, Prospect, ProspectState, QueueState, SuppressionEntry
from app.utils import build_unsubscribe_token, email_domain, normalize_email


@dataclass
class SendResult:
    ok: bool
    provider: str
    response_excerpt: str


def default_subject(prospect: Prospect) -> str:
    return f"A more premium look for {prospect.company_name}"


def default_body(prospect: Prospect) -> str:
    website = prospect.website or "jullie website"
    return "\n".join(
        [
            f"Beste {prospect.company_name} Team,",
            "",
            f"Ik kwam {website} tegen en dacht dat onze producten en diensten van Schild Inc relevant kunnen zijn voor jullie bedrijf.",
            "",
            "Ken je Schild Inc al? Wij helpen fietsenwinkels hun branding te versterken met gepersonaliseerde premium metalen labels en custom bike accessoires met eigen logo.",
            "",
            "Deze labels geven fietsen en de totale presentatie een professionelere en meer premium uitstraling. Onze oplossingen worden al gebruikt door meer dan 500 fietsenwinkels, waaronder BikeTotaal, Azor, VMG, Gazelle en nog veel meer.",
            "",
            "Om het makkelijk te maken, kunnen we eerst gratis een labelontwerp maken met jullie huidige logo. Zo kun je direct zien hoe jullie branding eruit kan zien op jullie fietsen.",
            "",
            "En als jullie huidige logo wat verouderd aanvoelt, bieden we ook een logo redesign service aan voor €89,95 om het moderner en meer premium te maken.",
            "",
            "Naast labels bieden we ook white-label bike accessoires met jullie logo aan. Deze kunnen:",
            "",
            "* in de winkel worden doorverkocht",
            "* als giveaway worden meegegeven bij fietsverkopen",
            "* helpen om de klanttevredenheid te verhogen",
            "* extra zichtbaarheid voor jullie merk geven wanneer klanten ze buiten gebruiken",
            "",
            "Het doel is dus niet alleen om een product te verkopen, maar om jullie fietsenwinkel te helpen een sterker en zichtbaarder merk op te bouwen.",
            "",
            "Als je wilt, kan ik je sturen:",
            "",
            "* een paar projectvoorbeelden",
            "* onze catalogus",
            "* of een eerste gratis labelontwerpidee voor jullie winkel",
            "",
            "Sta je daarvoor open?",
            "",
            "Met vriendelijke groet,",
            "",
            "",
            "Schild Inc Team",
        ]
    )


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
        )
    ) or 0


def build_queue_for_day(session: Session, queue_day: date, limit: int | None = None) -> int:
    limit = limit or settings.default_queue_size
    candidates = session.scalars(
        select(Prospect).where(
            Prospect.review_status == ProspectState.approved,
            Prospect.approved_for_outreach.is_(True),
            Prospect.match_status == MatchStatus.new_prospect,
        ).order_by(Prospect.updated_at.desc())
    ).all()

    created = 0
    for prospect in candidates:
        suppressed, _ = is_suppressed(session, prospect.email, prospect.company_name)
        if suppressed or not prospect.email:
            continue
        existing = session.scalar(
            select(OutreachQueueItem).where(
                OutreachQueueItem.prospect_id == prospect.id,
                OutreachQueueItem.queue_date == queue_day,
            )
        )
        if existing:
            continue
        item = OutreachQueueItem(
            prospect=prospect,
            queue_date=queue_day,
            state=QueueState.ready,
            subject=default_subject(prospect),
            body=default_body(prospect),
        )
        session.add(item)
        created += 1
        if created >= limit:
            break
    return created


def append_unsubscribe_footer(body: str, to_email: str) -> tuple[str, str]:
    token = build_unsubscribe_token(to_email)
    unsubscribe_url = f"{settings.app_base_url}/unsubscribe/{token}?email={to_email}"
    footer = f"\n\n--\nReplies go to {settings.reply_to_email}\nUnsubscribe: {unsubscribe_url}"
    return f"{body.rstrip()}{footer}", token


def send_queue_item(session: Session, item: OutreachQueueItem) -> SendResult:
    prospect = item.prospect
    if prospect.match_status != MatchStatus.new_prospect:
        raise ValueError("Only net-new approved prospects can be sent.")

    suppressed, reason = is_suppressed(session, prospect.email, prospect.company_name)
    if suppressed:
        item.state = QueueState.suppressed
        session.add(
            EmailLog(
                queue_item=item,
                prospect_id=prospect.id,
                to_email=prospect.email,
                subject=item.subject,
                provider=settings.mail_provider,
                status="suppressed",
                response_excerpt=reason,
                reply_to=settings.reply_to_email,
            )
        )
        return SendResult(False, settings.mail_provider, reason)

    if sent_count_for_day(session, item.queue_date) >= settings.daily_send_limit:
        raise ValueError("Daily send limit reached.")

    final_body, token = append_unsubscribe_footer(item.body, prospect.email)
    result = _send_email(
        to_email=prospect.email,
        subject=item.subject,
        body=final_body,
        reply_to=settings.reply_to_email,
    )
    item.state = QueueState.sent if result.ok else item.state
    item.sent_to = prospect.email
    item.sent_at = datetime.utcnow() if result.ok else item.sent_at
    session.add(
        EmailLog(
            queue_item=item,
            prospect_id=prospect.id,
            to_email=prospect.email,
            subject=item.subject,
            provider=result.provider,
            status="sent" if result.ok else "failed",
            response_excerpt=result.response_excerpt[:1000],
            reply_to=settings.reply_to_email,
            unsubscribe_token=token,
            sent_at=datetime.utcnow() if result.ok else None,
        )
    )
    return result


def _send_email(to_email: str, subject: str, body: str, reply_to: str) -> SendResult:
    provider = settings.mail_provider.lower()
    if provider == "resend":
        payload = json.dumps(
            {
                "from": settings.mail_from,
                "to": [to_email],
                "subject": subject,
                "text": body,
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
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
            if settings.smtp_use_tls:
                smtp.starttls()
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(message)
        return SendResult(True, "smtp", "SMTP send ok")

    return SendResult(True, "console", f"Console mode only. Would send to {to_email}.")
