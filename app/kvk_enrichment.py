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
from concurrent.futures import ThreadPoolExecutor, FIRST_COMPLETED, wait, as_completed
from datetime import datetime, timezone
from threading import Thread
from typing import Any
from urllib.parse import quote_plus, urljoin, urlparse
from urllib.request import Request, urlopen

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

import threading

from app.config import settings
from app.db import SessionLocal
from app.email_guesser import best_guess as guess_email_for_domain
from app.google_places import search_google_places
from app.matching import apply_kvk_matching
from app.models import KvkCompany
from app.utils import normalize_domain, normalize_email

_scheduler_started = False
_scheduler_lock = threading.Lock()

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


_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", re.I)
_SEARCH_SNIPPET_BLOCK_RE = re.compile(
    r'class="result__(?:title|snippet|url)[^>]*>([\s\S]*?)</a>', re.I
)
_TAG_RE = re.compile(r"<[^>]+>")
_REJECT_EMAIL_LOCAL = {
    "noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon",
    "support-ticket", "webmaster", "postmaster", "abuse", "admin",
}
_REJECT_EMAIL_DOMAINS = {
    "sentry.io", "wixsite.com", "shopify.com", "mailchimp.com",
    "klaviyo.com", "google.com", "googlemail.com", "wordpress.com",
    "example.com", "example.org", "domain.com", "yourdomain.com",
    "company.com", "test.com", "email.com",
} | _SKIP_DOMAINS


def _filter_emails_from_text(text: str, max_emails: int = 10) -> list[str]:
    """
    Run the email regex over any text blob (search snippets, HTML pages,
    etc.) and return only candidates that look like real business emails
    after filtering out vendor noise, placeholders, and image extensions.
    Order preserved so the first-found wins downstream ranking ties.
    """
    if not text:
        return []
    found: list[str] = []
    seen: set[str] = set()
    for em in _EMAIL_RE.findall(text):
        em = em.strip(".,;:!?\"')(<>").lower()
        if em in seen:
            continue
        seen.add(em)
        local, _, dom = em.partition("@")
        if not dom or "." not in dom:
            continue
        if local in _REJECT_EMAIL_LOCAL:
            continue
        if any(dom == d or dom.endswith("." + d) for d in _REJECT_EMAIL_DOMAINS):
            continue
        if any(local.endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".gif", ".svg")):
            continue
        if len(local) < 2 or local.isdigit():
            continue
        found.append(em)
        if len(found) >= max_emails:
            break
    return found


def _emails_from_snippets(query: str, max_emails: int = 10) -> list[str]:
    """
    Extract email addresses from search-engine result snippets.

    Tries multiple backends, residential-quality sources FIRST:
      A. Playwright Google scrape (free, real Chromium DOM — bypasses the
         cloud-IP block that hits bare urllib requests)
      B. Bing HTML scrape (free, but cloud IPs get a JS-only stub)
      C. DuckDuckGo HTML scrape (free, sparse snippets)
      D. Google CSE (rich, but 'entire web' toggle is deprecated)
      E. Brave Search API (paid, last resort)
    """
    # ── Source A: Playwright Google (real Chromium — works from cloud IPs)
    try:
        from app.playwright_search import google_snippet_text
        text = google_snippet_text(query)
        if text:
            emails = _filter_emails_from_text(text, max_emails=max_emails)
            if emails:
                return emails
    except Exception as exc:
        print(f"[snippets] playwright source failed: {exc}")

    # ── Source B: Bing (free, but cloud IPs get a JS-only stub) ───────────
    try:
        from app.bing_search import bing_snippet_text
        text = bing_snippet_text(query, count=10)
        if text:
            emails = _filter_emails_from_text(text, max_emails=max_emails)
            if emails:
                return emails
    except Exception:
        pass

    # ── Source B: DuckDuckGo HTML (free, but snippets often sparse) ───────
    try:
        from html import unescape
        from urllib.parse import unquote
        url = _DDG_URL.format(query=quote_plus(query))
        req = Request(url, headers=_DDG_HEADERS)
        with urlopen(req, timeout=8) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        readable = unquote(unescape(html))
        emails = _filter_emails_from_text(readable, max_emails=max_emails)
        if emails:
            return emails
    except Exception:
        pass

    # ── Source C: Google CSE (only useful if user configured a legacy one) ─
    try:
        from app.google_search import cse_snippet_text, is_enabled as _cse_on
        if _cse_on():
            text = cse_snippet_text(query, num=5)
            emails = _filter_emails_from_text(text, max_emails=max_emails)
            if emails:
                return emails
    except Exception:
        pass

    # ── Source D: Brave Search (PAID — last resort to save credit) ────────
    try:
        from app.brave_search import brave_snippet_text, is_enabled as _brave_on
        if _brave_on():
            text = brave_snippet_text(query, count=5)
            emails = _filter_emails_from_text(text, max_emails=max_emails)
            if emails:
                return emails
    except Exception:
        pass

    return []


