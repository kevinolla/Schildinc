"""Phase 2 lead-scoring tests — explainable, deterministic, never approves."""
from __future__ import annotations

import json

from sqlalchemy import select

from app import lead_scoring
from app.lead_scoring import compute_lead_score, persist_lead_score
from app.models import LeadScore

_VALID_PRIORITIES = {"high", "medium", "low", "manual_review", "exclude"}


def test_already_client_is_excluded():
    r = compute_lead_score(already_client=True, has_website=True, website_confidence=100)
    assert r.outreach_priority == "exclude"
    assert r.sample_pack_eligibility == "no"
    assert any("client" in reason for reason in r.reasons)


def test_low_fit_tier_excluded_and_brand_store_reviewed():
    assert compute_lead_score(bike_tier="Low Fit", has_website=True).outreach_priority == "exclude"
    assert compute_lead_score(bike_tier="Brand Store", has_website=True).outreach_priority == "manual_review"


def test_high_quality_shop_gets_high_priority_and_sample_yes():
    r = compute_lead_score(
        bike_tier="Good Tier", has_website=True, website_confidence=100, has_phone=True,
        trusted_facts={"premium_brand_signal", "workshop_focus", "public_store_quality_signal"},
    )
    assert r.store_quality_score >= 70
    assert r.outreach_priority == "high"
    assert r.sample_pack_eligibility == "yes"
    assert r.call_followup_eligibility == "yes"
    assert r.commercial_potential in {"big", "medium", "multi_store", "chain"}


def test_outreach_priority_is_always_a_label_never_an_approval():
    r = compute_lead_score(has_website=True, website_confidence=100, trusted_facts={"premium_brand_signal"})
    assert r.outreach_priority in _VALID_PRIORITIES
    # The result object exposes NO approval field of any kind.
    assert not hasattr(r, "approved_for_outreach")
    assert not hasattr(r, "approved")


def test_low_confidence_facts_do_not_influence_score():
    # Same premium signal, once trusted, once only review-required.
    trusted = compute_lead_score(has_website=True, website_confidence=100, trusted_facts={"premium_brand_signal"})
    review_only = compute_lead_score(has_website=True, website_confidence=100, review_facts={"premium_brand_signal"})
    assert trusted.store_quality_score > review_only.store_quality_score


def test_scoring_is_deterministic():
    a = compute_lead_score(bike_tier="Good Tier", has_website=True, website_confidence=90, trusted_facts={"workshop_focus"})
    b = compute_lead_score(bike_tier="Good Tier", has_website=True, website_confidence=90, trusted_facts={"workshop_focus"})
    assert a.input_fingerprint == b.input_fingerprint
    assert a.store_quality_score == b.store_quality_score
    assert a.outreach_priority == b.outreach_priority


def test_persist_lead_score_upserts_and_records_reasons(db_session):
    r = compute_lead_score(bike_tier="Good Tier", has_website=True, website_confidence=100,
                           trusted_facts={"premium_brand_signal"})
    persist_lead_score(db_session, "kvk", 42, r)
    db_session.commit()
    row = db_session.scalar(select(LeadScore))
    assert row.subject_id == 42
    assert row.outreach_priority == r.outreach_priority
    assert json.loads(row.reasons)  # explainable reasons stored


def test_persist_respects_manual_override(db_session):
    r1 = compute_lead_score(has_website=True, website_confidence=100, trusted_facts={"premium_brand_signal"})
    persist_lead_score(db_session, "kvk", 7, r1)
    db_session.commit()
    row = db_session.scalar(select(LeadScore).where(LeadScore.subject_id == 7))
    row.manual_override = True
    row.outreach_priority = "high"
    db_session.commit()

    # A recompute with different inputs must NOT overwrite a human-frozen row.
    r2 = compute_lead_score(already_client=True)  # would be 'exclude'
    persist_lead_score(db_session, "kvk", 7, r2)
    db_session.commit()
    db_session.refresh(row)
    assert row.manual_override is True
    assert row.outreach_priority == "high"  # unchanged
