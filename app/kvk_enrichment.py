"""
KVK Enrichment Pipeline
=======================
Finds website, email, and phone for KVK bike companies starting from
only a business name and address (no website required).

Pipeline per company:
  1. If website missing → search Google Places API (if key set)
                       → fallback: DuckDuckGo HTML search
  2. If website found  → run Playwright contact scraper for email/phone
  3. Store all results with source + confidence

Public data only. No form-filling. No private email scraping.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from threading import Thread
from typing import Any
from urllib.parse import quote_plus, urljoin, urlparse
from urllib.request import Request, urlopen

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.google_places import search_google_places
from app.matching import apply_kvk_matching
from app.models import KvkCompany
from app.utils import normalize_domain, normalize_email

# ---------------------------------------------------------------------------
# Website finder — Google Places first, DuckDuckGo fallback
# ---------------------------------------------------------------------------

_DDG_URL = "https://html.duckduckgo.com/html/?q={query}"
_DDG_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; SchildIncProspectCrawler/2.0; +https://schildinc.com)",
    "Accept-Language": "nl,en;q=0.9",
}
_RESULT_URL_PATTERN = re.compile(r'uddg=([^&"\']+)', re.I)
_URL_IN_HREF = re.compile(r'href="(https?://[^"]+)"', re.I)

# Domains to skip when picking a website from search results
_SKIP_DOMAINS = {
    "google.com", "google.nl", "duckduckgo.com", "bing.com", "facebook.com",
    "instagram.com", "linkedin.com", "twitter.com", "youtube.com",
    "kvk.nl", "bedrijfsprofiel.nl", "openkvk.nl", "kvkinfo.nl",
    "yelp.com", "tripadvisor.com", "thuisbezorgd.nl", "trustpilot.com",
    "123inkt.nl", "marktplaats.nl", "2dehands.be",
}


def _is_skip_domain(url: str) -> bool:
    try:
        host = urlparse(url).netloc.lower().lstrip("www.")
        return any(host == d or host.endswith("." + d) for d in _SKIP_DOMAINS)
    except Exception:
        return True


def _ddg_search_urls(query: str, max_results: int = 5) -> list[str]:
    """Search DuckDuckGo HTML (no API key) and return the top result URLs."""
    try:
        url = _DDG_URL.format(query=quote_plus(query))
        req = Request(url, headers=_DDG_HEADERS)
        with urlopen(req, timeout=8) as resp:
            html = resp.read().decode("utf-8", errors="replace")

        # DDG encodes result URLs as uddg=<urlencoded> query params
        from urllib.parse import unquote
        candidates: list[str] = []
        for match in _RESULT_URL_PATTERN.finditer(html):
            decoded = unquote(match.group(1))
            if decoded.startswith("http") and not _is_skip_domain(decoded):
                candidates.append(decoded)
                if len(candidates) >= max_results:
                    break

        if not candidates:
            # Fallback: extract plain href links
            for match in _URL_IN_HREF.finditer(html):
                href = match.group(1)
                if not _is_skip_domain(href) and href.startswith("http"):
                    candidates.append(href)
                    if len(candidates) >= max_results:
                        break

        return candidates
    except Exception:
        return []


def _google_places_lookup(company: KvkCompany) -> dict[str, str]:
    """
    Search Google Places API using the pre-built google_maps_query.
    Returns {website, phone, place_name, confidence}.
    """
    if not settings.google_places_api_key:
        return {}

    query = company.google_maps_query or f"{company.company_name} {company.primary_city or ''} fietswinkel"
    try:
        results = search_google_places(query)
        if not results:
            return {}

        place = results[0]
        website = place.get("websiteUri", "")
        phone = place.get("nationalPhoneNumber", "")
        place_name = place.get("displayName", {}).get("text", "")

        # Simple name-similarity check to avoid wrong-store matches
        from rapidfuzz import fuzz
        name_score = fuzz.WRatio(
            (company.company_name or "").lower(),
            (place_name or "").lower(),
        )
        confidence = "high" if name_score >= 80 else "medium" if name_score >= 60 else "low"

        return {
            "website": website,
            "phone": phone,
            "place_name": place_name,
            "source": "google_places",
            "confidence": confidence,
        }
    except Exception:
        return {}


def _ddg_lookup(company: KvkCompany) -> dict[str, str]:
    """
    DuckDuckGo HTML search fallback — no API key required.
    Searches by company name + city, returns best website candidate.
    """
    query = company.google_maps_query or f"{company.company_name} {company.primary_city or ''} fietswinkel"
    urls = _ddg_search_urls(query)
    if not urls:
        # Try without "fietswinkel"
        query2 = f"{company.company_name} {company.primary_city or ''}".strip()
        urls = _ddg_search_urls(query2)

    if not urls:
        return {}

    best = urls[0]
    domain = normalize_domain(best)

    # Extra check: if company name tokens appear in domain, raise confidence
    name_tokens = set((company.canonical_company_name_clean or company.company_name or "").lower().split())
    domain_tokens = set(re.split(r"[\.\-]", domain.lower()))
    overlap = name_tokens & domain_tokens
    confidence = "medium" if overlap else "low"

    return {
        "website": best,
        "source": "ddg_search",
        "confidence": confidence,
    }


def find_website_for_kvk_company(company: KvkCompany) -> dict[str, str]:
    """
    Try Google Places first, then DuckDuckGo.
    Returns dict with keys: website, phone (optional), source, confidence.
    Returns {} if nothing found.
    """
    result = _google_places_lookup(company)
    if result.get("website"):
        return result

    result = _ddg_lookup(company)
    return result


# ---------------------------------------------------------------------------
# Full enrichment pipeline
# ---------------------------------------------------------------------------

def enrich_kvk_company_full(session: Session, company: KvkCompany) -> None:
    """
    Full pipeline:
    1. Find website if missing (Places API or DuckDuckGo)
    2. Scrape email + phone from website (Playwright)
    3. Re-run customer matching
    4. Update enrichment_status
    """
    from app.discovery import discover_contacts_for_kvk_company

    company.last_enrichment_attempt_at = datetime.now(tz=timezone.utc)

    # Step 1: find website if missing
    if not company.website:
        company.enrichment_status = "searching"
        session.commit()

        search_result = find_website_for_kvk_company(company)

        if search_result.get("website"):
            website = search_result["website"]
            if not website.startswith(("http://", "https://")):
                website = f"https://{website}"
            company.website = website
            company.website_domain = normalize_domain(website)
            company.email_source_url = search_result.get("source", "search")
            if search_result.get("phone") and not company.phone_public:
                company.phone_public = search_result["phone"]
                company.phone_source_url = search_result.get("source", "search")
                company.phone_confidence = search_result.get("confidence", "medium")
            session.commit()

    # Step 2: scrape email + phone from website
    if company.website:
        discover_contacts_for_kvk_company(session, company)
    else:
        company.enrichment_status = "no_website"
        apply_kvk_matching(session, company)
        session.commit()


# ---------------------------------------------------------------------------
# Background job runner
# ---------------------------------------------------------------------------

def run_kvk_enrichment_job(company_id: int) -> None:
    """Meant to be called in a background Thread."""
    db = SessionLocal()
    try:
        company = db.get(KvkCompany, company_id)
        if company and company.enrichment_status not in ("discovered",):
            enrich_kvk_company_full(db, company)
    except Exception as exc:
        db.rollback()
        try:
            company = db.get(KvkCompany, company_id)
            if company:
                company.enrichment_status = "error"
                company.notes = (company.notes or "") + f" | job error: {exc}"
                db.commit()
        except Exception:
            pass
    finally:
        db.close()


def run_kvk_bulk_enrichment(company_ids: list[int], delay_seconds: float = 0.5) -> None:
    """
    Run enrichment for multiple companies sequentially in one thread.
    Uses a small delay between requests to be respectful.
    """
    db = SessionLocal()
    try:
        for cid in company_ids:
            company = db.get(KvkCompany, cid)
            if company and company.enrichment_status not in ("discovered",):
                try:
                    enrich_kvk_company_full(db, company)
                except Exception as exc:
                    try:
                        company.enrichment_status = "error"
                        company.notes = (company.notes or "") + f" | bulk error: {exc}"
                        db.commit()
                    except Exception:
                        db.rollback()
                if delay_seconds > 0:
                    time.sleep(delay_seconds)
    finally:
        db.close()
