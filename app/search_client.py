"""SearXNG-backed business-name -> website search client.

Purpose
-------
Given a business name (optionally + city / country), ask a self-hosted
`SearXNG <https://docs.searxng.org/>`_ instance for the company's own
website. SearXNG is a privacy-respecting metasearch engine: it runs the
actual Google/Bing/DuckDuckGo/Brave queries *from its own server*, so we
get real, human-like result snippets without exposing our cloud IP to
Google's JS-only stub pages. This is the "open" (non-Google-API) backend
for the discovery pipeline (see ``app/discovery_open.py``).

This module ONLY talks to SearXNG's JSON API
(``GET {SEARXNG_URL}/search?format=json&q=...``). It ranks the returned
results by how likely each one is the company's OWN domain (token overlap
between the company name and the result domain/title, ccTLD bonus,
directory/marketplace demotion) and hands back scored candidates.

Public API
----------
- ``is_configured() -> bool``
      True only when ``settings.searxng_url`` is set. Everything else
      keys off this so the module is a complete no-op when unconfigured.
- ``search(query: str, limit: int = 10) -> list[SearchResult]``
      Low-level: run one raw SearXNG query, return ranked ``SearchResult``
      objects (``title, url, domain, score`` + ``snippet, engine``). This is
      the signature the spec mandates and is also one of the shapes the
      orchestrator probes for.
- ``find_website(company_name, city="", country_code="", *, limit=5)``
      High-level: build a good query from name+city, run ``search``, then
      apply the company-aware ranking (name-token overlap, ccTLD boost,
      directory demotion). Returns ranked ``SearchResult`` list.
- ``best_website(company_name, city="", country_code="")``
      The single top candidate IFF its score clears
      ``settings.discovery_review_threshold``; otherwise ``None`` (so the
      caller routes the row to manual review instead of trusting it).
- ``clear_cache()`` — drop the in-process cache (used by tests / ops).

Graceful-fallback behaviour (NOTHING here ever raises)
------------------------------------------------------
- ``SEARXNG_URL`` unset                -> ``is_configured()`` False, all
                                          query functions return ``[]`` /
                                          ``None``.
- ``httpx`` not installed              -> falls back to ``urllib`` from the
                                          stdlib (lazy-imported inside the
                                          fetch helper, never at module top).
- network error / non-200 / bad JSON   -> logged at INFO, returns ``[]``.
- empty / whitespace query             -> returns ``[]`` immediately.

Caching
-------
Two layers, both optional:

1. **In-process dict cache** (always on). Keyed by a normalized
   ``(query, limit)`` tuple. SearXNG is the slow, rate-limited hop in the
   pipeline and discovery re-queries the same company names across batches,
   so this cuts a lot of duplicate round-trips within a single process.
   TTL-bounded (``_CACHE_TTL_SECONDS``) and size-capped (``_CACHE_MAX``) so
   a long-running daemon doesn't grow unbounded. Thread-safe.

2. **Optional DB cache hook** (interface only — no table created here).
   ``_DB_CACHE_BACKEND`` can be set by the integrator to an object
   implementing the tiny ``SearchCacheBackend`` protocol below. When set,
   ``search`` reads from it before hitting the network and writes back
   after a successful fetch. This lets a future ``search_cache`` table
   share results across processes/dynos. Until then it stays ``None`` and
   only the in-process cache is used.

   TODO(integrator): create a ``search_cache`` table + Alembic migration
   (suggested columns: ``query_key TEXT PRIMARY KEY``, ``results_json TEXT``,
   ``fetched_at TIMESTAMPTZ``) and register a backend via
   ``set_db_cache_backend(MySqlAlchemyBackend(SessionLocal))``. The backend
   only has to implement ``get(query_key) -> list[dict] | None`` and
   ``set(query_key, results: list[dict]) -> None`` and MUST swallow its own
   errors so a cache hiccup can never break discovery.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Optional, Protocol
from urllib.parse import quote_plus

from app.config import settings
from app.utils import normalize_domain, normalize_text

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Value object
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SearchResult:
    """One ranked candidate website surfaced by SearXNG.

    Field names are chosen to line up with
    ``discovery_open.WebsiteChoice`` / ``search_client.WebsiteCandidate``
    so the orchestrator's tolerant adapter (`_coerce_candidate`) maps us
    with zero glue:

    - ``title``   page title from the search result
    - ``url``     full result URL
    - ``domain``  registrable host (``www.`` stripped) via ``normalize_domain``
    - ``score``   0-100 confidence this is the company's OWN site
    - ``snippet`` meta-description excerpt (handy for the review UI)
    - ``engine``  which underlying engine inside SearXNG surfaced it
    """
    title: str = ""
    url: str = ""
    domain: str = ""
    score: int = 0
    snippet: str = ""
    engine: str = ""


# Backwards/forwards-compat alias: the design doc + the orchestrator both
# reference a ``WebsiteCandidate`` type. Expose the same class under that
# name so either importer works without us shipping two dataclasses.
WebsiteCandidate = SearchResult


# ---------------------------------------------------------------------------
# Optional DB cache hook (interface only — no table is created here)
# ---------------------------------------------------------------------------

class SearchCacheBackend(Protocol):
    """Tiny protocol for an optional cross-process search cache.

    The integrator may register an implementation via
    ``set_db_cache_backend``. Implementations MUST be exception-safe (return
    ``None`` / no-op on any error) so a cache failure never breaks discovery.
    """

    def get(self, query_key: str) -> Optional[list[dict[str, Any]]]:
        ...

    def set(self, query_key: str, results: list[dict[str, Any]]) -> None:
        ...


# Module-level slot for the optional backend. Stays None until an integrator
# wires up a real ``search_cache`` table (see module docstring TODO).
_DB_CACHE_BACKEND: Optional[SearchCacheBackend] = None


def set_db_cache_backend(backend: Optional[SearchCacheBackend]) -> None:
    """Register (or clear) the optional cross-process DB cache backend."""
    global _DB_CACHE_BACKEND
    _DB_CACHE_BACKEND = backend


# ---------------------------------------------------------------------------
# In-process cache (always on)
# ---------------------------------------------------------------------------

# query_key -> (expires_at_epoch, list[SearchResult])
_CACHE: dict[str, tuple[float, list[SearchResult]]] = {}
_CACHE_LOCK = threading.Lock()
# Default 1h TTL: company websites are stable, but we don't want a daemon to
# pin a stale empty result forever after SearXNG was briefly down.
_CACHE_TTL_SECONDS = 3600.0
# Hard cap so a long-lived process can't grow the dict without bound. When we
# exceed it we drop the whole cache (simplest correct eviction; the cache is a
# latency optimization, not a source of truth).
_CACHE_MAX = 5000


def _cache_key(query: str, limit: int) -> str:
    """Normalized, stable key for the caches (lower/whitespace-collapsed)."""
    return f"{normalize_text(query)}::{int(limit)}"


def _cache_get(key: str) -> Optional[list[SearchResult]]:
    now = time.time()
    with _CACHE_LOCK:
        entry = _CACHE.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if expires_at < now:
            # Expired — drop it and miss.
            _CACHE.pop(key, None)
            return None
        return value


def _cache_set(key: str, value: list[SearchResult]) -> None:
    with _CACHE_LOCK:
        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.clear()
        _CACHE[key] = (time.time() + _CACHE_TTL_SECONDS, value)


def clear_cache() -> None:
    """Drop the in-process cache. Useful for tests / manual ops."""
    with _CACHE_LOCK:
        _CACHE.clear()


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------

def is_configured() -> bool:
    """True iff a SearXNG base URL is configured.

    Read via ``getattr`` with a default so this module imports cleanly even
    before the integrator adds ``searxng_url`` to ``app/config.Settings``.
    """
    return bool(str(getattr(settings, "searxng_url", "") or "").strip())


def _base_url() -> str:
    """SearXNG base URL with any trailing slash removed."""
    return str(getattr(settings, "searxng_url", "") or "").strip().rstrip("/")


def _timeout_seconds() -> float:
    """Request timeout; supports either SEARXNG_TIMEOUT or SEARXNG_TIMEOUT_S."""
    # Accept both names so we match whatever the integrator settles on.
    raw = getattr(settings, "searxng_timeout_s", None)
    if raw is None:
        raw = getattr(settings, "searxng_timeout", None)
    try:
        value = float(raw) if raw is not None else 8.0
    except (TypeError, ValueError):
        value = 8.0
    return value if value > 0 else 8.0


def _engines() -> str:
    """Comma-separated engine list to run *inside* SearXNG (server-side)."""
    return str(
        getattr(settings, "searxng_engines", "")
        or "google,bing,duckduckgo,brave"
    ).strip()


def _review_threshold() -> int:
    """Score a candidate must clear for ``best_website`` to auto-pick it."""
    try:
        return int(getattr(settings, "discovery_review_threshold", 60))
    except (TypeError, ValueError):
        return 60


# Hosts that are directories / marketplaces / social, NOT a company's own
# site. Heavily demoted in ranking so we never mistake a Facebook page or a
# marketplace listing for the real website.
_DIRECTORY_HOSTS = {
    "facebook.com", "m.facebook.com", "instagram.com", "linkedin.com",
    "twitter.com", "x.com", "youtube.com", "pinterest.com", "tiktok.com",
    "marktplaats.nl", "2dehands.be", "yelp.com", "yelp.nl",
    "kvk.nl", "companyinfo.nl", "drimble.nl", "openingstijden.nl",
    "tripadvisor.com", "tripadvisor.nl", "google.com", "goo.gl",
    "maps.google.com", "amazon.com", "amazon.nl", "amazon.de", "ebay.com",
    "ebay.nl", "etsy.com", "bol.com", "wikipedia.org", "trustpilot.com",
    "indeed.com", "glassdoor.com", "crunchbase.com",
}

# ISO-2 country code -> ccTLD suffix(es) we boost. Region-relevant subset of
# the markets Schild targets (NL/DE/FR/BE/UK/US/...). A site whose domain ends
# in the prospect's ccTLD is much more likely to be the real local business.
_CCTLD_BY_CC = {
    "NL": (".nl",),
    "DE": (".de",),
    "FR": (".fr",),
    "BE": (".be",),
    "UK": (".uk", ".co.uk"),
    "GB": (".uk", ".co.uk"),
    "US": (".us", ".com"),
    "AT": (".at",),
    "CH": (".ch",),
    "ES": (".es",),
    "IT": (".it",),
    "DK": (".dk",),
    "SE": (".se",),
    "NO": (".no",),
    "IE": (".ie",),
}


# ---------------------------------------------------------------------------
# HTTP fetch (lazy httpx, urllib fallback) — never raises
# ---------------------------------------------------------------------------

def _fetch_json(url: str, timeout: float) -> Optional[dict[str, Any]]:
    """GET ``url`` and parse JSON. Returns the dict, or ``None`` on any error.

    Tries ``httpx`` first (lazy-imported inside the function per house rules);
    if httpx isn't installed, falls back to stdlib ``urllib``. Either path
    swallows all errors and returns ``None`` so callers never see an
    exception.
    """
    # --- Preferred path: httpx (lazy import) -------------------------------
    try:
        import httpx  # noqa: PLC0415 - intentional lazy import
    except Exception:  # noqa: BLE001 - dep missing -> use urllib fallback
        httpx = None  # type: ignore[assignment]

    headers = {"Accept": "application/json", "User-Agent": "SchildBot/1.0"}

    if httpx is not None:
        try:
            resp = httpx.get(url, headers=headers, timeout=timeout, follow_redirects=True)
            if resp.status_code != 200:
                logger.info("search_client: SearXNG returned HTTP %s", resp.status_code)
                return None
            return resp.json()
        except Exception as exc:  # noqa: BLE001 - any httpx failure -> graceful miss
            logger.info("search_client: httpx fetch failed (%s)", exc)
            return None

    # --- Fallback path: stdlib urllib -------------------------------------
    try:
        from urllib.request import Request, urlopen  # noqa: PLC0415

        req = Request(url, headers=headers)
        with urlopen(req, timeout=timeout) as r:  # noqa: S310 - URL is our configured SearXNG
            if getattr(r, "status", 200) != 200:
                return None
            raw = r.read()
        return json.loads(raw.decode("utf-8", errors="replace"))
    except Exception as exc:  # noqa: BLE001 - graceful miss
        logger.info("search_client: urllib fetch failed (%s)", exc)
        return None


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------

def _name_tokens(name: str) -> list[str]:
    """Significant tokens from a company name (drops legal-suffix noise).

    Used both for query building and for scoring domain/title overlap.
    """
    stop = {
        "the", "and", "bv", "b", "v", "nv", "vof", "gmbh", "sarl", "sas",
        "ltd", "llc", "inc", "co", "company", "shop", "store", "winkel",
        "de", "het", "een", "van", "der", "den",
    }
    return [t for t in normalize_text(name).split() if t and t not in stop]


def _name_overlap_score(name: str, domain: str, title: str) -> int:
    """0-100 overlap between the company name and the result domain/title.

    Uses rapidfuzz when available (already a project dependency) for a fuzzy
    token-set ratio, and ALWAYS layers on a deterministic exact-token bonus
    so the function still works (and tests still pass) even if rapidfuzz is
    somehow unavailable. Lazy-imported per house rules.
    """
    tokens = _name_tokens(name)
    if not tokens:
        return 0

    # Bare domain label (e.g. "bikecity" from "bikecity.nl") for token checks.
    domain_label = (domain.split(".", 1)[0] if domain else "").replace("-", "")
    domain_joined = (domain or "").replace(".", "").replace("-", "")
    title_norm = normalize_text(title)

    # Deterministic component: fraction of name tokens that appear in the
    # domain label or the title. This alone is enough for the common case
    # where the brand name is literally the domain.
    hits = 0
    for tok in tokens:
        if tok and (tok in domain_joined or tok in title_norm):
            hits += 1
    exact_fraction = hits / len(tokens)
    exact_score = int(exact_fraction * 100)

    # Fuzzy component (best-effort): compare the joined name against the
    # domain label, catching minor spelling drift / concatenation.
    fuzzy_score = 0
    try:
        from rapidfuzz import fuzz  # noqa: PLC0415 - lazy, optional

        name_joined = "".join(tokens)
        if name_joined and domain_label:
            fuzzy_score = int(fuzz.token_set_ratio(name_joined, domain_label))
    except Exception:  # noqa: BLE001 - rapidfuzz optional; exact score still applies
        fuzzy_score = 0

    return max(exact_score, fuzzy_score)


def _score_candidate(
    *, name: str, url: str, domain: str, title: str, country_code: str
) -> int:
    """Final 0-100 confidence that ``url`` is the company's OWN website."""
    base = _name_overlap_score(name, domain, title)

    # ccTLD bonus: a domain ending in the prospect's country TLD is more
    # likely the real local business site.
    cc = str(country_code or "").strip().upper()
    suffixes = _CCTLD_BY_CC.get(cc, ())
    if suffixes and any(domain.endswith(suf) for suf in suffixes):
        base += 10

    # Directory / marketplace / social demotion: these are never the
    # company's own site, so floor them hard regardless of name overlap
    # (a Facebook *page* title contains the brand name too).
    if domain in _DIRECTORY_HOSTS or any(
        domain == h or domain.endswith("." + h) for h in _DIRECTORY_HOSTS
    ):
        base = min(base, 15)

    return max(0, min(100, base))


