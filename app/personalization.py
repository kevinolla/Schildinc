"""AI-assisted cold-outreach personalization (DESIGN_V2 Phase 3A).

Generates four short, controlled outputs for a lead — first-line, outreach
angle, CTA suggestion, internal sales note — from ONLY trusted, verifiable
inputs. It is deliberately conservative:

  * Inputs are restricted to: trusted enrichment facts (review_required=False),
    accepted website, bike tier, lead score, and company/city/sector data.
  * NEVER fabricates facts. A hallucination guard rejects any AI output that
    references a fact we did not supply -> falls back to generic copy.
  * NEVER auto-approves outreach. Output status is draft / needs_review /
    generic_fallback; a human approves before anything sends.
  * FAILS OPEN. No API key, no trusted facts, low confidence, over budget, or
    any error -> a safe GENERIC result (no AI claims) so the baseline copy is
    always usable.
  * OFF by default (settings.personalization_enabled) and a no-op without
    ANTHROPIC_API_KEY.

``build_personalization`` is the entry point. ``_call_llm`` is the only network
hop and is monkeypatchable in tests, so the whole module is testable offline.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Personalization

logger = logging.getLogger(__name__)

STATUS_DRAFT = "draft"
STATUS_GENERIC = "generic_fallback"

# UTC-day call breaker (cost cap), mirroring the Brave breaker pattern.
_BREAKER_LOCK = threading.Lock()
_BREAKER = {"day": "", "count": 0}


# --------------------------------------------------------------------------
# gates / config helpers (monkeypatchable in tests)
# --------------------------------------------------------------------------

def personalization_enabled() -> bool:
    return bool(getattr(settings, "personalization_enabled", False))


def _api_key() -> str:
    return str(getattr(settings, "anthropic_api_key", "") or "").strip()


def _min_confidence() -> int:
    try:
        return int(getattr(settings, "personalization_min_confidence", 60))
    except (TypeError, ValueError):
        return 60


def _max_facts() -> int:
    try:
        return max(1, int(getattr(settings, "personalization_max_facts", 3)))
    except (TypeError, ValueError):
        return 3


def _daily_limit() -> int:
    try:
        return max(0, int(getattr(settings, "personalization_daily_limit", 200)))
    except (TypeError, ValueError):
        return 200


def _utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _budget_ok() -> bool:
    """True if we are under today's call cap. Increments on success via _spend()."""
    limit = _daily_limit()
    if limit <= 0:
        return False
    with _BREAKER_LOCK:
        if _BREAKER["day"] != _utc_day():
            _BREAKER["day"] = _utc_day()
            _BREAKER["count"] = 0
        return _BREAKER["count"] < limit


def _spend() -> None:
    with _BREAKER_LOCK:
        if _BREAKER["day"] != _utc_day():
            _BREAKER["day"] = _utc_day()
            _BREAKER["count"] = 0
        _BREAKER["count"] += 1


# --------------------------------------------------------------------------
# value object
# --------------------------------------------------------------------------

@dataclass
class PersonalizationResult:
    first_line_text: str = ""
    primary_angle: str = ""
    supporting_fact: str = ""
    cta_variant: str = ""
    internal_sales_note: str = ""
    personalization_confidence: int = 0
    source_summary: str = ""
    facts_used: list[str] = field(default_factory=list)
    model_used: str = ""
    status: str = STATUS_GENERIC
    input_fingerprint: str = ""


# --------------------------------------------------------------------------
# generic fallback (no AI, always safe)
# --------------------------------------------------------------------------

def _generic_fallback(company_name: str, *, reason: str, fingerprint: str) -> PersonalizationResult:
    company = (company_name or "").strip()
    greeting = f"{company} team" if company else "there"
    return PersonalizationResult(
        first_line_text="",  # empty -> the baseline template's own opener is used
        primary_angle="generic",
        supporting_fact="",
        cta_variant="default",
        internal_sales_note=f"Generic copy (no AI personalization): {reason}.",
        personalization_confidence=0,
        source_summary=f"greeting='{greeting}'",
        facts_used=[],
        model_used="",
        status=STATUS_GENERIC,
        input_fingerprint=fingerprint,
    )


