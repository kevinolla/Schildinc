"""3-step cold email SEQUENCE engine (DESIGN_V2 Phase 3B).

A weekly cadence (default Wednesday 07:00 lead-local) of baseline emails with
optional layered personalization. It is a PRODUCER on top of the existing
campaign sender: when a step is due it materializes a normal EmailCampaign +
per-recipient EmailCampaignRecipient (with personalized merge_data) and lets the
UNCHANGED sender daemon drain it — so suppression-at-send, tracking, throttle and
the dry-run keystone all still apply. This module never sends mail itself.

Safety:
  * Gated by settings.sequence_engine_enabled (default OFF).
  * Baseline templates stand alone; personalization fills merge slots and falls
    back to empty (baseline) whenever it's missing/weak — never fabricated.
  * Stop conditions re-checked before every step: suppressed/unsubscribed,
    existing customer, paused, completed. Suppression is ALSO re-checked by the
    sender at send time (double-safe).
  * Sequence campaigns inherit CAMPAIGN_DRY_RUN_DEFAULT, so real sends require
    the same explicit dry-run-off decision as manual campaigns.
"""
from __future__ import annotations

import json
import logging
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.emailing import is_suppressed
from app.email_engine import render_for_recipient
from app.models import (
    EmailCampaign,
    EmailCampaignRecipient,
    EmailSequence,
    KvkCompany,
    Personalization,
    SequenceEmail,
    SequenceEnrollment,
    SequenceStep,
)

logger = logging.getLogger(__name__)


def engine_enabled() -> bool:
    return bool(getattr(settings, "sequence_engine_enabled", False))


# ---------------------------------------------------------------------------
# Cadence — next Wednesday 07:00 lead-local (configurable), as UTC.
# ---------------------------------------------------------------------------

def _tz(name: str):
    try:
        from zoneinfo import ZoneInfo  # stdlib 3.9+
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001
        return timezone.utc


def compute_next_send(
    after_utc: datetime, *, weekday: int = 2, hour: int = 7, gap_days: int = 0,
    tz_name: str = "Europe/Amsterdam",
) -> datetime:
    """Return the next send instant (UTC, tz-aware): the first ``weekday`` at
    ``hour``:00 lead-local that is at least ``gap_days`` after ``after_utc``."""
    tz = _tz(tz_name)
    if after_utc.tzinfo is None:
        after_utc = after_utc.replace(tzinfo=timezone.utc)
    local = after_utc.astimezone(tz)
    gapped = local + timedelta(days=gap_days)
    start = gapped.replace(hour=hour, minute=0, second=0, microsecond=0)
    if start < gapped:
        start = start + timedelta(days=1)
    days_ahead = (int(weekday) - start.weekday()) % 7
    send_local = start + timedelta(days=days_ahead)
    return send_local.astimezone(timezone.utc)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_default_sequence(session: Session) -> EmailSequence | None:
    return session.scalar(
        select(EmailSequence).where(
            EmailSequence.is_default.is_(True), EmailSequence.is_active.is_(True)
        ).order_by(EmailSequence.id.asc())
    )


# ---------------------------------------------------------------------------
# Enrollment
# ---------------------------------------------------------------------------

