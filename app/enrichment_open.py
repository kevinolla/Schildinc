"""Wire the open (non-Google) discovery stack into KVK enrichment.

This is the bridge between `app/discovery_open.py` (pure orchestrator that
returns a `DiscoveryOutcome`) and the `kvk_companies` table. It:

  1. picks KVK rows that still need discovery,
  2. runs `discover_for_company(...)` for each (SearXNG -> crawl -> extract),
  3. writes the result + confidence + provenance back onto the row,
  4. runs review-only customer suppression (NEVER auto-flags Klant — it only
     records match_confidence/best_match_reason for the Match-review queue).

Everything is GATED by `settings.discovery_engine`:
  - "open"   -> use this path
  - "google" -> skip (the legacy app/kvk_enrichment.py daemon stays in charge)

Safe + incremental: this does not replace or stop the existing enrichment
daemon. It is invoked on demand from the Discovery-review UI (a button) and
can also be run as a one-shot batch. With SEARXNG_URL unset, discovery_open
degrades to "needs_review"/"no_website" and nothing crashes.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import KvkCompany
from app.discovery_open import discover_for_company
from app.suppression import match_existing_customer


def open_engine_active() -> bool:
    """True when the open discovery path should be used."""
    return getattr(settings, "discovery_engine", "open") == "open"


def _persist_outcome(company: KvkCompany, outcome) -> None:
    """Map a DiscoveryOutcome onto the KvkCompany row (only filling blanks so a
    manual/verified value is never clobbered by an automated find)."""
    if outcome.website and not (company.website or "").strip():
        company.website = outcome.website
        company.website_domain = outcome.website_domain
    company.website_confidence = int(outcome.website_confidence or 0)

    if outcome.email_public and not (company.email_public or "").strip():
        company.email_public = outcome.email_public
        company.email_source_url = outcome.email_source_page
        company.email_confidence = str(outcome.email_confidence or "")
    if outcome.phone_public and not (company.phone_public or "").strip():
        company.phone_public = outcome.phone_public
        company.phone_source_url = outcome.phone_source_page
        company.phone_confidence = str(outcome.phone_confidence or "")
    if outcome.instagram_url and not (company.instagram_url or "").strip():
        company.instagram_url = outcome.instagram_url
    if outcome.linkedin_url and not (company.linkedin_url or "").strip():
        company.linkedin_url = outcome.linkedin_url
    if outcome.whatsapp_number and not (company.whatsapp_number or "").strip():
        company.whatsapp_number = outcome.whatsapp_number

    # Provenance / audit (always overwrite — reflects the latest attempt).
    company.discovery_query_used = outcome.discovery_query_used or ""
    company.discovery_input_type = outcome.discovery_input_type or ""
    company.discovery_backend = outcome.backend or "open"
    company.last_enrichment_attempt_at = datetime.now(timezone.utc)
    company.search_attempts = (company.search_attempts or 0) + 1

    # Map the orchestrator status onto the row's enrichment_status so the
    # existing /kvk filters + the new review queue agree on state.
    status_map = {
        "found": "discovered",
        "partial": "partial",
        "no_contacts": "no_contacts",
        "no_website": "no_website",
        "needs_review": "needs_review",
        "use_google_fallback": company.enrichment_status,  # leave as-is
    }
    company.enrichment_status = status_map.get(outcome.status, company.enrichment_status)


def _persist_suppression_review(session: Session, company: KvkCompany) -> None:
    """Run review-only suppression and record the verdict on the row.

    IMPORTANT: this NEVER sets already_client_flag. It only writes
    match_confidence + best_match_reason so a human can confirm in the
    Match-review queue. (Auto-flagging stays the job of the STRICT matcher.)
    """
    result = match_existing_customer(
        session,
        website_domain=company.website_domain or company.website or "",
        email=company.email_public or "",
        company_name=company.company_name or "",
        city=company.primary_city or "",
        country=company.country_code or "NL",
        kvk_number=company.kvk_number or "",
    )
    company.match_confidence = result.match_confidence
    company.best_match_reason = result.best_match_reason
    if result.matched_customer_id and not company.matched_customer_id:
        company.matched_customer_id = result.matched_customer_id
    # An EXACT match is safe to auto-suppress (domain/email/kvk identity);
    # high/medium/low are surfaced for human review only.
    if result.match_confidence == "exact":
        company.already_client_flag = True
        company.client_match_status = "matched"


def run_open_discovery_for_company(session: Session, company: KvkCompany, *, commit: bool = True) -> str:
    """Discover one company via the open stack + record suppression review.

    Returns the resulting enrichment_status. Never raises on discovery errors —
    a failure is recorded as the outcome status and the row stays processable.
    """
    outcome = discover_for_company(
        session,
        name=company.company_name or "",
        city=company.primary_city or "",
        country=company.country_code or "NL",
        postal=company.primary_postal_code or "",
        website=company.website or "",
    )
    # engine=google -> caller should run the legacy daemon; do nothing here.
    if outcome.status == "use_google_fallback":
        if commit:
            session.commit()
        return "use_google_fallback"

    _persist_outcome(company, outcome)
    _persist_suppression_review(session, company)
    if commit:
        session.commit()
    return company.enrichment_status


def select_discovery_candidates(session: Session, *, limit: int = 25, max_attempts: int = 2):
    """KVK rows that still need open discovery: not already a client, missing an
    email, not yet exhausted, never finished. Fewest-attempts first."""
    return session.scalars(
        select(KvkCompany)
        .where(KvkCompany.already_client_flag.is_(False))
        .where(KvkCompany.email_public == "")
        .where(KvkCompany.enrichment_status.notin_(["discovered", "no_contacts"]))
        .where(KvkCompany.search_attempts < max_attempts)
        .order_by(KvkCompany.search_attempts.asc(), KvkCompany.id.asc())
        .limit(limit)
    ).all()


def scan_possible_customers(session: Session, *, limit: int = 300) -> dict:
    """Run review-only suppression over non-client KVK rows that have an email
    or website but no recorded match verdict yet. Populates match_confidence /
    best_match_reason so the Match-review queue has something to show. Auto-
    suppresses only EXACT identity matches; everything else is for human review.
    """
    rows = session.scalars(
        select(KvkCompany)
        .where(KvkCompany.already_client_flag.is_(False))
        .where(or_(KvkCompany.email_public != "", KvkCompany.website != ""))
        .where(or_(KvkCompany.match_confidence == "", KvkCompany.match_confidence.is_(None)))
        .order_by(KvkCompany.id.asc())
        .limit(limit)
    ).all()
    stats = {"scanned": 0, "exact": 0, "high": 0, "medium": 0, "low": 0, "none": 0}
    for company in rows:
        _persist_suppression_review(session, company)
        stats["scanned"] += 1
        stats[company.match_confidence or "none"] = stats.get(company.match_confidence or "none", 0) + 1
        session.commit()
    stats["ok"] = True
    return stats


def run_open_discovery_batch(session: Session, *, limit: int = 25) -> dict:
    """Run open discovery over a batch. Returns a small stats dict for the UI."""
    if not open_engine_active():
        return {"ok": False, "error": "discovery_engine_not_open", "processed": 0}
    rows = select_discovery_candidates(session, limit=limit)
    stats = {"processed": 0, "found": 0, "partial": 0, "needs_review": 0, "no_website": 0, "no_contacts": 0}
    for company in rows:
        status = run_open_discovery_for_company(session, company, commit=False)
        stats["processed"] += 1
        stats[status] = stats.get(status, 0) + 1
        session.commit()
    stats["ok"] = True
    return stats
