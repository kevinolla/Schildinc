"""Directory crawler — always-on, multi-job business discovery (migration 0026).

The operator creates CrawlJob rows from /crawler ("all Bike shops in Germany",
"Woodwork + Furniture in Amsterdam + Rotterdam", ...). A background scheduler
runs up to ``CRAWLER_MAX_CONCURRENT_JOBS`` jobs at the same time.

Geography is country-first, optionally narrowed to cities: an empty city list
sweeps the whole country (one country-wide OSM query per sector tag + the full
major-city grid for web search); a city list restricts both stages to exactly
those cities (any city name works — not just the built-in grid).

Business sources — 100% free, no Google Cloud:

1. **OpenStreetMap Overpass** (primary): structured business listings by
   sector tag (``shop=bicycle``, ``craft=carpenter``, ...) with real names,
   addresses, websites, phones — and often a tagged public email, which skips
   site-crawling entirely.
2. **SearXNG** (self-hosted meta-search): localized "sector-term city" web
   queries fill in businesses OSM doesn't know; directory/platform domains are
   blocklisted.

Every hit is deduped against prospects (source ref + website domain) AND the
KVK pool (domain), stored into ``prospects`` with ``source='crawler'``, then a
public email is extracted via ``web_extract.discover_contacts`` (visible-page
scan + MX-validated info@domain fallback). Exact-email matches against
existing customers are flagged so they never enter cold outreach.

Jobs are resumable: the plan is re-derived from (sectors, country, cities) and
resumed at ``queries_done``, so a deploy/restart never loses progress. Pausing
sets ``status='paused'``; the worker notices between queries and exits cleanly.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlencode

from sqlalchemy import func, select

from app.config import settings
from app.country_codes import to_iso2
from app.db import SessionLocal
from app.models import CrawlJob, Customer, KvkCompany, MatchStatus, Prospect
from app.utils import normalize_domain
from app.web_extract import discover_contacts

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# Strict shape check on extracted emails — page scrapes occasionally pick up
# trailing junk (e.g. "info@site.de\") that would bounce at send time.
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def _clean_email(value: str) -> str:
    email = (value or "").strip().strip("\\/.,;:<>()[]'\"").lower()
    return email if _EMAIL_RE.match(email) else ""


# ──────────────────────────────────────────────────────────────────────────
# Query vocabulary: localized search terms per sector per country
# ──────────────────────────────────────────────────────────────────────────
# Keys are the canonical sector names from lead_classifier.SECTORS. Each maps
# ISO-2 country -> Places text-search terms in the local language. "default"
# is the English fallback for countries without a dedicated list. Two terms
# per sector keeps cost predictable (queries = cities x terms x sectors).

SECTOR_SEARCH_TERMS: dict[str, dict[str, list[str]]] = {
    "Bike": {
        "NL": ["fietsenwinkel", "fietsenmaker"],
        "BE": ["fietsenwinkel", "magasin de vélos"],
        "DE": ["fahrradladen", "fahrradgeschäft"],
        "FR": ["magasin de vélos", "réparateur de vélos"],
        "GB": ["bike shop", "bicycle shop"],
        "US": ["bike shop", "bicycle store"],
        "default": ["bike shop", "bicycle store"],
    },
    "Candles": {
        "NL": ["kaarsenwinkel", "kaarsenmakerij"],
        "DE": ["kerzengeschäft", "kerzenmanufaktur"],
        "FR": ["boutique de bougies", "fabricant de bougies"],
        "default": ["candle shop", "candle maker"],
    },
    "Woodwork": {
        "NL": ["houtbewerking bedrijf", "timmerwerkplaats"],
        "BE": ["houtbewerking bedrijf", "menuiserie"],
        "DE": ["schreinerei", "tischlerei"],
        "FR": ["menuiserie", "ébéniste"],
        "default": ["woodworking shop", "carpentry workshop"],
    },
    "Furniture": {
        "NL": ["meubelmaker", "meubelwinkel"],
        "BE": ["meubelmaker", "magasin de meubles"],
        "DE": ["möbelgeschäft", "möbeltischlerei"],
        "FR": ["magasin de meubles", "fabricant de meubles"],
        "default": ["furniture store", "furniture maker"],
    },
    "SteelWork": {
        "NL": ["metaalbewerking", "staalconstructie bedrijf"],
        "DE": ["metallbau", "schlosserei"],
        "FR": ["métallerie", "ferronnerie"],
        "default": ["metal fabrication", "steel fabrication"],
    },
    "Music": {
        "NL": ["muziekwinkel", "muziekinstrumenten winkel"],
        "DE": ["musikgeschäft", "musikinstrumente laden"],
        "FR": ["magasin de musique", "magasin d'instruments de musique"],
        "default": ["music store", "musical instrument store"],
    },
    "Fashion": {
        "NL": ["kledingboetiek", "modeatelier"],
        "DE": ["modeboutique", "modeatelier"],
        "FR": ["boutique de mode", "atelier de couture"],
        "default": ["fashion boutique", "clothing boutique"],
    },
    "Liquor & Bottles": {
        "NL": ["slijterij", "wijnhandel"],
        "DE": ["spirituosenladen", "weinhandlung"],
        "FR": ["caviste", "magasin de spiritueux"],
        "default": ["liquor store", "wine shop"],
    },
    "Service": {
        "default": ["repair service", "service company"],
    },
    "Art": {
        "NL": ["kunstgalerie", "kunstenaarsatelier"],
        "DE": ["kunstgalerie", "künstleratelier"],
        "FR": ["galerie d'art", "atelier d'artiste"],
        "default": ["art gallery", "artist studio"],
    },
}

# Major cities per supported country — the geographic grid each job sweeps.
COUNTRY_CITIES: dict[str, list[str]] = {
    "NL": [
        "Amsterdam", "Rotterdam", "Den Haag", "Utrecht", "Eindhoven", "Groningen",
        "Tilburg", "Almere", "Breda", "Nijmegen", "Apeldoorn", "Arnhem", "Haarlem",
        "Enschede", "Amersfoort", "Zaanstad", "Den Bosch", "Zwolle", "Leiden",
        "Maastricht", "Dordrecht", "Ede", "Alphen aan den Rijn", "Leeuwarden",
        "Alkmaar", "Emmen", "Delft", "Venlo", "Deventer", "Sittard", "Helmond",
        "Heerlen", "Hilversum", "Amstelveen", "Zoetermeer",
    ],
    "DE": [
        "Berlin", "Hamburg", "München", "Köln", "Frankfurt am Main", "Stuttgart",
        "Düsseldorf", "Leipzig", "Dortmund", "Essen", "Bremen", "Dresden",
        "Hannover", "Nürnberg", "Duisburg", "Bochum", "Wuppertal", "Bielefeld",
        "Bonn", "Münster", "Karlsruhe", "Mannheim", "Augsburg", "Wiesbaden",
        "Mönchengladbach", "Braunschweig", "Kiel", "Aachen", "Chemnitz",
        "Magdeburg", "Freiburg", "Krefeld", "Mainz", "Lübeck", "Erfurt",
        "Oberhausen", "Rostock", "Kassel", "Potsdam", "Saarbrücken",
    ],
    "FR": [
        "Paris", "Marseille", "Lyon", "Toulouse", "Nice", "Nantes", "Montpellier",
        "Strasbourg", "Bordeaux", "Lille", "Rennes", "Reims", "Toulon",
        "Saint-Étienne", "Le Havre", "Grenoble", "Dijon", "Angers", "Nîmes",
        "Clermont-Ferrand", "Le Mans", "Aix-en-Provence", "Brest", "Tours",
        "Amiens", "Limoges", "Annecy", "Perpignan", "Besançon", "Metz",
        "Orléans", "Rouen", "Mulhouse", "Caen", "Nancy",
    ],
    "BE": [
        "Brussel", "Antwerpen", "Gent", "Charleroi", "Liège", "Brugge", "Namur",
        "Leuven", "Mons", "Aalst", "Mechelen", "Kortrijk", "Hasselt", "Oostende",
        "Sint-Niklaas", "Tournai", "Genk", "Roeselare", "Verviers",
    ],
    "GB": [
        "London", "Birmingham", "Manchester", "Glasgow", "Leeds", "Liverpool",
        "Newcastle", "Sheffield", "Bristol", "Edinburgh", "Leicester",
        "Nottingham", "Cardiff", "Belfast", "Coventry", "Bradford",
        "Stoke-on-Trent", "Wolverhampton", "Plymouth", "Southampton", "Reading",
        "Derby", "Luton", "Portsmouth", "Brighton", "Norwich", "Oxford",
        "Cambridge", "York", "Aberdeen",
    ],
    "US": [
        "New York", "Los Angeles", "Chicago", "Houston", "Phoenix",
        "Philadelphia", "San Antonio", "San Diego", "Dallas", "Austin",
        "San Jose", "Jacksonville", "Columbus", "Charlotte", "Indianapolis",
        "San Francisco", "Seattle", "Denver", "Washington DC", "Boston",
        "Nashville", "Portland", "Las Vegas", "Detroit", "Memphis",
        "Louisville", "Milwaukee", "Baltimore", "Minneapolis", "Atlanta",
    ],
}

COUNTRY_LABELS: dict[str, str] = {
    "NL": "Netherlands",
    "DE": "Germany",
    "FR": "France",
    "BE": "Belgium",
    "GB": "United Kingdom",
    "US": "USA",
}

# One-click presets for the standing target lists.
JOB_PRESETS: list[dict[str, str]] = [
    {"name": "Bike — Germany", "sectors": "Bike", "country_code": "DE"},
    {"name": "Bike — France", "sectors": "Bike", "country_code": "FR"},
    {"name": "Woodwork + Furniture — Netherlands", "sectors": "Woodwork,Furniture", "country_code": "NL"},
    {"name": "Woodwork + Furniture — Germany", "sectors": "Woodwork,Furniture", "country_code": "DE"},
]


# Directory/aggregator/junk hosts that SearXNG surfaces for "sector city"
# queries but are never a business's own site (extends the search_client
# blocklist with DE/FR/NL local directories + big platforms).
_SEARXNG_BLOCKED_HOSTS = {
    "werkenntdenbesten.de", "kennstdueinen.de", "gelbeseiten.de", "11880.com",
    "golocal.de", "dasoertliche.de", "dastelefonbuch.de", "meinestadt.de",
    "branchenbuch.de", "stadtbranchenbuch.com", "cylex.de", "cylex.nl",
    "cylex-branchenbuch.de", "yelp.de", "yelp.fr", "herold.at",
    "pagesjaunes.fr", "118712.fr", "hotfrog.de", "hotfrog.fr", "hotfrog.nl",
    "infobel.com", "europages.com", "telefoonboek.nl", "detelefoongids.nl",
    "openingstijden.nl", "oozo.nl", "klantenvertellen.nl", "trustoo.nl",
    "marktplaats.nl", "2dehands.be", "kleinanzeigen.de", "leboncoin.fr",
    "microsoft.com", "apple.com", "google.com", "amazon.com", "amazon.de",
    "amazon.fr", "amazon.nl", "reddit.com", "quora.com", "booking.com",
    "groupon.com", "groupon.de", "tripadvisor.com", "tripadvisor.de",
    "tripadvisor.fr", "tripadvisor.nl", "wikipedia.org", "facebook.com",
    "instagram.com", "linkedin.com", "youtube.com", "pinterest.com",
}


def _domain_blocked(domain: str) -> bool:
    if not domain:
        return True
    try:
        from app.search_client import _DIRECTORY_HOSTS  # noqa: PLC0415 - shared blocklist
        merged = _SEARXNG_BLOCKED_HOSTS | _DIRECTORY_HOSTS
    except Exception:  # noqa: BLE001
        merged = _SEARXNG_BLOCKED_HOSTS
    return any(domain == h or domain.endswith("." + h) for h in merged)


def _title_to_company_name(title: str, domain: str) -> str:
    """Best-effort business name from a search-result title."""
    first = title
    for sep in ("|", "–", "—", " - ", "·", "::"):
        if sep in first:
            first = first.split(sep, 1)[0]
    first = first.strip(" .")
    if 2 <= len(first) <= 60:
        return first
    # Title is descriptive/too long -> fall back to the domain label.
    label = (domain.split(".", 1)[0] if domain else "").replace("-", " ").strip()
    return label.title() if label else first[:60]


def _searxng_business_search(term: str, city: str, limit: int) -> list[dict]:
    """SearXNG fallback: organic results for 'term city' -> prospect records.

    Lower precision than Places (no address, name comes from the page title)
    but works without any Google API. Directories/platforms are dropped; the
    per-site email extraction that follows validates each site is real.
    """
    from app import search_client  # noqa: PLC0415 - lazy, optional backend

    if not search_client.is_configured():
        return []
    results = search_client.search(f"{term} {city}", limit=max(limit * 2, 20))
    records: list[dict] = []
    seen: set[str] = set()
    for r in results:
        domain = r.domain
        if domain in seen or _domain_blocked(domain):
            continue
        seen.add(domain)
        records.append({
            "source_reference": f"searxng:{domain}",
            "company_name": _title_to_company_name(r.title, domain),
            "website": f"https://{domain}",
            "phone": "",
            "company_type": "",
            "address": "",
            "city": city,
            "state": "",
            "country_code": "",  # trust the job's country; SearXNG has no address data
            "google_maps_url": "",
        })
        if len(records) >= limit:
            break
    return records


# ──────────────────────────────────────────────────────────────────────────
# OpenStreetMap Overpass source (free, keyless, structured)
# ──────────────────────────────────────────────────────────────────────────
# Sector -> OSM tag filters. One Overpass query per tag per (country|city).
# Sectors without a sensible OSM tag (Service) are web-search only.

OSM_SECTOR_TAGS: dict[str, list[str]] = {
    "Bike": ['"shop"="bicycle"'],
    "Candles": ['"shop"="candles"', '"craft"="candlemaker"'],
    "Woodwork": ['"craft"="carpenter"', '"craft"="joiner"'],
    "Furniture": ['"shop"="furniture"', '"craft"="cabinet_maker"'],
    "SteelWork": ['"craft"="metal_construction"', '"craft"="blacksmith"'],
    "Music": ['"shop"="musical_instrument"'],
    "Fashion": ['"shop"="boutique"'],
    "Liquor & Bottles": ['"shop"="alcohol"', '"shop"="wine"'],
    "Service": [],
    "Art": ['"shop"="art"'],
}


def _osm_endpoints() -> list[str]:
    return [u.strip() for u in settings.crawler_osm_endpoints.split(",") if u.strip()]


def _overpass_fetch(query: str) -> dict | None:
    """POST one Overpass QL query; try each endpoint until one answers."""
    import httpx  # noqa: PLC0415 - lazy per house rules

    body = urlencode({"data": query})
    headers = {"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "SchildBot/1.0"}
    for endpoint in _osm_endpoints():
        try:
            resp = httpx.post(endpoint, content=body, headers=headers,
                              timeout=settings.crawler_osm_timeout + 10)
            if resp.status_code == 200:
                return resp.json()
            logger.info("crawler: overpass %s returned HTTP %s", endpoint, resp.status_code)
        except Exception as exc:  # noqa: BLE001 - try the next mirror
            logger.info("crawler: overpass %s failed (%s)", endpoint, exc)
    return None


def _osm_area_clause(country_code: str, city: str) -> str:
    """Overpass QL that resolves the search area (whole country or one city).

    City areas are resolved INSIDE the country so name collisions across
    borders (Paris FR vs Paris US) cannot leak results.
    """
    cc = country_code.strip().upper()
    if not city:
        return f'area["ISO3166-1"="{cc}"]["admin_level"="2"]->.a;'
    safe_city = city.replace('"', "")
    return (
        f'area["ISO3166-1"="{cc}"]["admin_level"="2"]->.c;'
        + f'\nrel["name"="{safe_city}"]["boundary"="administrative"](area.c);'
        + "\nmap_to_area->.a;"
    )


def _osm_search(tag_expr: str, country_code: str, city: str) -> list[dict]:
    """One Overpass query -> prospect records (name required per element)."""
    area = _osm_area_clause(country_code, city)
    query = (
        f"[out:json][timeout:{settings.crawler_osm_timeout}];\n"
        f"{area}\n"
        f"nwr[{tag_expr}](area.a);\n"
        f"out center tags {max(10, settings.crawler_osm_limit)};"
    )
    payload = _overpass_fetch(query)
    if not payload:
        return []

    records: list[dict] = []
    for el in payload.get("elements", []):
        tags = el.get("tags") or {}
        name = (tags.get("name") or "").strip()
        if not name:
            continue
        website = (tags.get("website") or tags.get("contact:website") or "").strip()
        addr = " ".join(p for p in [tags.get("addr:street", ""), tags.get("addr:housenumber", "")] if p)
        records.append({
            "source_reference": f"osm:{el.get('type', 'n')}/{el.get('id', '')}",
            "company_name": name[:120],
            "website": website,
            # Tagged public email — used directly, skips site-crawling.
            "email": (tags.get("email") or tags.get("contact:email") or "").strip(),
            "phone": (tags.get("phone") or tags.get("contact:phone") or "").strip(),
            "company_type": tag_expr.replace('"', ""),
            "address": addr,
            "city": (tags.get("addr:city") or city or "").strip(),
            "state": "",
            "country_code": (tags.get("addr:country") or "").strip(),
            "google_maps_url": "",
        })
    return records


def _searxng_available() -> bool:
    try:
        from app import search_client  # noqa: PLC0415
        return search_client.is_configured()
    except Exception:  # noqa: BLE001
        return False


def search_sources_available() -> bool:
    """True when at least one business-search backend can serve queries."""
    return settings.crawler_osm_enabled or _searxng_available()


def available_countries() -> list[tuple[str, str]]:
    """(iso2, label) pairs the crawler can sweep, for the create-job form."""
    return [(cc, COUNTRY_LABELS.get(cc, cc)) for cc in COUNTRY_CITIES]


def available_sectors() -> list[str]:
    """Sectors with a search vocabulary, in canonical order."""
    return list(SECTOR_SEARCH_TERMS.keys())


def parse_sectors(raw: str) -> list[str]:
    """CSV -> known canonical sector names (silently drops unknowns)."""
    return [s.strip() for s in (raw or "").split(",") if s.strip() in SECTOR_SEARCH_TERMS]


def parse_cities(raw: str) -> list[str]:
    """CSV -> cleaned city names, deduped, original order kept."""
    seen: set[str] = set()
    cities: list[str] = []
    for c in (raw or "").split(","):
        name = c.strip()
        key = name.lower()
        if name and key not in seen:
            seen.add(key)
            cities.append(name)
    return cities


def build_query_plan(sectors_csv: str, country_code: str, cities_csv: str = "") -> list[tuple[str, str, str, str]]:
    """Deterministic (source, sector, query, city) plan for a job.

    Geography is country-first: no cities -> the whole country (country-wide
    OSM queries + the full built-in city grid for web search). With cities ->
    both stages run on exactly those cities (any city name works, not just
    the grid).

    OSM items come first — one bulk structured query covers far more ground
    than a web search and often carries tagged emails. Deterministic ordering
    is what makes jobs resumable from ``queries_done`` alone.
    """
    cc = (country_code or "").strip().upper()
    sectors = parse_sectors(sectors_csv)
    picked = parse_cities(cities_csv)
    search_cities = picked or COUNTRY_CITIES.get(cc, [])
    plan: list[tuple[str, str, str, str]] = []

    # Stage 1 — OSM: country-wide (one query per tag) or per picked city.
    if settings.crawler_osm_enabled:
        for sector in sectors:
            for tag in OSM_SECTOR_TAGS.get(sector, []):
                for city in (picked or [""]):
                    plan.append(("osm", sector, tag, city))

    # Stage 2 — SearXNG web search, city-major so coverage stays coherent.
    for city in search_cities:
        for sector in sectors:
            vocab = SECTOR_SEARCH_TERMS[sector]
            for term in vocab.get(cc, vocab.get("default", [])):
                plan.append(("searxng", sector, term, city))
    return plan


# ──────────────────────────────────────────────────────────────────────────
# Job worker
# ──────────────────────────────────────────────────────────────────────────

def _ingest_record(db, job: CrawlJob, rec: dict, sector: str, backend: str) -> None:
    """Dedupe one search hit, extract its email, store a crawler prospect.

    Transaction hygiene matters here: email extraction can take 30s+ per site,
    so ALL slow network work happens before any row is written. Each record is
    a short read -> slow extract -> quick insert+commit, which keeps the DB
    write lock held for milliseconds (SQLite dev has one writer; Postgres
    appreciates short transactions too).
    """
    job.found_count += 1
    name = (rec.get("company_name") or "").strip()
    ref = (rec.get("source_reference") or "").strip()
    if not name:
        db.commit()
        return

    # Keep only businesses actually in the job's country (Places sometimes
    # bleeds across borders near city edges).
    cc = to_iso2(rec.get("country_code") or "") or ""
    if cc and cc != job.country_code.upper():
        job.dup_count += 1
        db.commit()
        return

    # Dedupe: same place id (any earlier import/job), same website domain in
    # prospects, or same domain already in the KVK outreach pool.
    domain = normalize_domain(rec.get("website") or "")
    exists = None
    if ref:
        exists = db.scalar(select(Prospect.id).where(Prospect.source_reference == ref).limit(1))
    if exists is None and domain:
        exists = db.scalar(select(Prospect.id).where(Prospect.website_domain == domain).limit(1))
        if exists is None:
            exists = db.scalar(select(KvkCompany.id).where(KvkCompany.website_domain == domain).limit(1))
    if exists is not None:
        job.dup_count += 1
        db.commit()
        return

    # Slow part FIRST, outside any write transaction. A pre-supplied email
    # (OSM contact tag) skips site-crawling entirely.
    website = (rec.get("website") or "").strip()
    tagged_email = _clean_email(rec.get("email") or "")
    contact = None
    contact_error = ""
    if not tagged_email and job.extract_emails and website:
        try:
            contact = discover_contacts(website, name)
        except Exception as exc:  # noqa: BLE001 - one bad site never kills the job
            contact_error = str(exc)[:300]
        time.sleep(max(0.0, settings.crawler_extract_spacing))

    prospect = Prospect(
        source="crawler",
        source_reference=ref,
        company_name=name,
        website=website,
        website_domain=domain,
        phone=rec.get("phone") or "",
        city=rec.get("city") or "",
        state=rec.get("state") or "",
        country_code=cc or job.country_code.upper(),
        address=rec.get("address") or "",
        google_maps_url=rec.get("google_maps_url") or "",
        company_type=rec.get("company_type") or "",
        crawl_job_id=job.id,
        main_sector=sector,
        website_search_query=job.current_activity,
        discovery_backend=backend,
        email_discovery_status="no_website" if not website else "not_started",
    )
    if tagged_email:
        prospect.email = tagged_email
        prospect.email_domain = tagged_email.rsplit("@", 1)[-1]
        prospect.email_confidence = 95
        prospect.email_source_page = "osm-contact-tag"
        prospect.email_discovery_status = "found"
        prospect.email_discovered_at = _utcnow()
    elif contact is not None:
        cleaned = _clean_email(contact.email_public)
        if cleaned:
            prospect.email = cleaned
            prospect.email_domain = cleaned.rsplit("@", 1)[-1]
            prospect.email_confidence = contact.email_confidence
            prospect.email_source_page = contact.email_source_page
            prospect.email_discovery_status = "found"
            prospect.email_discovered_at = _utcnow()
            prospect.emails_found = ", ".join(contact.emails_found[:10])
            prospect.pages_scanned = ", ".join(contact.pages_scanned[:10])
        else:
            prospect.email_discovery_status = "no_email"
            prospect.pages_scanned = ", ".join(contact.pages_scanned[:10])
    elif contact_error:
        prospect.email_discovery_status = "error"
        prospect.discovery_error = contact_error

    # Existing-customer flag (exact email only — mirrors the strict-matching
    # rule; campaigns re-check suppression at send time anyway).
    cust_id = None
    if prospect.email:
        cust_id = db.scalar(
            select(Customer.id)
            .where(func.lower(Customer.customer_email_primary) == prospect.email.lower())
            .limit(1)
        )

    db.add(prospect)
    job.new_count += 1
    if prospect.email:
        job.email_count += 1
    if cust_id is not None:
        prospect.match_status = MatchStatus.existing_customer
        prospect.existing_customer_id = cust_id
        prospect.match_method = "exact_email"
        job.client_count += 1
    db.commit()


def _run_job(job_id: int) -> None:
    """Worker body for one job. Commits per query so counters are live."""
    db = SessionLocal()
    try:
        job = db.get(CrawlJob, job_id)
        if job is None or job.status != "running":
            return
        if not search_sources_available():
            job.status = "error"
            job.error = "No search backend available — set GOOGLE_PLACES_API_KEY or SEARXNG_URL"
            db.commit()
            return

        plan = build_query_plan(job.sectors, job.country_code, job.cities)
        if not plan:
            job.status = "error"
            job.error = f"No query plan: unknown sectors '{job.sectors}' or country '{job.country_code}'"
            db.commit()
            return

        job.queries_total = len(plan)
        if job.started_at is None:
            job.started_at = _utcnow()
        db.commit()

        label = COUNTRY_LABELS.get(job.country_code.upper(), job.country_code)
        while job.queries_done < len(plan):
            # Re-read status each iteration so pause/delete takes effect fast.
            db.expire_all()
            job = db.get(CrawlJob, job_id)
            if job is None:
                return
            if job.status != "running":
                logger.info("crawler: job %s stopped (status=%s)", job_id, job.status)
                return
            if job.new_count >= job.max_results:
                job.status = "done"
                job.finished_at = _utcnow()
                job.current_activity = f"Reached cap of {job.max_results} new businesses"
                db.commit()
                return

            source, sector, term, city = plan[job.queries_done]
            place_label = city or label
            job.current_activity = (
                f"OSM {term} in {place_label}" if source == "osm" else f"{term} in {place_label}"
            ) + f" ({job.queries_done + 1}/{len(plan)})"
            db.commit()

            try:
                if source == "osm":
                    records = _osm_search(term, job.country_code, city)
                else:
                    records = _searxng_business_search(
                        term, city, limit=max(1, min(20, settings.crawler_places_page_size))
                    )
                backend = source
            except Exception as exc:  # noqa: BLE001 - a failed query is skipped, not fatal
                logger.warning("crawler: job %s query failed (%s in %s): %s", job_id, term, place_label, exc)
                records, backend = [], "none"

            for rec in records:
                if job.new_count >= job.max_results:
                    break
                try:
                    _ingest_record(db, job, rec, sector, backend)
                except Exception as exc:  # noqa: BLE001 - one bad record never kills the batch
                    db.rollback()
                    job = db.get(CrawlJob, job_id)
                    logger.warning("crawler: job %s ingest failed: %s", job_id, exc)

            job.queries_done += 1
            job.updated_at = _utcnow()
            db.commit()
            time.sleep(max(0.0, settings.crawler_query_spacing))

        job.status = "done"
        job.finished_at = _utcnow()
        job.current_activity = "Finished full query plan"
        db.commit()
        logger.info("crawler: job %s done (%s new, %s emails)", job_id, job.new_count, job.email_count)
    except Exception as exc:  # noqa: BLE001 - record failure on the job row
        logger.exception("crawler: job %s crashed", job_id)
        try:
            db.rollback()
            job = db.get(CrawlJob, job_id)
            if job is not None:
                job.status = "error"
                job.error = str(exc)[:500]
                db.commit()
        except Exception:  # noqa: BLE001
            pass
    finally:
        db.close()
        with _active_lock:
            _active_jobs.pop(job_id, None)


# ──────────────────────────────────────────────────────────────────────────
# Scheduler (same daemon pattern as kvk_enrichment)
# ──────────────────────────────────────────────────────────────────────────

_active_jobs: dict[int, threading.Thread] = {}
_active_lock = threading.Lock()
_scheduler_started = False
_scheduler_lock = threading.Lock()


def _tick() -> None:
    """Claim free slots for runnable jobs (status='running', not yet claimed)."""
    with _active_lock:
        for jid in [j for j, t in _active_jobs.items() if not t.is_alive()]:
            _active_jobs.pop(jid, None)
        slots = max(0, settings.crawler_max_concurrent_jobs - len(_active_jobs))
    if slots <= 0:
        return

    with SessionLocal() as db:
        runnable = db.scalars(
            select(CrawlJob.id).where(CrawlJob.status == "running").order_by(CrawlJob.id)
        ).all()

    for jid in runnable:
        if slots <= 0:
            break
        with _active_lock:
            if jid in _active_jobs:
                continue
            thread = threading.Thread(target=_run_job, args=(jid,), daemon=True, name=f"crawl-job-{jid}")
            _active_jobs[jid] = thread
        thread.start()
        slots -= 1


def _crawler_loop() -> None:
    logger.info(
        "crawler: scheduler started (max %s concurrent jobs, tick %ss)",
        settings.crawler_max_concurrent_jobs, settings.crawler_interval,
    )
    while True:
        try:
            _tick()
        except Exception:  # noqa: BLE001 - the daemon must never die
            logger.exception("crawler: tick failed")
        time.sleep(max(3, settings.crawler_interval))


def start_crawler_scheduler() -> None:
    """Idempotent daemon start, called from app lifespan."""
    if not settings.crawler_enabled:
        logger.info("crawler: disabled via CRAWLER_ENABLED")
        return
    global _scheduler_started
    with _scheduler_lock:
        if _scheduler_started:
            return
        _scheduler_started = True
    threading.Thread(target=_crawler_loop, daemon=True, name="directory-crawler").start()


def get_crawler_status(db) -> dict:
    """Everything the /crawler page polls: jobs + totals + scheduler health."""
    jobs = db.scalars(select(CrawlJob).order_by(CrawlJob.id.desc())).all()
    with _active_lock:
        active_ids = [jid for jid, t in _active_jobs.items() if t.is_alive()]
    return {
        "scheduler_active": _scheduler_started,
        "max_concurrent": settings.crawler_max_concurrent_jobs,
        "active_job_ids": active_ids,
        "osm_enabled": settings.crawler_osm_enabled,
        "searxng_configured": _searxng_available(),
        "source_available": search_sources_available(),
        "jobs": [
            {
                "id": j.id,
                "name": j.name,
                "sectors": j.sectors,
                "country_code": j.country_code,
                "country": COUNTRY_LABELS.get(j.country_code.upper(), j.country_code),
                "cities": j.cities,
                "status": j.status,
                "live": j.id in active_ids,
                "max_results": j.max_results,
                "queries_total": j.queries_total,
                "queries_done": j.queries_done,
                "pct": round(j.queries_done / j.queries_total * 100) if j.queries_total else 0,
                "found_count": j.found_count,
                "new_count": j.new_count,
                "dup_count": j.dup_count,
                "email_count": j.email_count,
                "client_count": j.client_count,
                "current_activity": j.current_activity,
                "error": j.error,
            }
            for j in jobs
        ],
        "totals": {
            "new": sum(j.new_count for j in jobs),
            "emails": sum(j.email_count for j in jobs),
            "running": sum(1 for j in jobs if j.status == "running"),
        },
    }