def enroll(
    session: Session, *, sequence: EmailSequence, subject_type: str, subject_id: int,
    to_email: str, company_name: str = "", merge_context: dict | None = None,
    tz_name: str | None = None, created_by: str = "", now: datetime | None = None,
) -> SequenceEnrollment | None:
    """Enroll one lead. Skips duplicates, empty/suppressed emails. Caller commits.

    Returns the enrollment, or None if skipped. Enrollment is just data — it
    will not send anything until the engine is enabled and the step is due.
    """
    to_email = (to_email or "").strip()
    if not to_email:
        return None
    suppressed, _ = is_suppressed(session, to_email, company_name)
    if suppressed:
        return None
    existing = session.scalar(
        select(SequenceEnrollment).where(
            SequenceEnrollment.sequence_id == sequence.id,
            SequenceEnrollment.subject_type == subject_type,
            SequenceEnrollment.subject_id == subject_id,
        )
    )
    if existing is not None:
        return existing  # idempotent — never double-enroll

    now = now or _now()
    tz_name = tz_name or getattr(settings, "sequence_default_timezone", "Europe/Amsterdam")
    step1 = session.scalar(
        select(SequenceStep).where(
            SequenceStep.sequence_id == sequence.id, SequenceStep.step_number == 1
        )
    )
    weekday = step1.send_weekday if step1 else int(getattr(settings, "sequence_send_weekday", 2))
    hour = step1.send_hour_local if step1 else int(getattr(settings, "sequence_send_hour_local", 7))
    next_at = compute_next_send(now, weekday=weekday, hour=hour, gap_days=0, tz_name=tz_name)

    enrollment = SequenceEnrollment(
        sequence_id=sequence.id, subject_type=subject_type, subject_id=subject_id,
        to_email=to_email, company_name=company_name or "",
        merge_context=json.dumps(merge_context or {}),
        current_step=0, sequence_status="active", next_followup_at=next_at,
        timezone=tz_name, created_by=created_by, enrolled_at=now,
    )
    session.add(enrollment)
    session.flush()
    return enrollment


def stop_enrollment(session: Session, enrollment: SequenceEnrollment, reason: str) -> None:
    enrollment.sequence_status = "stopped"
    enrollment.stop_reason = reason
    enrollment.next_followup_at = None


def stop_active_enrollments_for_email(session: Session, email: str, reason: str) -> int:
    """Stop all active enrollments for an email (reply/unsubscribe hook). Caller commits."""
    email = (email or "").strip().lower()
    if not email:
        return 0
    rows = session.scalars(
        select(SequenceEnrollment).where(
            SequenceEnrollment.sequence_status == "active",
            SequenceEnrollment.to_email.ilike(email),
        )
    ).all()
    for e in rows:
        stop_enrollment(session, e, reason)
    return len(rows)


# ---------------------------------------------------------------------------
# Personalization layering (baseline + approved blocks; fail-safe to baseline)
# ---------------------------------------------------------------------------

def _perso_enabled() -> bool:
    return bool(getattr(settings, "personalization_enabled", False))


def _perso_min_conf() -> int:
    try:
        return int(getattr(settings, "personalization_min_confidence", 60))
    except (TypeError, ValueError):
        return 60


def _personalization_merge(session: Session, enrollment: SequenceEnrollment, step_number: int) -> tuple[dict, list[str], str]:
    """Return (slot_values, fields_used, confidence_summary) for one step.

    Progressive: step 1 -> first_line; step 2 -> + angle_block; step 3 -> +
    cta_block. Uses ONLY a usable Personalization row (not generic_fallback, and
    confidence >= min). Otherwise slots stay empty and the baseline stands alone.
    """
    slots = {"first_line": "", "angle_block": "", "cta_block": ""}
    used: list[str] = []
    if not _perso_enabled():
        return slots, used, "personalization off"

    # Prefer a step-specific personalization row; fall back to the general (0) row.
    row = session.scalar(
        select(Personalization).where(
            Personalization.subject_type == enrollment.subject_type,
            Personalization.subject_id == enrollment.subject_id,
            Personalization.sequence_step == step_number,
        )
    )
    if row is None:
        row = session.scalar(
            select(Personalization).where(
                Personalization.subject_type == enrollment.subject_type,
                Personalization.subject_id == enrollment.subject_id,
                Personalization.sequence_step == 0,
            )
        )
    if row is None or row.status == "generic_fallback":
        return slots, used, "no usable personalization"
    min_conf = _perso_min_conf()
    if (row.personalization_confidence or 0) < min_conf and row.status != "approved":
        return slots, used, f"confidence {row.personalization_confidence} < {min_conf}"

    if row.first_line_text:
        slots["first_line"] = row.first_line_text
        used.append("first_line")
    if step_number >= 2 and (row.supporting_fact or row.primary_angle):
        slots["angle_block"] = row.supporting_fact or row.primary_angle
        used.append("angle_block")
    if step_number >= 3 and row.cta_variant and row.cta_variant != "default":
        slots["cta_block"] = row.cta_variant
        used.append("cta_block")
    return slots, used, f"personalization status={row.status} conf={row.personalization_confidence}"


