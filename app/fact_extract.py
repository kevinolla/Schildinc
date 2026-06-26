"""Best-effort public-fact extraction from an accepted company website.

DESIGN_V2 Phase 2 (B). Given a website we ALREADY accepted as the company's own
(via discovery_open's precision gate), pull a small set of *public, factual*
marketing signals — premium brands carried, workshop/service emphasis, multi-
location, second-hand focus, a founding year, etc. — each with provenance and a
0-100 confidence.

Hard rules (mirroring the brief):
  * Best-effort only — every detector emits a fact ONLY when there is a concrete
    textual match. Nothing is invented; absence of a match means no fact.
  * Confidence is conservative. Only an unambiguous, distinctive signal
    (e.g. a named premium brand) clears the auto-trust threshold; soft/heuristic
    signals stay low so persist_facts marks them review_required and they are
    never operationalized downstream until a human clears them.
  * ``detect_facts_from_text`` is PURE (text in, facts out) and unit-testable.
    ``extract_facts`` adds the network fetch (lazy, via web_extract) on top.

The output is a list of plain dicts shaped for ``enrichment_facts.persist_facts``:
``{field_name, extracted_value, source_url, extraction_method, confidence}``.
Nothing here runs unless the caller has checked ``settings.discovery_facts_enabled``.
"""
from __future__ import annotations

import logging
import re

from app.config import settings

logger = logging.getLogger(__name__)

EXTRACTION_METHOD = "web_extract"

# Distinctive premium bike brands — chosen to avoid common English words
# (so we don't false-positive on "focus"/"giant"/"cube"/"scott"). A literal,
# word-boundary match on one of these is strong evidence -> high confidence.
_PREMIUM_BRANDS = (
    "gazelle", "batavus", "sparta", "koga", "stromer", "riese", "brompton",
    "vanmoof", "cortina", "kalkhoff", "qwic", "santos", "gepida", "kettler",
    "pegasus", "gudereit", "winora", "babboe", "urban arrow", "tern",
    "riese & müller", "riese und müller", "moustache", "flyer", "sinus",
)

# field_name -> (regex, confidence, value-label). Soft signals get <80 so they
# land in review; only premium-brand (handled separately) clears auto-trust.
_SOFT_PATTERNS: tuple[tuple[str, re.Pattern[str], int], ...] = (
    ("second_hand_signal", re.compile(r"\b(tweedehands|tweede\s*hands|occasion[s]?|gebruikte\s+fiets(?:en)?|second[\s-]?hand|refurbished)\b", re.I), 75),
    ("workshop_focus", re.compile(r"\b(werkplaats|reparatie[s]?|onderhoud(?:sbeurt)?|fietsenmaker|repair|workshop)\b", re.I), 70),
    ("service_focus", re.compile(r"\b(service|aftersales|after[\s-]?sales|garantie|onderhoudsabonnement)\b", re.I), 60),
    ("accessories_focus", re.compile(r"\b(accessoires|accessories|onderdelen|fietstassen|fietskleding|helmen|sloten|spare\s+parts)\b", re.I), 60),
    ("multi_location_signal", re.compile(r"\b(vestigingen|filialen|onze\s+winkels|meerdere\s+vestigingen|locaties|branches|stores\s+in)\b", re.I), 70),
    ("chain_or_hq_signal", re.compile(r"\b(franchise|hoofdkantoor|onderdeel\s+van|vestiging\s+van|profile\b|bike\s*totaal|biretco|fietsenwinkel\.nl|halfords)\b", re.I), 65),
    ("public_store_quality_signal", re.compile(r"\b(offici[eë]+(?:e?l)?\s+dealer|premium\s+dealer|premium\s+store|concept\s+store|flagship|award[s]?|erkend|beste\s+fietsenwinkel)\b", re.I), 70),
)

# Founding year / anniversary -> a concrete public fact.
_FOUNDED_RE = re.compile(r"\b(sinds|opgericht\s+in|since|established(?:\s+in)?|al\s+sinds)\s+((?:18|19|20)\d{2})\b", re.I)
# Public owner/founder name (conservative; low confidence -> always review).
_OWNER_RE = re.compile(r"\b(?:eigenaar|oprichter|owner|founder)\s*[:\-]?\s*([A-Z][a-zà-ÿ]+(?:\s+[A-Z][a-zà-ÿ]+){1,2})")


def _clip(value: str, limit: int = 240) -> str:
    value = " ".join(str(value or "").split())
    return value[:limit]