def _parse_results(payload: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    """Pull the raw result rows out of a SearXNG JSON payload (defensive)."""
    if not isinstance(payload, dict):
        return []
    rows = payload.get("results")
    if not isinstance(rows, list):
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict):
            out.append(row)
        if len(out) >= max(1, limit):
            break
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def search(query: str, limit: int = 10) -> list[SearchResult]:
    """Run one raw SearXNG query and return up-to-``limit`` ranked results.

    This is the low-level entry point mandated by the spec. It does NOT do
    company-name-aware boosting beyond a generic relevance score derived from
    the query text vs. the result domain/title — use ``find_website`` when you
    have structured name/city/country and want the full ranking.

    Returns ``[]`` when unconfigured, on empty query, or on any error.
    Results are cached in-process (and via the optional DB backend) keyed on
    the normalized ``(query, limit)``.
    """
    q = (query or "").strip()
    if not is_configured() or not q:
        return []

    limit = max(1, int(limit or 10))
    key = _cache_key(q, limit)

    # 1) In-process cache.
    cached = _cache_get(key)
    if cached is not None:
        return cached

    # 2) Optional DB cache backend (best-effort, never fatal).
    if _DB_CACHE_BACKEND is not None:
        try:
            rows = _DB_CACHE_BACKEND.get(key)
            if rows is not None:
                results = [SearchResult(**{k: r.get(k) for k in SearchResult.__annotations__ if r.get(k) is not None}) for r in rows]
                _cache_set(key, results)
                return results
        except Exception as exc:  # noqa: BLE001 - cache must never break us
            logger.info("search_client: DB cache get failed (%s)", exc)

    # 3) Network fetch from SearXNG.
    params = {
        "q": q,
        "format": "json",
        "engines": _engines(),
        "safesearch": "0",
    }
    url = f"{_base_url()}/search?" + "&".join(
        f"{k}={quote_plus(str(v))}" for k, v in params.items()
    )
    payload = _fetch_json(url, _timeout_seconds())
    if payload is None:
        # Network/parse failure: cache the empty result briefly is risky (we
        # might pin a transient outage), so do NOT cache misses from the
        # network — only successful payloads get cached.
        return []

    raw_rows = _parse_results(payload, limit)
    results: list[SearchResult] = []
    for row in raw_rows:
        u = str(row.get("url") or row.get("link") or "").strip()
        if not u:
            continue
        domain = normalize_domain(u)
        title = str(row.get("title") or "").strip()
        snippet = str(row.get("content") or row.get("snippet") or "").strip()
        engine = str(row.get("engine") or "").strip()
        # Generic relevance score from the *query* text (no structured name
        # here) — name-aware boosting happens in find_website().
        score = _score_candidate(
            name=q, url=u, domain=domain, title=title, country_code=""
        )
        results.append(
            SearchResult(
                title=title, url=u, domain=domain, score=score,
                snippet=snippet, engine=engine,
            )
        )

    # Stable sort: highest score first, preserving SearXNG's own order on ties.
    results.sort(key=lambda r: r.score, reverse=True)

    # Cache the successful result both in-process and (best-effort) in the DB.
    _cache_set(key, results)
    if _DB_CACHE_BACKEND is not None:
        try:
            _DB_CACHE_BACKEND.set(key, [asdict(r) for r in results])
        except Exception as exc:  # noqa: BLE001 - cache write never fatal
            logger.info("search_client: DB cache set failed (%s)", exc)

    return results


