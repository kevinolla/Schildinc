"""Explainable, reviewable lead scoring (DESIGN_V2 Phase 2, C).

A deterministic, rules-based scorer — NO LLM, NO randomness — that grades a
company into five dimensions plus a read-only mirror of the existing bike tier:

  * store_quality_score        0-100
  * commercial_potential       small | medium | big | multi_store | chain
  * outreach_priority          high | medium | low | manual_review | exclude
  * sample_pack_eligibility    yes | no | review
  * call_followup_eligibility  yes | no

Invariants (from the brief):
  * Scoring NEVER approves outreach — outreach_priority is a priority label, not
    an approval. There is no path here that sets any "approved_for_outreach".
  * Existing bike-tier logic is PRESERVED, not replaced — we only mirror the
    tier and let it gate (Low Fit -> exclude; Brand Store / Hard to Reach ->
    manual_review).
  * Only TRUSTED facts (confidence >= autotrust, i.e. review_required=False)
    influence the score. Low-confidence facts are passed as ``review_facts`` and
    deliberately given little/no weight so they can't move the score.
  * Every decision is explainable: ``reasons`` lists the signals that fired.

``compute_lead_score`` is pure. ``persist_lead_score`` upserts a LeadScore row
(caller commits) and respects manual_override.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import LeadScore

# Tiers that mean "don't pursue" / "handle specially" — preserved legacy logic.
_EXCLUDE_TIERS = {"low fit"}
_REVIEW_TIERS = {"brand store", "hard to reach"}


@dataclass
class LeadScoreResult:
    store_quality_score: int = 0
    commercial_potential: str = "small"
    outreach_priority: str = "low"
    sample_pack_eligibility: str = "review"
    call_followup_eligibility: str = "no"
    bike_tier: str = ""
    reasons: list[str] = field(default_factory=list)
    input_fingerprint: str = ""


def _fingerprint(*parts) -> str:
    raw = "|".join(str(p) for p in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def compute_lead_score(
    *,
    already_client: bool = False,
    bike_tier: str = "",
    sector_relevant: bool = True,
    has_website: bool = False,
    website_confidence: int = 0,
    has_phone: bool = False,
    trusted_facts: frozenset[str] | set[str] = frozenset(),
    review_facts: frozenset[str] | set[str] = frozenset(),
) -> LeadScoreResult:
    """Grade one company. Pure + deterministic. ``trusted_facts`` /
    ``review_facts`` are sets of ``enrichment_facts`` field_names (trusted =
    review_required False). Only trusted facts move the score."""
    tier_norm = str(bike_tier or "").strip().lower()
    trusted = set(trusted_facts or ())
    reasons: list[str] = []

    # ---- store_quality_score (0-100), explainable additive model -----------
    quality = 0
    if has_website and website_confidence >= _autopick():
        quality += 35
        reasons.append("accepted own website (+35)")
    elif has_website:
        quality += 15
        reasons.append("website present but unverified (+15)")
    if "premium_brand_signal" in trusted:
        quality += 25
        reasons.append("premium brands carried (+25)")
    if "workshop_focus" in trusted:
        quality += 10
        reasons.append("workshop/repair focus (+10)")
    if "service_focus" in trusted:
        quality += 8
        reasons.append("service focus (+8)")
    if "accessories_focus" in trusted:
        quality += 6
        reasons.append("accessories range (+6)")
    if "public_store_quality_signal" in trusted:
        quality += 12
        reasons.append("public quality/dealer signal (+12)")
    if "public_store_fact" in trusted:
        quality += 4
        reasons.append("established/heritage signal (+4)")
    if not sector_relevant:
        quality = max(0, quality - 20)
        reasons.append("sector not core (-20)")
    quality = max(0, min(100, quality))

    # ---- commercial_potential ----------------------------------------------
    if "chain_or_hq_signal" in trusted:
        commercial = "chain"
        reasons.append("chain/HQ signal -> chain")
    elif "multi_location_signal" in trusted:
        commercial = "multi_store"
        reasons.append("multi-location signal -> multi_store")
    elif quality >= 65:
        commercial = "big"
    elif quality >= 40:
        commercial = "medium"
    else:
        commercial = "small"

    # ---- outreach_priority (NEVER an approval) ------------------------------
    if already_client:
        priority = "exclude"
        reasons.append("already a client -> exclude")
    elif tier_norm in _EXCLUDE_TIERS:
        priority = "exclude"
        reasons.append(f"bike tier '{bike_tier}' -> exclude")
    elif tier_norm in _REVIEW_TIERS:
        priority = "manual_review"
        reasons.append(f"bike tier '{bike_tier}' -> manual review")
    elif not has_website:
        priority = "manual_review"
        reasons.append("no accepted website -> manual review")
    elif "repair_first_signal" in trusted and quality < 45:
        priority = "manual_review"
        reasons.append("repair-first, low quality -> manual review")
    elif quality >= 70:
        priority = "high"
    elif quality >= 45:
        priority = "medium"
    else:
        priority = "low"

    # ---- sample_pack_eligibility -------------------------------------------
    label_angle = sector_relevant and ("premium_brand_signal" in trusted or "public_store_quality_signal" in trusted)
    if priority == "exclude":
        sample = "no"
    elif priority == "high" and label_angle:
        sample = "yes"
        reasons.append("high priority + strong label angle -> sample pack yes")
    else:
        sample = "review"

    # ---- call_followup_eligibility -----------------------------------------
    call = "yes" if (has_phone and priority in {"high", "medium"}) else "no"
    if call == "yes":
        reasons.append("phone present + priority -> call follow-up yes")

    fp = _fingerprint(
        already_client, tier_norm, sector_relevant, has_website, website_confidence,
        has_phone, sorted(trusted), getattr(settings, "lead_scoring_engine_version", 1),
    )

    return LeadScoreResult(
        store_quality_score=quality,
        commercial_potential=commercial,
        outreach_priority=priority,
        sample_pack_eligibility=sample,
        call_followup_eligibility=call,
        bike_tier=str(bike_tier or ""),
        reasons=reasons,
        input_fingerprint=fp,
    )


def _autopick() -> int:
    try:
        return int(getattr(settings, "discovery_autopick_score", 80) or 80)
    except (TypeError, ValueError):
        return 80


def scoring_enabled() -> bool:
    return bool(getattr(settings, "lead_scoring_enabled", False))


def persist_lead_score(
    session: Session, subject_type: str, subject_id: int, result: LeadScoreResult
) -> LeadScore | None:
    """Upsert the LeadScore row for a subject. Caller commits.

    Respects manual_override (a human-frozen row is never overwritten) and skips
    a recompute when the input_fingerprint is unchanged (idempotent)."""
    subject_type = (subject_type or "").strip().lower()
    row = session.scalar(
        select(LeadScore).where(
            LeadScore.subject_type == subject_type,
            LeadScore.subject_id == subject_id,
        )
    )
    if row is not None and row.manual_override:
        return row  # human-frozen — never clobber
    if row is not None and row.input_fingerprint == result.input_fingerprint:
        return row  # unchanged inputs — nothing to do

    if row is None:
        row = LeadScore(subject_type=subject_type, subject_id=subject_id)
        session.add(row)

    row.store_quality_score = result.store_quality_score
    row.commercial_potential = result.commercial_potential
    row.outreach_priority = result.outreach_priority
    row.sample_pack_eligibility = result.sample_pack_eligibility
    row.call_followup_eligibility = result.call_followup_eligibility
    row.bike_tier = result.bike_tier
    row.reasons = json.dumps(result.reasons)
    row.engine_version = int(getattr(settings, "lead_scoring_engine_version", 1))
    row.input_fingerprint = result.input_fingerprint
    row.updated_at = datetime.utcnow()
    session.flush()
    return row
