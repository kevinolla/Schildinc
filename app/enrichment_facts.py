"""Enrichment facts — provenance-first storage for discovered company facts.

DESIGN_V2 foundation phase. This module is the write path for the
``enrichment_facts`` table. It is intentionally small and side-effect-light:
``persist_facts`` adds/updates ORM rows and flushes, but the CALLER commits
(mirroring the "pure core, caller persists" rule used by discovery_open).

Guardrails baked in here:
  * Every fact must carry a ``field_name``; rows without one are skipped.
  * ``review_required`` is derived from confidence vs ``fact_autotrust_min`` —
    a low-confidence fact is parked for human review and must NOT be treated as
    truth or used in outreach until cleared.
  * Idempotent on (subject_type, subject_id, field_name, source_url): re-running
    discovery updates the existing row instead of creating duplicates.

Nothing in production calls ``persist_facts`` yet — extraction callers are a
later phase, gated by ``settings.discovery_facts_enabled`` (see ``facts_enabled``).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import EnrichmentFact

# Subject kinds an enrichment fact may attach to (polymorphic, no FK).
SUBJECT_TYPES = {"kvk", "prospect", "contact"}


def facts_enabled() -> bool:
    """Whether enrichment-fact EXTRACTION is turned on. Off by default."""
    return bool(getattr(settings, "discovery_facts_enabled", False))


def _autotrust_min(override: int | None) -> int:
    if override is not None:
        return int(override)
    return int(getattr(settings, "fact_autotrust_min", 80))


def persist_facts(
    session: Session,
    subject_type: str,
    subject_id: int,
    facts: list[dict],
    *,
    autotrust_min: int | None = None,
) -> list[EnrichmentFact]:
    """Upsert a batch of discovered facts for one subject. Caller commits.

    Each ``facts`` item is a dict with keys: ``field_name`` (required),
    ``extracted_value``, ``source_url``, ``extraction_method``, ``confidence``.
    Returns the persisted/updated ORM objects (flushed, not committed).
    """
    threshold = _autotrust_min(autotrust_min)
    subject_type = (subject_type or "").strip().lower()
    out: list[EnrichmentFact] = []

    for raw in facts or []:
        field_name = (raw.get("field_name") or "").strip()
        if not field_name:
            continue  # a fact with no field is not a fact
        source_url = (raw.get("source_url") or "").strip()
        value = raw.get("extracted_value") or ""
        method = (raw.get("extraction_method") or "").strip()
        try:
            confidence = int(raw.get("confidence") or 0)
        except (TypeError, ValueError):
            confidence = 0
        review_required = confidence < threshold

        existing = session.scalar(
            select(EnrichmentFact).where(
                EnrichmentFact.subject_type == subject_type,
                EnrichmentFact.subject_id == subject_id,
                EnrichmentFact.field_name == field_name,
                EnrichmentFact.source_url == source_url,
            )
        )
        if existing is not None:
            existing.extracted_value = value
            existing.extraction_method = method
            existing.confidence = confidence
            # Don't silently re-open a fact a human already reviewed/cleared.
            if not existing.reviewed_by:
                existing.review_required = review_required
            existing.extracted_at = datetime.utcnow()
            existing.updated_at = datetime.utcnow()
            out.append(existing)
        else:
            fact = EnrichmentFact(
                subject_type=subject_type,
                subject_id=subject_id,
                field_name=field_name,
                extracted_value=value,
                source_url=source_url,
                extraction_method=method,
                confidence=confidence,
                review_required=review_required,
            )
            session.add(fact)
            out.append(fact)

    session.flush()
    return out