def _rank_snippet_emails(emails: list[str], company: KvkCompany) -> str:
    """
    Pick the most likely company email from snippet candidates by scoring
    each on local-part type (info@ wins for SMBs) and how well the email
    domain matches the company name tokens.
    """
    if not emails:
        return ""

    name = (company.company_name or "").lower()
    name_tokens = {t for t in re.split(r"[^a-z0-9]+", name) if len(t) >= 3}
    generic_drop = {"the", "van", "de", "het", "een", "and", "fiets", "fietsen", "bike", "bikes", "store", "shop"}
    name_tokens -= generic_drop

    best_email = ""
    best_score = -1
    GENERIC_LOCALS = {"info", "contact", "hello", "sales", "verkoop", "winkel", "shop", "klantenservice", "office"}

    for em in emails:
        local, _, dom = em.partition("@")
        score = 0
        # Local-part: prefer generic info@/contact@ for SMBs (most reliable)
        if local in GENERIC_LOCALS:
            score += 30
        # Domain overlap with company name tokens = strong signal
        dom_tokens = {t for t in re.split(r"[^a-z0-9]+", dom.replace(".", " ")) if len(t) >= 3}
        overlap = name_tokens & dom_tokens
        if overlap:
            score += 40 + min(20, 10 * len(overlap))
        # Penalize free webmail (gmail/hotmail/etc) — owners often list these
        # but they're a weaker signal than a real domain email
        if dom in {"gmail.com", "hotmail.com", "outlook.com", "yahoo.com",
                   "ziggo.nl", "kpnmail.nl", "live.nl", "icloud.com"}:
            score -= 30
        # Prefer .nl for Dutch businesses
        if dom.endswith(".nl"):
            score += 8
        if score > best_score:
            best_score = score
            best_email = em

    return best_email if best_score >= 20 else ""


def _snippet_email_lookup(company: KvkCompany) -> dict[str, str]:
    """
    Last-resort email finder. Used ONLY when the free pipeline stages
    (Places + Playwright + MX-guess) couldn't resolve a record — i.e.
    we have no website at all. Runs exactly ONE Brave query per record
    to control cost.

    Returns dict with: email, website (from email domain), source,
    confidence.  Returns {} if nothing reliable found.
    """
    name = (company.company_name or "").strip()
    city = (company.primary_city or "").strip()
    if not name:
        return {}

    # Most-specific single query: `"Exact Name" City contact email`. Quoted
    # name + "contact" keyword biases Brave toward the contact page snippet
    # where emails live. Just one shot — no fallback second query.
    if city:
        query = f'"{name}" {city} contact email'
    else:
        query = f'"{name}" contact email'

    candidates = _emails_from_snippets(query, max_emails=10)
    if not candidates:
        return {}

    best = _rank_snippet_emails(candidates, company)
    if not best:
        return {}

    _, _, dom = best.partition("@")
    return {
        "email": best,
        "website": f"https://{dom}",
        "website_domain": dom,
        "source": "search_snippet",
        "confidence": "high",  # snippet-found emails are usually accurate
    }


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