# ---------------------------------------------------------------------------
# Scheduler — process due enrollments (producer -> existing sender)
# ---------------------------------------------------------------------------

def _is_existing_customer(session: Session, enrollment: SequenceEnrollment) -> bool:
    if enrollment.subject_type != "kvk":
        return False
    co = session.get(KvkCompany, enrollment.subject_id)
    return bool(co and co.already_client_flag)


def process_due_enrollments(session: Session, *, now: datetime | None = None, limit: int = 200) -> dict:
    """Send the due step for each active enrollment whose next_followup_at <= now.

    Gated: returns immediately when the engine is disabled. Builds one campaign
    per (sequence, step) batch and hands off to the existing sender. Caller need
    not commit — this commits per batch.
    """
    if not engine_enabled():
        return {"ok": False, "reason": "sequence_engine_disabled", "processed": 0}

    now = now or _now()
    due = session.scalars(
        select(SequenceEnrollment).where(
            SequenceEnrollment.sequence_status == "active",
            SequenceEnrollment.next_followup_at.isnot(None),
            SequenceEnrollment.next_followup_at <= now,
        ).order_by(SequenceEnrollment.next_followup_at.asc()).limit(limit)
    ).all()

    stats = {"processed": 0, "sent_steps": 0, "stopped": 0, "completed": 0, "skipped": 0}
    # group: (sequence_id, step_number) -> list[enrollment]
    groups: dict[tuple[int, int], list[SequenceEnrollment]] = {}
    for e in due:
        stats["processed"] += 1
        # stop conditions (suppression re-checked again by the sender at send)
        suppressed, reason = is_suppressed(session, e.to_email, e.company_name)
        if suppressed:
            stop_enrollment(session, e, f"suppressed:{reason}")
            stats["stopped"] += 1
            continue
        if _is_existing_customer(session, e):
            stop_enrollment(session, e, "existing_customer")
            stats["stopped"] += 1
            continue
        seq = session.get(EmailSequence, e.sequence_id)
        if seq is None or not seq.is_active:
            stats["skipped"] += 1
            continue
        step_number = (e.current_step or 0) + 1
        if step_number > (seq.step_count or 3):
            e.sequence_status = "completed"
            e.next_followup_at = None
            stats["completed"] += 1
            continue
        groups.setdefault((e.sequence_id, step_number), []).append(e)

    session.commit()

    for (sequence_id, step_number), enrollments in groups.items():
        _send_step_batch(session, sequence_id, step_number, enrollments, now)
        stats["sent_steps"] += len(enrollments)
        session.commit()

    stats["ok"] = True
    return stats


