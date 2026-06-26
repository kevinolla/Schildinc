"""Phase 3B sequence-engine tests — cadence, stops, progression, render safety."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import select

from app import sequences
from app.models import (
    Personalization,
    SequenceEmail,
    SequenceEnrollment,
    SuppressionEntry,
)
from app.sequence_library import seed_sequence_templates


def _tz(name="Europe/Amsterdam"):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        return timezone.utc


# ── cadence ──────────────────────────────────────────────────────────────────
def test_compute_next_send_is_wednesday_0700_local():
    # Enroll on a Monday 09:00 UTC -> first send must be a Wednesday 07:00 local.
    after = datetime(2026, 6, 22, 9, 0, tzinfo=timezone.utc)  # Monday
    out = sequences.compute_next_send(after, weekday=2, hour=7, gap_days=0, tz_name="Europe/Amsterdam")
    local = out.astimezone(_tz())
    assert local.weekday() == 2      # Wednesday
    assert local.hour == 7 and local.minute == 0
    assert out > after


def test_compute_next_send_step_gap_is_a_week_later():
    first = datetime(2026, 6, 24, 5, 0, tzinfo=timezone.utc)  # a Wednesday ~07:00 local
    second = sequences.compute_next_send(first, weekday=2, hour=7, gap_days=7, tz_name="Europe/Amsterdam")
    assert (second - first).days >= 6
    assert second.astimezone(_tz()).weekday() == 2


# ── enrollment ───────────────────────────────────────────────────────────────
def test_enroll_creates_active_enrollment_and_is_idempotent(db_session):
    seq = seed_sequence_templates(db_session)
    db_session.commit()
    e1 = sequences.enroll(db_session, sequence=seq, subject_type="kvk", subject_id=1,
                          to_email="info@shop.nl", company_name="Shop BV")
    db_session.commit()
    assert e1.sequence_status == "active"
    assert e1.current_step == 0
    assert e1.next_followup_at is not None
    e2 = sequences.enroll(db_session, sequence=seq, subject_type="kvk", subject_id=1,
                          to_email="info@shop.nl", company_name="Shop BV")
    assert e2.id == e1.id              # idempotent


def test_enroll_skips_suppressed_email(db_session):
    seq = seed_sequence_templates(db_session)
    db_session.add(SuppressionEntry(email="no@shop.nl", active=True, reason="unsub"))
    db_session.commit()
    e = sequences.enroll(db_session, sequence=seq, subject_type="kvk", subject_id=2,
                         to_email="no@shop.nl", company_name="No BV")
    assert e is None


# ── scheduler / progression ──────────────────────────────────────────────────
def _due_now(db_session, seq, subject_id, email, monkeypatch):
    monkeypatch.setattr(sequences, "engine_enabled", lambda: True)
    e = sequences.enroll(db_session, sequence=seq, subject_type="kvk", subject_id=subject_id,
                         to_email=email, company_name="Shop BV",
                         merge_context={"company_name": "Shop BV", "city": "Amsterdam"})
    e.next_followup_at = datetime(2020, 1, 1, tzinfo=timezone.utc)  # force due
    db_session.commit()
    return e


def test_full_step_progression_1_2_3_then_completed(db_session, monkeypatch):
    seq = seed_sequence_templates(db_session)
    db_session.commit()
    e = _due_now(db_session, seq, 10, "info@progress.nl", monkeypatch)

    for expected_step in (1, 2, 3):
        sequences.process_due_enrollments(db_session, now=datetime(2026, 6, 24, 5, 0, tzinfo=timezone.utc))
        db_session.refresh(e)
        assert e.current_step == expected_step
        if expected_step < 3:
            assert e.sequence_status == "active"
            e.next_followup_at = datetime(2020, 1, 1, tzinfo=timezone.utc)  # force next due
            db_session.commit()
    assert e.sequence_status == "completed"
    assert e.next_followup_at is None
    emails = db_session.scalars(select(SequenceEmail).where(SequenceEmail.enrollment_id == e.id)).all()
    assert sorted(x.step_number for x in emails) == [1, 2, 3]


def test_stop_on_suppression_before_send(db_session, monkeypatch):
    seq = seed_sequence_templates(db_session)
    db_session.commit()
    e = _due_now(db_session, seq, 11, "stop@shop.nl", monkeypatch)
    db_session.add(SuppressionEntry(email="stop@shop.nl", active=True, reason="unsub"))
    db_session.commit()
    sequences.process_due_enrollments(db_session, now=datetime(2026, 6, 24, 5, 0, tzinfo=timezone.utc))
    db_session.refresh(e)
    assert e.sequence_status == "stopped"
    assert "suppressed" in e.stop_reason


def test_disabled_engine_does_nothing(db_session, monkeypatch):
    seq = seed_sequence_templates(db_session)
    db_session.commit()
    monkeypatch.setattr(sequences, "engine_enabled", lambda: False)
    res = sequences.process_due_enrollments(db_session, now=datetime(2026, 6, 24, 5, 0, tzinfo=timezone.utc))
    assert res["ok"] is False


# ── render: baseline stands alone; personalization layers; no fabrication ─────
def test_baseline_renders_without_personalization(db_session, monkeypatch):
    seq = seed_sequence_templates(db_session)
    db_session.commit()
    e = _due_now(db_session, seq, 12, "info@base.nl", monkeypatch)
    monkeypatch.setattr(sequences, "_perso_enabled", lambda: False)
    sequences.process_due_enrollments(db_session, now=datetime(2026, 6, 24, 5, 0, tzinfo=timezone.utc))
    em = db_session.scalar(select(SequenceEmail).where(SequenceEmail.enrollment_id == e.id))
    assert "Schild Inc" in em.rendered_body_html
    assert "{{" not in em.rendered_body_html          # all merge slots resolved
    assert json.loads(em.personalization_fields_used) == []


def test_personalization_layers_when_present(db_session, monkeypatch):
    seq = seed_sequence_templates(db_session)
    db_session.add(Personalization(subject_type="kvk", subject_id=13, sequence_step=0,
                                   first_line_text="Mooie winkel in Amsterdam!",
                                   personalization_confidence=85, status="draft"))
    db_session.commit()
    e = _due_now(db_session, seq, 13, "info@perso.nl", monkeypatch)
    monkeypatch.setattr(sequences, "_perso_enabled", lambda: True)
    sequences.process_due_enrollments(db_session, now=datetime(2026, 6, 24, 5, 0, tzinfo=timezone.utc))
    em = db_session.scalar(select(SequenceEmail).where(SequenceEmail.enrollment_id == e.id))
    assert "Mooie winkel in Amsterdam!" in em.rendered_body_html
    assert "first_line" in json.loads(em.personalization_fields_used)


def test_reply_stops_active_enrollment(db_session):
    seq = seed_sequence_templates(db_session)
    e = sequences.enroll(db_session, sequence=seq, subject_type="kvk", subject_id=20,
                         to_email="replier@shop.nl", company_name="Replier BV")
    db_session.commit()
    n = sequences.stop_active_enrollments_for_email(db_session, "replier@shop.nl", "reply")
    db_session.commit()
    db_session.refresh(e)
    assert n == 1
    assert e.sequence_status == "stopped"
    assert e.stop_reason == "reply"


def test_per_step_personalization_prefers_step_specific_row(db_session, monkeypatch):
    seq = seed_sequence_templates(db_session)
    # A general (step 0) row AND a step-2-specific row for the same lead.
    db_session.add(Personalization(subject_type="kvk", subject_id=21, sequence_step=0,
                                   first_line_text="GENERAL line", personalization_confidence=85, status="draft"))
    db_session.add(Personalization(subject_type="kvk", subject_id=21, sequence_step=2,
                                   first_line_text="STEP2 specific line", personalization_confidence=85, status="draft"))
    db_session.commit()
    e = sequences.enroll(db_session, sequence=seq, subject_type="kvk", subject_id=21,
                         to_email="info@perstep.nl", company_name="PerStep BV")
    db_session.commit()
    monkeypatch.setattr(sequences, "_perso_enabled", lambda: True)
    slots1, _, _ = sequences._personalization_merge(db_session, e, 1)
    slots2, _, _ = sequences._personalization_merge(db_session, e, 2)
    assert slots1["first_line"] == "GENERAL line"        # step 1 -> general row
    assert slots2["first_line"] == "STEP2 specific line"  # step 2 -> step-specific row


def test_low_confidence_personalization_falls_back_to_baseline(db_session, monkeypatch):
    seq = seed_sequence_templates(db_session)
    db_session.add(Personalization(subject_type="kvk", subject_id=14, sequence_step=0,
                                   first_line_text="Weak line",
                                   personalization_confidence=30, status="draft"))
    db_session.commit()
    e = _due_now(db_session, seq, 14, "info@weak.nl", monkeypatch)
    monkeypatch.setattr(sequences, "_perso_enabled", lambda: True)
    sequences.process_due_enrollments(db_session, now=datetime(2026, 6, 24, 5, 0, tzinfo=timezone.utc))
    em = db_session.scalar(select(SequenceEmail).where(SequenceEmail.enrollment_id == e.id))
    assert "Weak line" not in em.rendered_body_html   # low-confidence not used
    assert json.loads(em.personalization_fields_used) == []