def _score_place_match(place: dict, company: KvkCompany) -> dict[str, Any]:
    """
    Score a Google Places result against a KVK company using BOTH the name
    and the postal address. Returns a dict with separate name/address scores
    plus a combined total — the caller uses these to decide whether to
    trust the website AND whether to rename the KVK record.
    """
    from rapidfuzz import fuzz

    display_name = (place.get("displayName") or {}).get("text", "") or ""
    formatted_addr = (place.get("formattedAddress") or "").lower()
    components = {
        (item.get("types") or [""])[0]: item.get("shortText", "")
        for item in place.get("addressComponents", [])
    }

    company_name = (company.company_name or "")
    name_score = int(fuzz.WRatio(company_name.lower(), display_name.lower())) if display_name else 0

    address_score = 0
    postal = (company.primary_postal_code or "").strip()
    if postal and len(postal) >= 5:
        # Dutch postal codes are 4-digit + 2-letter — match the leading 4 digits
        postal_digits = re.sub(r"\s+", "", postal)[:4]
        if postal_digits and postal_digits in formatted_addr.replace(" ", ""):
            address_score += 40

    company_city = (company.primary_city or "").strip().lower()
    place_city = (components.get("locality", "") or "").strip().lower()
    if company_city and place_city and company_city == place_city:
        address_score += 20
    elif company_city and company_city in formatted_addr:
        address_score += 12

    primary_addr = (company.primary_address or "").strip().lower()
    if primary_addr:
        addr_tokens = {t for t in re.split(r"[^a-z0-9]+", primary_addr) if len(t) >= 2}
        formatted_tokens = {t for t in re.split(r"[^a-z0-9]+", formatted_addr) if len(t) >= 2}
        # Generic words shouldn't count toward an address match
        addr_tokens -= {"the", "van", "de", "het", "een", "and", "and-"}
        overlap = addr_tokens & formatted_tokens
        if len(overlap) >= 3:
            address_score += 25
        elif len(overlap) >= 2:
            address_score += 15
        elif len(overlap) >= 1:
            address_score += 6

    address_score = min(60, address_score)
    has_website = bool(place.get("websiteUri"))

    # Combined: address-heavy (we want correct location)
    total = int(name_score * 0.45) + address_score + (5 if has_website else 0)

    return {
        "place": place,
        "display_name": display_name,
        "website": place.get("websiteUri", "") or "",
        "phone": place.get("nationalPhoneNumber", "") or "",
        "formatted_address": place.get("formattedAddress", "") or "",
        "google_maps_url": place.get("googleMapsUri", "") or "",
        "name_score": name_score,
        "address_score": address_score,
        "total_score": total,
        "has_website": has_website,
    }


