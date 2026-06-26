"""Phase 3A personalization tests — grounded, gated, never auto-approves.

All offline: personalization._call_llm is monkeypatched, so no network/API key.
"""
from __future__ import annotations

import json

from sqlalchemy import select

from app import personalization as p
from app.models import Personalization


def _enable(monkeypatch, llm_return):
    monkeypatch.setattr(p, "personalization_enabled", lambda: True)
    monkeypatch.setattr(p, "_api_key", lambda: "test-key")
    monkeypatch.setattr(p, "_call_llm", lambda system, user, model: llm_return)


_FACTS = {"premium_brand_signal": "Gazelle, Batavus"}
_GOOD_LLM = {
    "first_line": "Mooie winkel — en Gazelle is een sterk merk om te voeren.",
    "primary_angle": "premium brands",
    "cta_suggestion": "gratis ontwerp",
    "internal_sales_note": "Carries Gazelle/Batavus; lead with premium angle.",
    "supporting_fact": "premium brands carried",
    "facts_used": ["premium_brand_signal"],
    "confidence": 85,
}


def test_disabled_returns_generic_without_calling_llm(monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(p, "personalization_enabled", lambda: False)
    monkeypatch.setattr(p, "_call_llm", lambda *a, **k: called.__setitem__("n", called["n"] + 1) or {})
    r = p.build_personalization(company_name="Velo BV", trusted_facts=_FACTS)
    assert r.status == p.STATUS_GENERIC
    assert r.first_line_text == ""        # baseline opener used
    assert called["n"] == 0                # LLM never called when disabled


def test_no_api_key_is_generic(monkeypatch):
    monkeypatch.setattr(p, "personalization_enabled", lambda: True)
    monkeypatch.setattr(p, "_api_key", lambda: "")
    r = p.build_personalization(company_name="Velo BV", trusted_facts=_FACTS)
    assert r.status == p.STATUS_GENERIC


def test_no_trusted_facts_is_generic(monkeypatch):
    _enable(monkeypatch, _GOOD_LLM)
    r = p.build_personalization(company_name="Velo BV", trusted_facts={})
    assert r.status == p.STATUS_GENERIC
    assert "no trusted facts" in r.internal_sales_note


def test_happy_path_is_grounded_draft(monkeypatch):
    _enable(monkeypatch, _GOOD_LLM)
    r = p.build_personalization(company_name="Velo BV", city="Amsterdam", bike_tier="Good Tier", trusted_facts=_FACTS)
    assert r.status == p.STATUS_DRAFT
    assert r.personalization_confidence == 85
    assert r.facts_used == ["premium_brand_signal"]
    assert "Gazelle" in r.first_line_text


def test_hallucination_guard_rejects_ungrounded_fact(monkeypatch):
    bad = dict(_GOOD_LLM, facts_used=["multi_location_signal"])  # not provided
    _enable(monkeypatch, bad)
    r = p.build_personalization(company_name="Velo BV", trusted_facts=_FACTS)
    assert r.status == p.STATUS_GENERIC
    assert r.personalization_confidence == 0
    assert "ungrounded" in r.internal_sales_note


def test_low_confidence_falls_back_to_generic(monkeypatch):
    weak = dict(_GOOD_LLM, confidence=30)
    _enable(monkeypatch, weak)
    r = p.build_personalization(company_name="Velo BV", trusted_facts=_FACTS)
    assert r.status == p.STATUS_GENERIC


def test_llm_error_falls_back_to_generic(monkeypatch):
    monkeypatch.setattr(p, "personalization_enabled", lambda: True)
    monkeypatch.setattr(p, "_api_key", lambda: "k")

    def boom(*a, **k):
        raise RuntimeError("api down")

    monkeypatch.setattr(p, "_call_llm", boom)
    r = p.build_personalization(company_name="Velo BV", trusted_facts=_FACTS)
    assert r.status == p.STATUS_GENERIC


def test_never_auto_approves(monkeypatch):
    _enable(monkeypatch, _GOOD_LLM)
    r = p.build_personalization(company_name="Velo BV", trusted_facts=_FACTS)
    assert r.status in {p.STATUS_DRAFT, p.STATUS_GENERIC}
    assert r.status != "approved"
    assert not hasattr(r, "approved")


def test_budget_breaker_blocks_when_limit_zero(monkeypatch):
    _enable(monkeypatch, _GOOD_LLM)
    monkeypatch.setattr(p, "_daily_limit", lambda: 0)
    r = p.build_personalization(company_name="Velo BV", trusted_facts=_FACTS)
    assert r.status == p.STATUS_GENERIC
    assert "budget" in r.internal_sales_note


def test_persist_then_human_approval_not_clobbered(monkeypatch, db_session):
    _enable(monkeypatch, _GOOD_LLM)
    r = p.build_personalization(company_name="Velo BV", trusted_facts=_FACTS)
    p.persist_personalization(db_session, "kvk", 11, r)
    db_session.commit()
    row = db_session.scalar(select(Personalization))
    row.status = "approved"
    row.first_line_text = "Human edited line"
    db_session.commit()

    # Re-generate + persist must NOT overwrite the human-approved row.
    r2 = p.build_personalization(company_name="Velo BV", trusted_facts=_FACTS)
    p.persist_personalization(db_session, "kvk", 11, r2)
    db_session.commit()
    db_session.refresh(row)
    assert row.status == "approved"
    assert row.first_line_text == "Human edited line"
    assert json.loads(row.facts_used) == ["premium_brand_signal"]
