"""Tests for app.enrichment_facts.persist_facts — provenance + review gating."""
from __future__ import annotations

from sqlalchemy import select

from app import enrichment_facts as ef
from app.models import EnrichmentFact


def test_high_confidence_fact_is_autotrusted(db_session):
    rows = ef.persist_facts(
        db_session, "kvk", 1,
        [{"field_name": "premium_brands", "extracted_value": "Gazelle, Cube",
          "source_url": "https://shop.nl/about", "extraction_method": "web_extract",
          "confidence": 95}],
        autotrust_min=80,
    )
    db_session.commit()
    assert len(rows) == 1
    fact = db_session.scalar(select(EnrichmentFact))
    assert fact.confidence == 95
    assert not fact.review_required          # >= threshold -> usable automatically
    assert fact.source_url == "https://shop.nl/about"


def test_low_confidence_fact_requires_review(db_session):
    ef.persist_facts(
        db_session, "kvk", 2,
        [{"field_name": "multi_location", "extracted_value": "yes",
          "source_url": "https://shop.nl", "confidence": 40}],
        autotrust_min=80,
    )
    db_session.commit()
    fact = db_session.scalar(select(EnrichmentFact))
    assert fact.review_required              # below threshold -> parked for review


def test_persist_facts_is_idempotent_upsert(db_session):
    payload = [{"field_name": "workshop_focus", "extracted_value": "v1",
                "source_url": "https://shop.nl", "confidence": 90}]
    ef.persist_facts(db_session, "kvk", 3, payload, autotrust_min=80)
    db_session.commit()
    payload[0]["extracted_value"] = "v2"
    ef.persist_facts(db_session, "kvk", 3, payload, autotrust_min=80)
    db_session.commit()

    rows = db_session.scalars(
        select(EnrichmentFact).where(EnrichmentFact.subject_id == 3)
    ).all()
    assert len(rows) == 1                    # same (subject, field, source) -> updated, not duplicated
    assert rows[0].extracted_value == "v2"


def test_fact_without_field_name_is_skipped(db_session):
    rows = ef.persist_facts(
        db_session, "kvk", 4,
        [{"extracted_value": "orphan", "confidence": 99}],
        autotrust_min=80,
    )
    db_session.commit()
    assert rows == []
    assert db_session.scalar(select(EnrichmentFact)) is None


def test_reviewed_fact_not_reopened_on_reupsert(db_session):
    ef.persist_facts(
        db_session, "kvk", 5,
        [{"field_name": "awards", "extracted_value": "x", "source_url": "https://s.nl", "confidence": 30}],
        autotrust_min=80,
    )
    db_session.commit()
    fact = db_session.scalar(select(EnrichmentFact))
    fact.reviewed_by = "owner"              # a human cleared it
    fact.review_required = False
    db_session.commit()

    # Re-running discovery with a still-low confidence must NOT re-open it.
    ef.persist_facts(
        db_session, "kvk", 5,
        [{"field_name": "awards", "extracted_value": "x2", "source_url": "https://s.nl", "confidence": 30}],
        autotrust_min=80,
    )
    db_session.commit()
    db_session.refresh(fact)
    assert not fact.review_required