def detect_facts_from_text(text: str, *, source_url: str, company_name: str = "") -> list[dict]:
    """Pure detector: scan ``text`` and return any concrete facts found.

    Returns [] when nothing matches (no invented facts).
    """
    text = str(text or "")
    if not text.strip():
        return []
    low = text.lower()
    facts: list[dict] = []

    def _emit(field: str, value: str, confidence: int) -> None:
        facts.append({
            "field_name": field,
            "extracted_value": _clip(value),
            "source_url": source_url,
            "extraction_method": EXTRACTION_METHOD,
            "confidence": int(confidence),
        })

    # Premium brands — distinctive, literal word-boundary match -> high confidence.
    brands_found: list[str] = []
    for brand in _PREMIUM_BRANDS:
        if re.search(r"(?<![a-z])" + re.escape(brand) + r"(?![a-z])", low):
            brands_found.append(brand)
    if brands_found:
        _emit("premium_brand_signal", ", ".join(sorted(set(brands_found))), 85)

    # Soft keyword signals (each <80 -> review_required when persisted).
    for field, pattern, conf in _SOFT_PATTERNS:
        m = pattern.search(text)
        if m:
            _emit(field, m.group(0), conf)

    # repair_first heuristic: workshop terms present AND clear retail signals
    # absent. Heuristic -> low confidence (always review).
    workshop_hits = len(re.findall(r"\b(werkplaats|reparatie|onderhoud|repair)\b", low))
    retail_hits = len(re.findall(r"\b(webshop|kopen|assortiment|merken|collectie|shop\s+online|nieuwe\s+fietsen)\b", low))
    if workshop_hits >= 2 and retail_hits == 0:
        _emit("repair_first_signal", "repair/workshop-oriented (no clear retail signal)", 50)

    # Founding year / anniversary -> concrete public store fact.
    fm = _FOUNDED_RE.search(text)
    if fm:
        _emit("public_store_fact", f"founded/active since {fm.group(2)}", 75)

    # Public owner/founder name -> conservative, always review.
    om = _OWNER_RE.search(text)
    if om:
        _emit("public_owner_name", om.group(1).strip(), 45)

    # Business description — the site's own opening prose (factual: their words).
    desc = _clip(text, 220)
    if len(desc) >= 40:
        _emit("business_description", desc, 70)

    return facts


def _merge_best(facts: list[dict]) -> list[dict]:
    """De-dupe by (field_name, source_url), keeping the highest-confidence one."""
    best: dict[tuple[str, str], dict] = {}
    for f in facts:
        key = (f["field_name"], f.get("source_url", ""))
        cur = best.get(key)
        if cur is None or int(f.get("confidence", 0)) > int(cur.get("confidence", 0)):
            best[key] = f
    return list(best.values())


def extract_facts(website: str, company_name: str = "", country: str = "NL") -> list[dict]:
    """Fetch the accepted website (home + maybe one about page) and detect facts.

    Network is via ``web_extract`` (lazy import; same fetch stack as contact
    discovery). Never raises — returns [] on any problem.
    """
    website = str(website or "").strip()
    if not website:
        return []
    try:
        from app import web_extract  # type: ignore
    except Exception as exc:  # noqa: BLE001
        logger.info("fact_extract: web_extract unavailable (%s)", exc)
        return []

    max_pages = max(1, int(getattr(settings, "fact_extract_max_pages", 2)))
    try:
        home_url = web_extract.ensure_http_url(website)
        home_html = web_extract.fetch_html(home_url)
    except Exception as exc:  # noqa: BLE001
        logger.info("fact_extract: fetch failed for %s (%s)", website, exc)
        return []
    if not home_html:
        return []

    pages: list[tuple[str, str]] = [(home_url, web_extract.main_text(home_html))]

    # Optionally pull ONE about/over-ons page for richer facts.
    if max_pages > 1:
        try:
            for link in web_extract.contact_page_links(home_html, home_url):
                if any(k in link.lower() for k in ("about", "over-ons", "overons")):
                    sub = web_extract.fetch_html(link)
                    if sub:
                        pages.append((link, web_extract.main_text(sub)))
                    break
        except Exception:  # noqa: BLE001
            pass

    all_facts: list[dict] = []
    for url, text in pages:
        all_facts.extend(detect_facts_from_text(text, source_url=url, company_name=company_name))
    return _merge_best(all_facts)