def _send_step_batch(
    session: Session, sequence_id: int, step_number: int,
    enrollments: list[SequenceEnrollment], now: datetime,
) -> None:
    """Create one campaign for this (sequence, step) batch + advance enrollments."""
    step = session.scalar(
        select(SequenceStep).where(
            SequenceStep.sequence_id == sequence_id, SequenceStep.step_number == step_number
        )
    )
    if step is None or step.template_id is None:
        return
    from app.models import EmailTemplate
    tpl = session.get(EmailTemplate, step.template_id)
    if tpl is None:
        return

    dry_run = bool(getattr(settings, "campaign_dry_run_default", True))
    campaign = EmailCampaign(
        name=f"[seq {sequence_id} · step {step_number}] {tpl.name}",
        template_id=tpl.id,
        subject=step.subject_override or tpl.subject,
        body_html=tpl.body_html,
        body_text=tpl.body_text,
        audience_type="sequence",
        lead_temperature="cold",
        status="sending",
        dry_run=dry_run,
        sender_alias=settings.gmail_send_as,
        sender_name=settings.gmail_sender_name,
        reply_to=settings.reply_to_email,
        created_by="sequence-engine",
        started_at=now,
    )
    session.add(campaign)
    session.flush()

    count = 0
    for e in enrollments:
        try:
            base = json.loads(e.merge_context or "{}")
        except Exception:  # noqa: BLE001
            base = {}
        slots, used, conf_summary = _personalization_merge(session, e, step_number)
        merge = {**base, **slots, "company_name": base.get("company_name") or e.company_name}
        recipient = EmailCampaignRecipient(
            campaign_id=campaign.id,
            source_type=e.subject_type,
            kvk_company_id=e.subject_id if e.subject_type == "kvk" else None,
            to_email=e.to_email,
            company_name=e.company_name,
            merge_data=json.dumps(merge),
            tracking_token=f"seq-{secrets.token_urlsafe(12)}",
            status="pending",
        )
        session.add(recipient)
        session.flush()

        # Provenance: render now for the SequenceEmail record (the sender will
        # re-render identically at send time).
        try:
            subj, html, text = render_for_recipient(campaign, recipient)
        except Exception:  # noqa: BLE001
            subj, html, text = (campaign.subject, campaign.body_html, campaign.body_text)
        session.add(SequenceEmail(
            enrollment_id=e.id, step_number=step_number, campaign_id=campaign.id,
            recipient_id=recipient.id, scheduled_send_at=now,
            status=("preview" if dry_run else "queued"),
            baseline_template_version=tpl.name,
            personalization_version=conf_summary,
            rendered_subject=subj, rendered_body_html=html, rendered_body_text=text,
            personalization_fields_used=json.dumps(used),
            confidence_summary=conf_summary,
        ))

        # Advance the enrollment to the next step (or complete).
        seq = session.get(EmailSequence, sequence_id)
        e.current_step = step_number
        e.last_sent_at = now
        if step_number >= (seq.step_count or 3):
            e.sequence_status = "completed"
            e.next_followup_at = None
        else:
            nxt = session.scalar(
                select(SequenceStep).where(
                    SequenceStep.sequence_id == sequence_id,
                    SequenceStep.step_number == step_number + 1,
                )
            )
            gap = nxt.gap_days if nxt else int(getattr(settings, "sequence_step_gap_days", 7))
            wd = nxt.send_weekday if nxt else int(getattr(settings, "sequence_send_weekday", 2))
            hr = nxt.send_hour_local if nxt else int(getattr(settings, "sequence_send_hour_local", 7))
            e.next_followup_at = compute_next_send(now, weekday=wd, hour=hr, gap_days=gap, tz_name=e.timezone)
        count += 1

    campaign.total_recipients = count


# ---------------------------------------------------------------------------
# Background scheduler daemon (gated; idempotent — mirrors the email sender)
# ---------------------------------------------------------------------------

_scheduler_started = False
_scheduler_lock = threading.Lock()


def start_sequence_scheduler() -> None:
    """Start the sequence scheduler daemon, but ONLY when the engine is enabled.

    With the flag off this is a no-op (no thread, no work). Idempotent.
    """
    global _scheduler_started
    if not engine_enabled():
        return
    with _scheduler_lock:
        if _scheduler_started:
            return
        _scheduler_started = True

    def _loop() -> None:
        from app.db import SessionLocal
        interval = max(60, int(getattr(settings, "sequence_scheduler_interval", 300)))
        while True:
            try:
                s = SessionLocal()
                try:
                    process_due_enrollments(s)
                finally:
                    s.close()
            except Exception as exc:  # noqa: BLE001 - never let the daemon die
                logger.info("sequence scheduler tick failed: %s", exc)
            time.sleep(interval)

    threading.Thread(target=_loop, daemon=True, name="sequence-scheduler").start()