def _select_model(bike_tier: str, lead_score: dict | None) -> str:
    """High-value leads get the stronger model; everyone else the cheap one."""
    tier = str(bike_tier or "").strip().lower()
    priority = str((lead_score or {}).get("outreach_priority", "")).lower()
    high_value = tier in {"good tier", "hard to reach"} or priority == "high"
    if high_value:
        return str(getattr(settings, "personalization_model_highvalue", "claude-opus-4-8"))
    return str(getattr(settings, "personalization_model_bulk", "claude-haiku-4-5-20251001"))


def _fingerprint(company_name, city, sector, bike_tier, website, facts) -> str:
    raw = "|".join([
        str(company_name), str(city), str(sector), str(bike_tier), str(website),
        json.dumps(facts, sort_keys=True, ensure_ascii=False),
    ])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------
# prompt + LLM call
# --------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "You are a B2B copywriter for Schild Inc, a Dutch maker of metal mudguard "
    "labels and custom accessories for bicycle shops. You write SHORT, warm, "
    "professional cold-outreach snippets in the lead's likely language (Dutch for "
    "NL, otherwise English).\n\n"
    "STRICT RULES:\n"
    "1. Use ONLY the VERIFIED FACTS provided. Never invent, infer, or imply any "
    "fact that is not in that list. If unsure, stay generic.\n"
    "2. Reference at most 1-3 facts. Do not sound over-researched or creepy.\n"
    "3. In 'facts_used', list ONLY the exact fact KEYS you actually used, taken "
    "verbatim from the provided verified-fact keys. If you used none, return [].\n"
    "4. Keep first_line to one natural sentence. Keep everything concise.\n"
    "5. Output ONLY a JSON object with keys: first_line, primary_angle, "
    "cta_suggestion, internal_sales_note, supporting_fact, facts_used, "
    "confidence (0-100, your honest certainty this is relevant and non-generic)."
)


def _build_user_prompt(*, company_name, city, sector, bike_tier, website, verified_facts: dict) -> str:
    lines = [
        f"Company: {company_name or '(unknown)'}",
        f"City: {city or '(unknown)'}",
        f"Sector: {sector or 'bike shop'}",
        f"Bike tier: {bike_tier or '(unknown)'}",
        f"Accepted website: {website or '(none)'}",
        "",
        "VERIFIED FACTS (key: value) — the ONLY facts you may use:",
    ]
    if verified_facts:
        for k, v in verified_facts.items():
            lines.append(f"- {k}: {v}")
    else:
        lines.append("- (none)")
    lines.append("")
    lines.append("Write the JSON now.")
    return "\n".join(lines)