def _google_places_lookup(company: KvkCompany) -> dict[str, Any]:
    """
    Advanced Google Places lookup — searches by name AND address, scores
    candidates across multiple queries, and returns the best match by
    combined name+address fit.

    Returned dict keys:
      website, phone, place_name, place_address, google_maps_url,
      name_score, address_score, total_score, source, confidence,
      rename_suggested (bool — True when address strongly matches but
      the KVK name disagrees with what Google shows)
    """
    if not settings.google_places_api_key:
        return {}

    # Build a ranked list of queries — most-specific first
    name = (company.company_name or "").strip()
    city = (company.primary_city or "").strip()
    addr = (company.primary_address or "").strip()
    postal = (company.primary_postal_code or "").strip()

    queries: list[str] = []
    if name and addr and city:
        queries.append(f"{name} {addr} {city}")
    if name and postal and city:
        queries.append(f"{name} {postal} {city}")
    if name and city:
        queries.append(f"{name} {city} fietswinkel")
    if company.google_maps_query:
        queries.append(company.google_maps_query)
    if name and city:
        queries.append(f"{name} {city}")

    seen_query: set[str] = set()
    queries = [q for q in queries if q.strip() and not (q in seen_query or seen_query.add(q))]

    best: dict[str, Any] | None = None
    # Cap at 2 Places calls per record to control API spend (~$0.064/record max).
    # First query is the most specific (name+address+city), so good matches stop early.
    for query in queries[:2]:
        try:
            results = search_google_places(query, page_size=5)
        except Exception:
            continue
        if not results:
            continue
        for place in results:
            scored = _score_place_match(place, company)
            if best is None or scored["total_score"] > best["total_score"]:
                best = scored
        if best and best["total_score"] >= 70:
            # Acceptable match — stop refining to save API budget
            break

    if not best:
        return {}

    # Reject anything with very weak signal
    if best["total_score"] < 30:
        return {}

    # Confidence comes from BOTH signals
    name_score = best["name_score"]
    addr_score = best["address_score"]
    if addr_score >= 40 and name_score >= 70:
        confidence = "high"
    elif addr_score >= 40:  # address solid, even if name differs
        confidence = "high"
    elif name_score >= 75:
        confidence = "medium"
    else:
        confidence = "low"

    # Rename suggestion: address confirms the location is right, but the
    # KVK name doesn't match what Google calls it (e.g., trade name vs.
    # legal name). Caller should swap the KVK name for the Google name.
    rename_suggested = (
        addr_score >= 40
        and name_score < 70
        and bool(best["display_name"])
        and best["display_name"].strip().lower() != (company.company_name or "").strip().lower()
    )

    return {
        "website": best["website"],
        "phone": best["phone"],
        "place_name": best["display_name"],
        "place_address": best["formatted_address"],
        "google_maps_url": best["google_maps_url"],
        "name_score": name_score,
        "address_score": addr_score,
        "total_score": best["total_score"],
        "source": "google_places",
        "confidence": confidence,
        "rename_suggested": rename_suggested,
    }


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
    Full pipeline (ordered cheapest → most expensive):
    0. Free Bing/DDG snippet search — mirrors the manual Chrome workflow,
       finds the email in result meta-descriptions in <2s. No key, no cost.
       Skipped only if we already have an email_public value.
    1. Find website (Places API → DuckDuckGo) — Places costs $0.032/call
    2. Scrape email from website via Playwright — free
    3. MX-validated `info@<domain>` guess — free
    4. LAST RESORT: paid Brave snippet search — only if every free
       stage missed AND we still have no website
    5. Re-run customer matching, update enrichment_status
    """
    from app.discovery import discover_contacts_for_kvk_company

    company.last_enrichment_attempt_at = datetime.now(tz=timezone.utc)

    # ── Step 0: Free snippet email search (Bing → DDG → CSE) ──────────────
    # Runs first because it's the fastest path when it works. The function
    # itself only calls Brave if every free source returned nothing, so
    # this stage is effectively free for most records.
    if not (company.email_public or "").strip():
        try:
            snippet = _snippet_email_lookup(company)
            if snippet.get("email"):
                company.email_public = snippet["email"]
                company.email_source_url = snippet.get("source", "search_snippet")
                company.email_confidence = snippet.get("confidence", "high")
                if not company.website:
                    derived = snippet.get("website", "")
                    if derived:
                        company.website = derived
                        company.website_domain = snippet.get("website_domain", "")
                company.enrichment_status = "discovered"
                apply_kvk_matching(session, company)
                session.commit()
                return  # Fast win — done.
        except Exception:
            pass

    # Step 1: find website if missing — Google Places (name+address) → DDG fallback
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

            # Step 1b: name override when Google Places address confirms the
            # location but the KVK legal name disagrees with the trade name.
            # Example: KVK lists "Joh. de Vries Beheer B.V." but Google
            # calls the same address "Joop Harmans Cycling XL" — we adopt
            # the Google name (the customer-facing brand) and stash the
            # original in notes for audit.
            if search_result.get("rename_suggested"):
                google_name = (search_result.get("place_name") or "").strip()
                if google_name and google_name.lower() != (company.company_name or "").strip().lower():
                    original = company.company_name or ""
                    company.company_name = google_name
                    # Audit trail in notes
                    rename_note = (
                        f"renamed_from='{original}' to='{google_name}' "
                        f"(addr_score={search_result.get('address_score')}, "
                        f"name_score={search_result.get('name_score')})"
                    )
                    company.notes = ((company.notes or "") + " | " + rename_note).lstrip(" |")

            session.commit()

    # Step 2: scrape email + phone from website
    if company.website:
        discover_contacts_for_kvk_company(session, company)

        # Step 2b: pattern fallback — if scraping found no email, try info@<domain>
        # with DNS MX validation. For Dutch SMBs this lifts hit-rate from ~25%
        # (sites that publish email) toward ~85% (sites that have a valid mailbox).
        if not (company.email_public or "").strip() and company.website_domain:
            try:
                guess = guess_email_for_domain(company.website_domain, require_mx=True)
                if guess:
                    company.email_public = guess.email
                    company.email_source_url = f"pattern:{guess.pattern}@"
                    company.email_confidence = "guessed"
                    if company.enrichment_status not in ("discovered",):
                        company.enrichment_status = "discovered"
                    apply_kvk_matching(session, company)
                    session.commit()
            except Exception:
                pass
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


# ---------------------------------------------------------------------------
# Auto-enrichment scheduler — runs on app startup, no manual action needed
# ---------------------------------------------------------------------------

def _reset_stuck_running_records(stale_after_minutes: int | None = None) -> int:
    """
    Reset records left in 'running' or 'searching' state by a dead worker.
    - At scheduler startup: reset ALL such records (stale_after_minutes=None)
    - Between batches: reset only ones older than `stale_after_minutes`
    Returns the count of reset rows.
    """
    db = SessionLocal()
    try:
        stmt = (
            update(KvkCompany)
            .where(KvkCompany.enrichment_status.in_(["running", "searching"]))
            .values(enrichment_status="pending")
        )
        if stale_after_minutes is not None:
            cutoff = datetime.now(tz=timezone.utc) - __import__("datetime").timedelta(minutes=stale_after_minutes)
            stmt = stmt.where(KvkCompany.last_enrichment_attempt_at < cutoff)
        result = db.execute(stmt)
        db.commit()
        return result.rowcount or 0
    except Exception:
        db.rollback()
        return 0
    finally:
        db.close()


def _enrich_one_company(company_id: int) -> str:
    """
    Worker function — opens its own DB session, runs full enrichment on a
    single company, and returns a short status string. Safe to call from a
    ThreadPoolExecutor.

    Self-timeout: the function emits log lines around each pipeline stage
    so we can pinpoint hangs in production. Playwright already has its
    own 6s timeout via settings.playwright_timeout_ms.
    """
    db = SessionLocal()
    started = time.monotonic()
    try:
        company = db.get(KvkCompany, company_id)
        if not company:
            return f"{company_id}:missing"
        if company.enrichment_status == "discovered":
            return f"{company_id}:already-done"
        company.enrichment_status = "searching" if not company.website else "running"
        db.commit()
        enrich_kvk_company_full(db, company)
        elapsed = time.monotonic() - started
        if elapsed > 30:
            print(f"[kvk-enrich] {company_id} took {elapsed:.1f}s status={company.enrichment_status}")
        return f"{company_id}:{company.enrichment_status}"
    except Exception as exc:
        try:
            comp = db.get(KvkCompany, company_id)
            if comp:
                comp.enrichment_status = "error"
                comp.notes = (comp.notes or "") + f" | auto: {exc}"
                db.commit()
        except Exception:
            db.rollback()
        print(f"[kvk-enrich] {company_id} ERROR after {time.monotonic()-started:.1f}s: {exc}")
        return f"{company_id}:error"
    finally:
        db.close()


def _auto_enrich_loop() -> None:
    """
    Background daemon thread that processes pending KVK companies in
    parallel using a ThreadPoolExecutor. Each worker owns its own DB
    session and runs the full pipeline (website search + Playwright
    scrape + MX-validated email pattern fallback).

    Throughput target: ~20 companies/minute, sized for 4000-record runs.
    """
    interval = settings.kvk_auto_enrich_interval
    batch_size = settings.kvk_auto_enrich_batch
    workers = max(1, getattr(settings, "kvk_auto_enrich_workers", 4))

    # One-time reset of stuck records left over from a previous container
    reset_count = _reset_stuck_running_records()
    if reset_count:
        print(f"[kvk-auto-enrich] Reset {reset_count} stuck records to 'pending'")

    while True:
        try:
            # Per-batch stuck-cleanup: free any record that has been
            # 'running'/'searching' for > 2 minutes (dead worker). Aggressive
            # because Playwright crashes are frequent on shared infra.
            stale = _reset_stuck_running_records(stale_after_minutes=2)
            if stale:
                print(f"[kvk-auto-enrich] Reset {stale} stale in-flight records")

            db = SessionLocal()
            try:
                pending_ids = [
                    row[0] for row in db.execute(
                        select(KvkCompany.id)
                        .where(KvkCompany.enrichment_status.in_(["pending", "no_website"]))
                        .where(KvkCompany.already_client_flag.is_(False))
                        .order_by(KvkCompany.id)
                        .limit(batch_size)
                    ).all()
                ]
            finally:
                db.close()

            if not pending_ids:
                print(f"[kvk-auto-enrich] No pending records — sleeping {interval}s")
                time.sleep(interval)
                continue

            print(f"[kvk-auto-enrich] Starting batch of {len(pending_ids)} ({workers} workers)")
            batch_start = time.monotonic()

            # Run the batch in parallel — each worker manages its own session.
            # Hard cap: BATCH_TIMEOUT seconds total. Any worker still running
            # after that is abandoned; its record will be re-picked up by the
            # 2-min stuck-cleanup. This prevents a single hung Playwright tab
            # from freezing the entire scheduler forever.
            BATCH_TIMEOUT = 180  # 3 min per batch hard cap
            pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="kvk-enrich")
            futures = [pool.submit(_enrich_one_company, cid) for cid in pending_ids]
            done, not_done = wait(futures, timeout=BATCH_TIMEOUT)
            for fut in done:
                try:
                    fut.result()
                except Exception:
                    pass
            # Abandon hung workers — don't wait on shutdown.
            # cancel_futures=True cancels queued (not-yet-started) tasks.
            pool.shutdown(wait=False, cancel_futures=True)

            elapsed = time.monotonic() - batch_start
            print(f"[kvk-auto-enrich] Batch done in {elapsed:.1f}s — {len(done)}/{len(futures)} completed, {len(not_done)} abandoned")

            # Short cool-down between batches, not after each company
            time.sleep(max(5, interval // 6))
        except Exception:
            time.sleep(interval)  # never crash the daemon


def start_auto_enrichment_scheduler() -> None:
    """Start the background enrichment daemon once (idempotent)."""
    global _scheduler_started
    if not settings.kvk_auto_enrich_enabled:
        return
    with _scheduler_lock:
        if _scheduler_started:
            return
        _scheduler_started = True
    t = Thread(target=_auto_enrich_loop, daemon=True, name="kvk-auto-enrich")
    t.start()


def get_enrichment_progress(db) -> dict:
    """Return live counts for dashboard progress display."""
    from sqlalchemy import func, select as sa_select
    total = db.scalar(sa_select(func.count(KvkCompany.id))) or 0
    pending = db.scalar(sa_select(func.count(KvkCompany.id)).where(
        KvkCompany.enrichment_status.in_(["pending", "no_website"]))) or 0
    in_progress = db.scalar(sa_select(func.count(KvkCompany.id)).where(
        KvkCompany.enrichment_status.in_(["searching", "running"]))) or 0
    done = db.scalar(sa_select(func.count(KvkCompany.id)).where(
        KvkCompany.enrichment_status.in_(["discovered", "partial"]))) or 0
    with_email = db.scalar(sa_select(func.count(KvkCompany.id)).where(
        KvkCompany.email_public != "")) or 0
    errors = db.scalar(sa_select(func.count(KvkCompany.id)).where(
        KvkCompany.enrichment_status == "error")) or 0
    pct = round(done / total * 100) if total else 0
    try:
        from app.google_search import is_enabled as _cse_enabled
        cse_on = _cse_enabled()
    except Exception:
        cse_on = False
    try:
        from app.brave_search import is_enabled as _brave_enabled, get_brave_usage
        brave_on = _brave_enabled()
        brave_usage = get_brave_usage()
    except Exception:
        brave_on = False
        brave_usage = {"used_today": 0, "daily_limit": 0, "remaining": 0}
    try:
        from app.bing_search import is_enabled as _bing_enabled
        bing_on = _bing_enabled()
    except Exception:
        bing_on = False
    try:
        from app.playwright_search import is_enabled as _pw_enabled
        pw_on = _pw_enabled()
    except Exception:
        pw_on = False
    return {
        "total": total,
        "pending": pending,
        "in_progress": in_progress,
        "done": done,
        "with_email": with_email,
        "errors": errors,
        "pct": pct,
        "active": _scheduler_started,
        "playwright_search_enabled": pw_on,
        "bing_search_enabled": bing_on,
        "brave_search_enabled": brave_on,
        "brave_usage": brave_usage,
        "google_cse_enabled": cse_on,
        "google_places_enabled": bool(settings.google_places_api_key),
    }