def _build_query(company_name: str, city: str, country_code: str) -> str:
    """Compose a clean SearXNG query from structured inputs."""
    parts = [str(company_name or "").strip()]
    if city:
        parts.append(str(city).strip())
    return " ".join(p for p in parts if p).strip()


def find_website(
    company_name: str,
    city: str = "",
    country_code: str = "",
    *,
    limit: int = 5,
) -> list[SearchResult]:
    """Find the company's own website, ranked with name/ccTLD awareness.

    Builds a ``name + city`` query, runs ``search`` (which handles caching +
    graceful fallback), then RE-SCORES every candidate using the structured
    company name and country so brand-token overlap and ccTLD matches float to
    the top and directory/social hosts sink. Returns up to ``limit`` results,
    highest score first. ``[]`` when unconfigured / no name / on error.
    """
    name = str(company_name or "").strip()
    if not is_configured() or not name:
        return []

    query = _build_query(name, city, country_code)
    # Pull a few extra raw results so re-ranking has room to reorder.
    raw = search(query, limit=max(limit * 2, 10))
    if not raw:
        return []

    rescored: list[SearchResult] = []
    for r in raw:
        score = _score_candidate(
            name=name, url=r.url, domain=r.domain, title=r.title,
            country_code=country_code,
        )
        # Replace only the score; SearchResult is frozen so build a new one.
        rescored.append(
            SearchResult(
                title=r.title, url=r.url, domain=r.domain, score=score,
                snippet=r.snippet, engine=r.engine,
            )
        )

    rescored.sort(key=lambda r: r.score, reverse=True)
    return rescored[: max(1, limit)]


def best_website(
    company_name: str, city: str = "", country_code: str = ""
) -> Optional[SearchResult]:
    """Top candidate IFF its score clears the review threshold, else ``None``.

    A ``None`` return is the explicit signal to the orchestrator that the row
    should go to the manual ``/review/discovery`` queue rather than being
    trusted automatically — this protects the strict-matching invariant.
    """
    candidates = find_website(company_name, city, country_code, limit=5)
    if not candidates:
        return None
    top = candidates[0]
    return top if top.score >= _review_threshold() else None
