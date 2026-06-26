"""Non-Google ("open stack") discovery orchestrator.

PURPOSE
-------
Given the *minimal* facts we hold about a company (name, city, country, postal,
and sometimes an already-known website) this module produces a single, fully
graded discovery outcome:

    name + city + country  ->  website  ->  public email / phone / socials

It is the glue layer of the open-discovery pipeline described in the redesign
spec. It deliberately owns NO scraping or HTTP logic of its own — it delegates:

  * name -> website          : ``app.search_client``   (SearXNG, no Google)
  * website -> contacts      : ``app.web_extract``     (httpx + Trafilatura,
                                reusing the proven rankers in ``app.discovery``)

This module's only job is *decision-making + grading*:

  1. If a website is already known -> trust it (input_type="existing_website",
     confidence 100) and go straight to contact extraction.
  2. Otherwise, if a SearXNG backend is configured, search for the company's
     own site, rank each candidate domain/title against the company name with
     RapidFuzz, and pick the best candidate ONLY if it clears the configured
     confidence threshold. Below threshold -> the row is flagged
     ``needs_review`` and the ranked candidates are returned for a human to
     resolve in the ``/review/discovery`` queue.
  3. Once we have a confident website, extract contacts from it.

GRACEFUL FALLBACK (the whole point of "open" + "additive")
----------------------------------------------------------
Every external capability degrades to a no-op instead of throwing:

  * ``settings.discovery_engine == "google"`` -> we DO NOT call Google here.
    Instead we return an outcome flagged ``backend="google"`` /
    ``status="use_google_fallback"`` so the *caller* can route the row to the
    legacy ``app.discovery`` / ``app.kvk_enrichment`` Google path. Keeping the
    Google call out of this module keeps the open stack cleanly separable.
  * SearXNG not configured (``search_client.is_configured()`` is False) and no
    known website -> nothing to search with -> ``status="needs_review"`` (the
    row lands in manual review). No exception.
  * ``app.search_client`` / ``app.web_extract`` not importable yet (built in
    parallel) -> caught, logged at INFO, degrade to needs_review / no-op.
  * RapidFuzz missing -> a cheap token-overlap fallback scorer is used so the
    pipeline still ranks candidates.

Nothing in this module raises into a scheduler. Everything returns a
``DiscoveryOutcome``. With no new env vars set the module imports cleanly and
``discover_for_company`` simply produces ``needs_review`` outcomes (or, for
rows that already have a website, runs extraction) — i.e. it never changes
today's behavior on its own.

This module is intentionally PURE of any DB writes. It takes a ``session``
purely so it can hand it to ``web_extract`` (some extractors may want it for
caching / suppression checks), and so that the public signature is stable for
the integrator who will persist the outcome onto KvkCompany / Prospect rows.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

# NOTE: ``Session`` is only used as a type annotation. We import it lazily-ish
# from SQLAlchemy (a hard dependency of the app, always present) so the module
# stays import-safe. Heavy / optional deps (rapidfuzz, httpx, trafilatura) are
# imported INSIDE functions per the house rules.
from sqlalchemy.orm import Session

from app.config import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tunables read defensively from settings (integrator adds these later).
# getattr(..., default) keeps the module importable even before the env vars
# exist in app/config.py.
# ---------------------------------------------------------------------------

def _confidence_threshold() -> int:
    """Minimum 0-100 score for a searched website to be auto-trusted.

    Below this, the company goes to the manual review queue instead of being
    silently believed. Defaults to 60 (matches the spec). Also accepts the
    older / alternate name ``match_review_min_score`` is intentionally NOT used
    here — that one governs Klant matching, a different concern.
    """
    raw = getattr(settings, "discovery_review_threshold", 60)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 60


def _engine_mode() -> str:
    """Which discovery engine to use: 'open' (SearXNG) or 'google' (legacy).

    The assignment spec names this ``settings.discovery_engine`` (default
    "open"). The broader redesign also references ``discovery_backend``
    ("auto"|"searxng"|"google"). We honor ``discovery_engine`` first (it is the
    name in our brief) and treat anything that isn't exactly "google" as the
    open path, so unknown/typo values fail safe toward the open stack.
    """
    return str(getattr(settings, "discovery_engine", "open") or "open").strip().lower()


# A confident-enough candidate to auto-pick. The brief says "~80"; we read it
# from settings so the integrator can tune precision without code changes.
# Falls back to max(threshold, 80) so we never auto-pick below the review bar.
def _autopick_score() -> int:
    raw = getattr(settings, "discovery_autopick_score", None)
    if raw is not None:
        try:
            return int(raw)
        except (TypeError, ValueError):
            pass
    return max(_confidence_threshold(), 80)


# --- DESIGN_V2 Phase 2: recall tunables (read defensively, OFF by default) ---

def _recall_variants_enabled() -> bool:
    return bool(getattr(settings, "discovery_recall_variants_enabled", False))


def _max_candidates() -> int:
    try:
        return max(1, int(getattr(settings, "discovery_max_candidates", 8)))
    except (TypeError, ValueError):
        return 8


def _max_variants() -> int:
    try:
        return max(1, int(getattr(settings, "discovery_max_query_variants", 6)))
    except (TypeError, ValueError):
        return 6


def _variant_search_limit() -> int:
    try:
        return max(1, int(getattr(settings, "discovery_variant_search_limit", 5)))
    except (TypeError, ValueError):
        return 5


# Sector clue terms appended to recall variants. Bike-first (the main segment);
# adding these is precision-safe — a wrong domain a clue surfaces is still floored
# by _fuzzy_score's distinctive-token gate, so clues only help find real sites.
_DEFAULT_SECTOR_TERMS = ("fietsenwinkel", "tweewielers", "fietsen", "bike shop", "bicycle")


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------

@dataclass
class WebsiteChoice:
    """A single ranked candidate website surfaced by the search backend.

    This mirrors the shape of ``search_client.WebsiteCandidate`` but we keep our
    own lightweight copy so this module does not hard-depend on that class
    existing at import time (it is built in parallel). Fields are populated
    best-effort from whatever the search client returns.
    """
    url: str = ""
    domain: str = ""
    title: str = ""
    snippet: str = ""
    score: int = 0  # 0-100 confidence this is the company's OWN site
    engine: str = ""


@dataclass
class DiscoveryOutcome:
    """The full, graded result of one open-discovery attempt.

    The integrator persists these onto KvkCompany / Prospect columns
    (website, website_domain, email_public/email, *_source_page,
    *_confidence, discovery_query_used, discovery_input_type, ...).

    ``status`` values:
      * ``found``               website + at least one strong contact
      * ``partial``             website found, contacts incomplete
      * ``no_contacts``         website found but no usable contacts
      * ``no_website``          could not determine a website
      * ``needs_review``        low-confidence candidate(s) -> human queue
      * ``use_google_fallback`` engine=google: caller should run legacy path
    """
    website: str = ""
    website_domain: str = ""
    website_confidence: int = 0

    email_public: str = ""
    email_source_page: str = ""
    email_confidence: int = 0

    phone_public: str = ""
    phone_source_page: str = ""
    phone_confidence: int = 0

    whatsapp_number: str = ""
    whatsapp_url: str = ""
    linkedin_url: str = ""
    instagram_url: str = ""

    emails_found: list[str] = field(default_factory=list)
    pages_scanned: list[str] = field(default_factory=list)

    discovery_query_used: str = ""
    discovery_input_type: str = ""  # "existing_website" | "search"
    backend: str = "open"           # "open" | "google" | "manual"

    status: str = "needs_review"
    candidates: list[WebsiteChoice] = field(default_factory=list)
    needs_review: bool = False
    error: str = ""


# ---------------------------------------------------------------------------
# Internal helpers (pure — safe to unit test directly)
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Domains that are directories / marketplaces / social — never a company's own
# canonical website. Used as a guard if the search client's own scoring failed
# to demote them.
_DIRECTORY_HINTS = (
    # Social / search / encyclopaedic
    "facebook.", "instagram.", "linkedin.", "twitter.", "x.com",
    "youtube.", "tiktok.", "pinterest.", "google.", "maps.google",
    "wikipedia.", "reddit.", "medium.com",
    # Marketplaces / big chain retailers (a small shop is never these)
    "marktplaats.", "amazon.", "ebay.", "etsy.", "bol.com", "2dehands.",
    "fietsenwinkel.nl", "mantel.com", "decathlon.", "bikester.", "fietsenkoning.",
    # Review / rating
    "yelp.", "tripadvisor.", "trustpilot.", "kieskeurig.", "tweakers.",
    # NL/BE/DE business directories & listing/aggregator sites (the actual
    # false-positives seen in production: telefoonboek, zundapp.one, fiets-zaken)
    "telefoonboek.", "detelefoongids.", "telefoongids.", "goudengids.",
    "yellowpages.", "oozo.", "cylex.", "openingstijden.", "drimble.",
    "company.info", "bedrijvenpagina.", "indebuurt.", "dichtbij.", "opendi.",
    "infobel.", "stadsgids.", "ondernemersplein.", "kvk.nl", "kvknummer.",
    "zundapp.one", "fiets-zaken.", "bedrijvengids.", "lokaalgids.", "wijkgids.",
)


def _tokens(value: str) -> set[str]:
    """Lowercase alphanumeric tokens, dropping noise/legal-form words."""
    raw = _TOKEN_RE.findall(str(value or "").lower())
    noise = {
        "bv", "b", "v", "nv", "vof", "gmbh", "ag", "sarl", "sas", "ltd",
        "inc", "the", "and", "en", "und", "et", "de", "het", "een",
        "www", "com", "nl", "be", "fr", "info", "shop", "store", "online",
    }
    return {t for t in raw if t not in noise and len(t) > 1}


# Generic industry/legal words that are NOT distinctive enough to identify a
# specific shop's domain. A match on these alone (e.g. company "X Bikes" -> a
# random domain containing "bikes") must NOT be trusted — only a match on the
# DISTINCTIVE part of the name (the proper noun) is reliable.
_GENERIC_INDUSTRY = {
    "fiets", "fietsen", "fietsenwinkel", "fietsenwinkels", "fietsenzaak", "fietsspeciaalzaak",
    "rijwiel", "rijwielen", "rijwielhandel", "rijwielspeciaalzaak", "tweewieler", "tweewielers",
    "tweewielercentrum", "bike", "bikes", "biking", "cycle", "cycles", "cycling", "cyclewerks",
    "scooter", "scooters", "brommer", "bromfiets", "bromfietsen", "sport", "sports", "shop",
    "store", "winkel", "handel", "centrum", "company", "holding", "beheer", "service", "repair",
    "the", "and", "voor", "alles", "city", "stad", "fa", "firma",
}


def _wratio(a: str, b: str) -> int:
    """RapidFuzz WRatio (lazy import) with a Jaccard fallback. 0-100."""
    if not a or not b:
        return 0
    try:
        from rapidfuzz import fuzz  # type: ignore
        return int(fuzz.WRatio(a, b))
    except Exception:  # noqa: BLE001
        ta, tb = _tokens(a), _tokens(b)
        if not ta or not tb:
            return 0
        return int(100 * len(ta & tb) / len(ta | tb))


def _fuzzy_score(company_name: str, candidate: WebsiteChoice) -> int:
    """Score 0-100 how likely ``candidate`` is the company's OWN website.

    Precision-first strategy (cold outreach data must be trustworthy):
      1. Known directory/social/marketplace domains -> floored to 5 (review).
      2. The trustworthy signal is the DOMAIN itself, matched against the
         DISTINCTIVE (non-generic) part of the company name. If a distinctive
         name token (>=4 chars) appears in the domain label, it's almost
         certainly the shop's own site -> high score.
      3. Otherwise we DO NOT trust page titles/URLs (a directory, a newspaper,
         or an unrelated site can echo the company name in its title). Such
         candidates are capped below the auto-pick bar so they go to the human
         review queue instead of being auto-accepted. This is what stops false
         positives like marriott.com / telegraaf.nl / shopee.com.
    """
    domain = (candidate.domain or "").lower()
    if domain.startswith("www."):
        domain = domain[4:]
    if any(hint in domain for hint in _DIRECTORY_HINTS):
        return 5

    domain_root = domain.split(".")[0]                       # the brand label
    distinctive = {t for t in _tokens(company_name) if len(t) >= 4 and t not in _GENERIC_INDUSTRY}

    # Strong, trustworthy: a distinctive name token is literally in the domain.
    if any(tok in domain_root for tok in distinctive):
        return max(85, _wratio(company_name, domain_root))

    # No distinctive domain match -> never auto-pick. Cap below the autopick bar
    # (we still return a small score so the candidate is surfaced for review).
    return min(45, _wratio(company_name, domain_root))


def _build_query(name: str, city: str, country: str, postal: str) -> str:
    """Compose the search string handed to the search backend.

    Keep it simple and human-like: ``"<name> <city> <postal>"`` plus an
    ``official website`` nudge. Country is appended only when it adds signal
    (i.e. not the default NL, where city already disambiguates well enough —
    but we still include a clean form for non-NL rows).
    """
    parts = [str(name or "").strip()]
    if city:
        parts.append(str(city).strip())
    if postal:
        parts.append(str(postal).strip())
    cc = str(country or "").strip()
    if cc and cc.upper() not in {"NL", "NETHERLANDS", "NETHERLAND"}:
        parts.append(cc)
    query = " ".join(p for p in parts if p)
    return query.strip()


def _coerce_candidate(raw: Any) -> WebsiteChoice:
    """Best-effort adapt whatever ``search_client`` returns into WebsiteChoice.

    Accepts either a dataclass/object with attributes or a plain dict, so this
    orchestrator does not break if the parallel ``search_client`` author named
    a field slightly differently. Missing fields default to "".
    """
    def _get(key: str, *aliases: str) -> Any:
        if isinstance(raw, dict):
            for k in (key, *aliases):
                if k in raw and raw[k] is not None:
                    return raw[k]
            return ""
        for k in (key, *aliases):
            if hasattr(raw, k):
                val = getattr(raw, k)
                if val is not None:
                    return val
        return ""

    url = str(_get("url", "link", "href") or "")
    domain = str(_get("domain", "host") or "")
    if not domain and url:
        # Derive domain from the URL using the existing util (always present).
        try:
            from app.utils import normalize_domain

            domain = normalize_domain(url)
        except Exception:  # noqa: BLE001
            domain = ""

    score_raw = _get("score", "confidence", "rank")
    try:
        score = int(score_raw) if score_raw != "" else 0
    except (TypeError, ValueError):
        score = 0

    return WebsiteChoice(
        url=url,
        domain=domain,
        title=str(_get("title", "name") or ""),
        snippet=str(_get("snippet", "description", "content") or ""),
        score=score,
        engine=str(_get("engine", "source") or ""),
    )


def _normalize_extract_result(raw: Any, website: str) -> dict[str, Any]:
    """Normalize whatever ``web_extract`` returns into a plain dict.

    The parallel ``web_extract`` module may return an ``ExtractedContacts``
    dataclass (per the redesign) or a ``DiscoveryResult``-shaped object. We map
    the union of likely field names so the orchestrator is resilient. Anything
    absent defaults to empty.
    """
    def _get(key: str, *aliases: str, default: Any = "") -> Any:
        if raw is None:
            return default
        if isinstance(raw, dict):
            for k in (key, *aliases):
                if k in raw and raw[k] is not None:
                    return raw[k]
            return default
        for k in (key, *aliases):
            if hasattr(raw, k):
                val = getattr(raw, k)
                if val is not None:
                    return val
        return default

    emails_found = _get("emails_found", default=[]) or []
    if isinstance(emails_found, str):
        emails_found = [e for e in re.split(r"[,\s;]+", emails_found) if e]
    pages_scanned = _get("pages_scanned", default=[]) or []
    if isinstance(pages_scanned, str):
        pages_scanned = [p for p in re.split(r"[,\s;]+", pages_scanned) if p]

    return {
        "email": str(_get("email", "email_public", default="") or ""),
        "email_source_page": str(
            _get("email_source_url", "email_source_page", "source_page", default="") or ""
        ),
        "email_confidence": _as_int(_get("email_confidence", "confidence", default=0)),
        "phone": str(_get("phone", "phone_public", "phone_number", default="") or ""),
        "phone_source_page": str(_get("phone_source_url", "phone_source_page", default="") or ""),
        "phone_confidence": _as_int(_get("phone_confidence", default=0)),
        "whatsapp_number": str(_get("whatsapp_number", default="") or ""),
        "whatsapp_url": str(_get("whatsapp_url", default="") or ""),
        "linkedin_url": str(_get("linkedin_url", default="") or ""),
        "instagram_url": str(_get("instagram_url", default="") or ""),
        "emails_found": [str(e) for e in emails_found],
        "pages_scanned": [str(p) for p in pages_scanned],
        "status": str(_get("status", default="") or ""),
        "error": str(_get("error", default="") or ""),
    }


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _call_web_extract(session: Session, website: str, name: str, city: str) -> Any:
    """Invoke the sibling ``web_extract`` module, tolerant of its exact API.

    The redesign names the function ``extract_contacts``; the assignment brief
    names it ``discover_contacts``. We try both (and a couple of obvious
    variants) so whichever the parallel author shipped, we connect to it. If
    the module/function is missing entirely we return None and the caller
    degrades to ``no_contacts``.

    Heavy imports happen INSIDE this function (house rule).
    """
    try:
        from app import web_extract  # type: ignore
    except Exception as exc:  # noqa: BLE001 - module built in parallel / dep missing
        logger.info("discovery_open: web_extract unavailable (%s); skipping extraction", exc)
        return None

    fn = None
    for fn_name in ("discover_contacts", "extract_contacts", "discover", "extract"):
        cand = getattr(web_extract, fn_name, None)
        if callable(cand):
            fn = cand
            break
    if fn is None:
        logger.info("discovery_open: no extract function found on web_extract")
        return None

    # Try the richer signature first (with session), fall back to website-only.
    # Different parallel implementations may or may not accept a session.
    attempts = (
        lambda: fn(session, website=website, company_name=name, city=city),
        lambda: fn(website=website, company_name=name, city=city),
        lambda: fn(session, website),
        lambda: fn(website),
    )
    for attempt in attempts:
        try:
            return attempt()
        except TypeError:
            continue  # signature mismatch -> try the next shape
        except Exception as exc:  # noqa: BLE001 - extractor must never crash us
            logger.info("discovery_open: web_extract call failed (%s)", exc)
            return None
    logger.info("discovery_open: could not match web_extract signature")
    return None


def _status_from_contacts(contacts: dict[str, Any]) -> str:
    """Grade extraction completeness into found / partial / no_contacts."""
    # Honor an explicit status from the extractor when it gave a useful one.
    explicit = contacts.get("status", "")
    if explicit in {"found", "partial", "no_contacts", "no_website", "error"}:
        return explicit
    has_email = bool(contacts.get("email"))
    has_other = bool(
        contacts.get("phone")
        or contacts.get("whatsapp_number")
        or contacts.get("linkedin_url")
        or contacts.get("instagram_url")
    )
    if has_email:
        return "found"
    if has_other:
        return "partial"
    return "no_contacts"


def _outcome_from_website(
    session: Session,
    *,
    website: str,
    website_confidence: int,
    query_used: str,
    input_type: str,
    candidates: list[WebsiteChoice],
) -> DiscoveryOutcome:
    """Run extraction against a chosen website and assemble the outcome.

    Shared by both the existing-website path and the confident-search path.
    """
    domain = ""
    try:
        from app.utils import normalize_domain

        domain = normalize_domain(website)
    except Exception:  # noqa: BLE001
        domain = ""

    raw = _call_web_extract(session, website, "", "")
    contacts = _normalize_extract_result(raw, website)
    status = _status_from_contacts(contacts)

    return DiscoveryOutcome(
        website=website,
        website_domain=domain,
        website_confidence=int(website_confidence),
        email_public=contacts["email"],
        email_source_page=contacts["email_source_page"],
        email_confidence=contacts["email_confidence"],
        phone_public=contacts["phone"],
        phone_source_page=contacts["phone_source_page"],
        phone_confidence=contacts["phone_confidence"],
        whatsapp_number=contacts["whatsapp_number"],
        whatsapp_url=contacts["whatsapp_url"],
        linkedin_url=contacts["linkedin_url"],
        instagram_url=contacts["instagram_url"],
        emails_found=contacts["emails_found"],
        pages_scanned=contacts["pages_scanned"],
        discovery_query_used=query_used,
        discovery_input_type=input_type,
        backend="open",
        status=status,
        candidates=candidates,
        needs_review=False,
        error=contacts["error"],
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def discover_for_company(
    session: Session,
    *,
    name: str,
    city: str = "",
    country: str = "NL",
    postal: str = "",
    website: str = "",
    sector_terms: tuple[str, ...] | None = None,
) -> DiscoveryOutcome:
    """Orchestrate non-Google discovery for one company.

    Args:
        session:  SQLAlchemy session (passed through to ``web_extract``).
        name:     Company / business name (required; "" -> needs_review).
        city:     City (improves search precision).
        country:  ISO-2 or full country name (default "NL").
        postal:   Postal/zip code (improves search precision).
        website:  An already-known website. If present we skip search entirely.

    Returns:
        A ``DiscoveryOutcome``. NEVER raises — any failure degrades to a
        ``needs_review`` / ``no_contacts`` / ``no_website`` outcome.

    Decision flow:
        1. engine == "google" -> return a marker outcome
           (status="use_google_fallback") so the caller runs the legacy path.
           We never call Google from here.
        2. website provided -> input_type="existing_website", confidence 100,
           go straight to extraction.
        3. search backend configured -> search, rank candidates by name match,
           auto-pick the top one if it clears the autopick score, else flag
           needs_review and return the ranked candidates for the review queue.
        4. nothing to search with -> needs_review (status reflects why).
    """
    name = str(name or "").strip()

    # -- Step 1: explicit Google engine -> hand back to the legacy caller ----
    if _engine_mode() == "google":
        # Documented marker: this module intentionally does NOT call Google.
        # The integrator's caller checks for this and runs the existing
        # app.discovery / app.kvk_enrichment Google pipeline instead.
        return DiscoveryOutcome(
            backend="google",
            status="use_google_fallback",
            discovery_input_type="search",
            needs_review=False,
            error="discovery_engine=google: caller should use legacy Google path",
        )

    # -- Step 2: an already-known website is the strongest signal we have ----
    if website and str(website).strip():
        website = str(website).strip()
        return _outcome_from_website(
            session,
            website=website,
            website_confidence=100,  # we already trust this URL
            query_used="",
            input_type="existing_website",
            candidates=[],
        )

    # Below here we MUST search to find a website. We need a name to do so.
    if not name:
        return DiscoveryOutcome(
            backend="manual",
            status="needs_review",
            discovery_input_type="search",
            needs_review=True,
            error="no name and no website: cannot search",
        )

    query = _build_query(name, city, country, postal)

    # -- Step 3: open search via SearXNG (sibling module) --------------------
    # DESIGN_V2 Phase 2 recall: when enabled, widen the candidate net with
    # several query variants (precision gate below is unchanged). Falls back to
    # the single-query path if the variant path surfaces nothing.
    if _recall_variants_enabled():
        candidates = _collect_candidates_multi(name, city, country, postal, sector_terms)
        if not candidates:
            candidates = _search_candidates(name, city, country, query)
    else:
        candidates = _search_candidates(name, city, country, query)

    if not candidates:
        # Either the backend is unconfigured, or it genuinely returned nothing.
        # Both cases -> a human must resolve this row. Never silently "no site".
        return DiscoveryOutcome(
            backend="manual",
            status="needs_review",
            discovery_query_used=query,
            discovery_input_type="search",
            candidates=[],
            needs_review=True,
            error="no website candidates from search backend",
        )

    # Score every candidate with OUR precision-first scorer and sort best-first.
    # We deliberately do NOT max() with the search backend's own score: the
    # backend ranks by relevance and will happily score a directory/listing
    # page (telefoonboek.nl, cylex.nl, zundapp.one) at 100 because the company
    # name appears in its title/URL. _fuzzy_score is the authoritative gate —
    # it floors directories and only trusts a distinctive name token IN THE
    # DOMAIN — so it must be the score the autopick decision uses.
    for cand in candidates:
        cand.score = _fuzzy_score(name, cand)
    # Primary sort key is the precision score (decides acceptance); _rank_bonus
    # is only a tiebreaker among equal scores for nicer review-queue ordering.
    candidates.sort(key=lambda c: (c.score, _rank_bonus(name, city, c)), reverse=True)

    best = candidates[0]
    autopick = _autopick_score()

    if best.score >= autopick and best.url:
        # Confident enough: trust this site and extract contacts from it.
        outcome = _outcome_from_website(
            session,
            website=best.url,
            website_confidence=best.score,
            query_used=query,
            input_type="search",
            candidates=candidates,
        )
        return outcome

    # -- Low confidence: route to manual review with the ranked candidates ---
    return DiscoveryOutcome(
        website="",
        website_domain="",
        website_confidence=best.score if best else 0,
        discovery_query_used=query,
        discovery_input_type="search",
        backend="open",
        status="needs_review",
        candidates=candidates,
        needs_review=True,
        error=f"top candidate score {best.score} below autopick {autopick}",
    )


def _query_variants(
    name: str, city: str, country: str, postal: str, sector_terms: tuple[str, ...] | None = None
) -> list[str]:
    """Build several human-like search queries for one company (recall).

    Order matters: strongest/most-specific first. Variants are de-duplicated
    (case-insensitive) and capped at ``_max_variants()``. The acceptance gate is
    unchanged — these only widen the candidate net.
    """
    name = str(name or "").strip()
    if not name:
        return []
    city = str(city or "").strip()
    terms = sector_terms if sector_terms is not None else _DEFAULT_SECTOR_TERMS

    out: list[str] = []
    seen: set[str] = set()

    def _add(q: str) -> None:
        q = " ".join(str(q or "").split()).strip()
        if q and q.lower() not in seen:
            seen.add(q.lower())
            out.append(q)

    _add(_build_query(name, city, country, postal))   # current behaviour, strongest
    _add(f"{name} {city}" if city else name)
    _add(name)
    for term in terms:
        _add(f"{name} {term} {city}" if city else f"{name} {term}")
    return out[: _max_variants()]


def _search_one_query(query: str, limit: int) -> list[WebsiteChoice]:
    """Run ONE specific query against search_client.search. Import-safe; [] on any miss."""
    try:
        from app import search_client  # type: ignore
    except Exception as exc:  # noqa: BLE001
        logger.info("discovery_open: search_client unavailable (%s)", exc)
        return []
    is_configured = getattr(search_client, "is_configured", None)
    if callable(is_configured):
        try:
            if not is_configured():
                return []
        except Exception:  # noqa: BLE001
            return []
    fn = getattr(search_client, "search", None)
    if not callable(fn):
        return []
    raw = None
    for attempt in (lambda: fn(query, limit), lambda: fn(query)):
        try:
            raw = attempt()
            break
        except TypeError:
            continue
        except Exception as exc:  # noqa: BLE001 - search must never crash us
            logger.info("discovery_open: search() failed (%s)", exc)
            return []
    out: list[WebsiteChoice] = []
    for r in raw or []:
        try:
            out.append(_coerce_candidate(r))
        except Exception:  # noqa: BLE001
            continue
    return out


def _collect_candidates_multi(
    name: str, city: str, country: str, postal: str, sector_terms: tuple[str, ...] | None
) -> list[WebsiteChoice]:
    """Run several query variants and merge their candidates (dedup by domain).

    Returns up to ``_max_candidates()`` distinct candidate domains. The caller
    then scores + gates them with the UNCHANGED precision logic.
    """
    variants = _query_variants(name, city, country, postal, sector_terms)
    if not variants:
        return []
    per = _variant_search_limit()
    cap = _max_candidates()
    seen: dict[str, WebsiteChoice] = {}
    order: list[str] = []
    for q in variants:
        for cand in _search_one_query(q, per):
            dom = (cand.domain or "").lower()
            if dom.startswith("www."):
                dom = dom[4:]
            if not dom or dom in seen:
                continue
            seen[dom] = cand
            order.append(dom)
            if len(seen) >= cap:
                break
        if len(seen) >= cap:
            break
    return [seen[d] for d in order]


def _rank_bonus(name: str, city: str, candidate: WebsiteChoice) -> int:
    """A small 0-100 TIEBREAKER (title/city/snippet match) used only to order
    candidates of equal _fuzzy_score. It NEVER affects acceptance — acceptance
    is decided solely by _fuzzy_score vs the autopick threshold."""
    title = candidate.title or ""
    snippet = candidate.snippet or ""
    bonus = _wratio(name, title)
    city = str(city or "").strip().lower()
    if city and (city in title.lower() or city in snippet.lower()):
        bonus += 20
    return min(100, bonus)


def _search_candidates(
    name: str, city: str, country: str, query: str
) -> list[WebsiteChoice]:
    """Call the sibling ``search_client`` to get ranked website candidates.

    Tolerant of the parallel module's exact API and import-safe: if the module
    or function is missing, or the backend is unconfigured, returns []. Heavy
    deps are imported inside ``search_client`` itself, so importing it here is
    cheap and safe.
    """
    try:
        from app import search_client  # type: ignore
    except Exception as exc:  # noqa: BLE001 - built in parallel / not present yet
        logger.info("discovery_open: search_client unavailable (%s)", exc)
        return []

    # Respect the backend's own "configured?" gate when it exposes one.
    is_configured = getattr(search_client, "is_configured", None)
    if callable(is_configured):
        try:
            if not is_configured():
                logger.info("discovery_open: search backend not configured; skipping search")
                return []
        except Exception:  # noqa: BLE001
            return []

    fn = None
    for fn_name in ("find_website", "search", "find_candidates"):
        cand = getattr(search_client, fn_name, None)
        if callable(cand):
            fn = cand
            break
    if fn is None:
        logger.info("discovery_open: no search function on search_client")
        return []

    # Try a few call shapes (kwargs first, then a positional query) so we work
    # with whatever signature the parallel author shipped.
    cc = str(country or "")
    attempts = (
        lambda: fn(name, city=city, country_code=cc),
        lambda: fn(name, city, cc),
        lambda: fn(query),
        lambda: fn(name),
    )
    raw_list = None
    for attempt in attempts:
        try:
            raw_list = attempt()
            break
        except TypeError:
            continue
        except Exception as exc:  # noqa: BLE001 - search must never crash us
            logger.info("discovery_open: search_client call failed (%s)", exc)
            return []

    if not raw_list:
        return []
    try:
        return [_coerce_candidate(r) for r in raw_list]
    except Exception as exc:  # noqa: BLE001
        logger.info("discovery_open: could not adapt search results (%s)", exc)
        return []