def _call_llm(system: str, user: str, model: str) -> dict:
    """Single network hop to the Anthropic API. Lazy import; raises on failure.

    Monkeypatched in tests. Returns the parsed JSON dict from the model.
    """
    import anthropic  # lazy: optional dep, only needed when the feature is ON

    client = anthropic.Anthropic(api_key=_api_key())
    resp = client.messages.create(
        model=model,
        max_tokens=600,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(getattr(block, "text", "") for block in resp.content).strip()
    # Be tolerant of code-fence wrapping.
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    return json.loads(text)


# --------------------------------------------------------------------------
# public API
# --------------------------------------------------------------------------

def build_personalization(
    *,
    company_name: str,
    city: str = "",
    sector: str = "bike",
    bike_tier: str = "",
    website: str = "",
    lead_score: dict | None = None,
    trusted_facts: dict[str, str] | None = None,
) -> PersonalizationResult:
    """Produce a controlled personalization, or a safe generic fallback.

    ``trusted_facts`` is a dict of {field_name: value} containing ONLY facts with
    review_required=False (the caller filters). Everything is fail-open.
    """
    trusted_facts = dict(trusted_facts or {})
    # Cap the number of facts we expose to the model (controlled personalization).
    if len(trusted_facts) > _max_facts():
        trusted_facts = dict(list(trusted_facts.items())[: _max_facts()])
    fp = _fingerprint(company_name, city, sector, bike_tier, website, trusted_facts)

    if not personalization_enabled():
        return _generic_fallback(company_name, reason="feature disabled", fingerprint=fp)
    if not _api_key():
        return _generic_fallback(company_name, reason="no API key", fingerprint=fp)
    if not trusted_facts:
        return _generic_fallback(company_name, reason="no trusted facts", fingerprint=fp)
    if not _budget_ok():
        return _generic_fallback(company_name, reason="daily budget reached", fingerprint=fp)

    model = _select_model(bike_tier, lead_score)
    system = _SYSTEM_PROMPT
    user = _build_user_prompt(
        company_name=company_name, city=city, sector=sector,
        bike_tier=bike_tier, website=website, verified_facts=trusted_facts,
    )

    try:
        data = _call_llm(system, user, model)
        _spend()
    except Exception as exc:  # noqa: BLE001 - any LLM/parse failure -> generic
        logger.info("personalization: LLM call failed (%s)", exc)
        return _generic_fallback(company_name, reason="LLM error", fingerprint=fp)

    if not isinstance(data, dict):
        return _generic_fallback(company_name, reason="bad LLM output", fingerprint=fp)

    facts_used = data.get("facts_used") or []
    if isinstance(facts_used, str):
        facts_used = [facts_used]
    facts_used = [str(f).strip() for f in facts_used if str(f).strip()]

    # HALLUCINATION GUARD: every cited fact key MUST be one we actually supplied.
    allowed = set(trusted_facts.keys())
    if any(f not in allowed for f in facts_used):
        logger.info("personalization: rejected — cited ungrounded fact(s) %s", facts_used)
        return _generic_fallback(company_name, reason="cited ungrounded fact", fingerprint=fp)

    try:
        confidence = int(data.get("confidence", 0))
    except (TypeError, ValueError):
        confidence = 0
    confidence = max(0, min(100, confidence))

    # Weak signal -> discard AI copy, ship generic (no low-confidence copy used).
    if confidence < _min_confidence():
        return _generic_fallback(company_name, reason=f"low confidence ({confidence})", fingerprint=fp)

    return PersonalizationResult(
        first_line_text=str(data.get("first_line", "")).strip(),
        primary_angle=str(data.get("primary_angle", "")).strip(),
        supporting_fact=str(data.get("supporting_fact", "")).strip(),
        cta_variant=str(data.get("cta_suggestion", "")).strip() or "default",
        internal_sales_note=str(data.get("internal_sales_note", "")).strip(),
        personalization_confidence=confidence,
        source_summary=f"facts={facts_used}; model={model}",
        facts_used=facts_used,
        model_used=model,
        status=STATUS_DRAFT,  # NEVER auto-approved — a human reviews/approves
        input_fingerprint=fp,
    )


def persist_personalization(
    session: Session, subject_type: str, subject_id: int, result: PersonalizationResult, *, sequence_step: int = 0
) -> Personalization:
    """Upsert the Personalization row. Caller commits. Respects a human-approved
    row (status='approved' or reviewed_by set) — never clobbers it."""
    subject_type = (subject_type or "").strip().lower()
    row = session.scalar(
        select(Personalization).where(
            Personalization.subject_type == subject_type,
            Personalization.subject_id == subject_id,
            Personalization.sequence_step == sequence_step,
        )
    )
    if row is not None and (row.status == "approved" or row.reviewed_by):
        return row  # human-owned — leave it

    if row is None:
        row = Personalization(subject_type=subject_type, subject_id=subject_id, sequence_step=sequence_step)
        session.add(row)

    row.first_line_text = result.first_line_text
    row.primary_angle = result.primary_angle
    row.supporting_fact = result.supporting_fact
    row.cta_variant = result.cta_variant
    row.internal_sales_note = result.internal_sales_note
    row.personalization_confidence = result.personalization_confidence
    row.source_summary = result.source_summary
    row.facts_used = json.dumps(result.facts_used)
    row.model_used = result.model_used
    row.status = result.status
    row.input_fingerprint = result.input_fingerprint
    row.updated_at = datetime.utcnow()
    session.flush()
    return row
