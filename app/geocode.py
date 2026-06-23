"""Open geocoding client (Photon / Pelias — NOT public Nominatim for bulk).

Purpose
-------
Forward-geocode a free-text address (or a "name, street, city" blob) into one
or more structured ``Place`` value objects (lat/lon + city/country/postcode).
This powers the open-stack discovery pipeline: when an imported KVK row or
Prospect is missing its postal code / province / coordinates, we resolve them
from a self-hosted (or hosted) geocoder instead of Google.

Why Photon / Pelias and NOT public Nominatim
---------------------------------------------
The public OpenStreetMap Nominatim instance forbids bulk / automated use and
will rate-limit or ban heavy callers. Photon (a self-hostable OSM geocoder,
https://photon.komoot.io) and Pelias (https://pelias.io) are built for exactly
this kind of automated, higher-volume forward geocoding. The owner provisions
one of them and points ``GEOCODER_URL`` at it. We therefore only support those
two backends here; ``GEOCODER_PROVIDER=none`` (the default) disables geocoding
entirely.

Graceful-fallback behavior (IMPORTANT)
--------------------------------------
This module is *additive and config-gated*. With the new settings unset the
whole subsystem is inert:

* ``is_configured()`` returns ``False`` when no provider/URL is set.
* ``geocode()`` returns ``[]`` whenever the geocoder is unconfigured, the query
  is empty, the network call fails, the response is malformed, or no match is
  found. It NEVER raises — callers can treat it as "no enrichment available"
  and move on.
* The heavy/optional dependency ``httpx`` is imported lazily *inside* the
  function that performs the HTTP call, so importing this module can never
  crash the app even if ``httpx`` is somehow absent. If the import fails we log
  once and return ``[]``.

Config (read defensively via ``getattr`` so the module imports even before the
integrator adds these attributes to ``app.config.Settings``):

* ``GEOCODER_PROVIDER``  -> ``settings.geocoder_provider``  (photon|pelias|none, default "none")
* ``GEOCODER_URL``       -> ``settings.geocoder_url``        (base URL of the instance, default "")
* ``GEOCODER_TIMEOUT_S`` -> ``settings.geocoder_timeout_s``  (HTTP timeout seconds, default 8.0)
* ``PELIAS_API_KEY``     -> ``settings.pelias_api_key``      (optional, only for hosted Pelias)

Public API
----------
* ``is_configured() -> bool``
* ``geocode(query: str, limit: int = 5) -> list[Place]``
* ``Place`` (frozen dataclass: display_name, lat, lon, city, country, postcode + extras)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.config import settings

# Try to reuse the project's country canonicaliser so ``Place.country`` comes
# back as a clean ISO-2 code (NL/DE/FR/...). This import is light (pure data),
# but we still guard it so a future refactor can't break geocode imports.
try:  # pragma: no cover - trivial import guard
    from app.country_codes import to_iso2 as _to_iso2
except Exception:  # pragma: no cover
    def _to_iso2(value: str | None) -> str:  # type: ignore[misc]
        return (value or "").strip().upper()[:2]

logger = logging.getLogger(__name__)

# Recognised backends. Anything else (including "none" / "") => disabled.
_PHOTON = "photon"
_PELIAS = "pelias"
_VALID_PROVIDERS = {_PHOTON, _PELIAS}


@dataclass(frozen=True)
class Place:
    """A single geocoded result (pure value object, safe to cache/compare).

    Fields the spec requires are first; the extras (lat/lon precision aside)
    are handy for downstream enrichment (filling province / coordinates).
    """

    display_name: str  # human-readable label of the matched place
    lat: float  # latitude (WGS84)
    lon: float  # longitude (WGS84)
    city: str = ""  # locality / town / city
    country: str = ""  # ISO-2 country code (e.g. "NL"), best-effort
    postcode: str = ""  # postal / ZIP code
    region: str = ""  # province / state, when the backend supplies it
    raw: dict[str, Any] = field(default_factory=dict, compare=False)  # original feature


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------
def _provider() -> str:
    """Return the normalised, *enabled* provider name, or "" if disabled.

    Reads via ``getattr`` so the module is import-safe before the integrator
    adds ``geocoder_provider`` to the frozen ``Settings`` dataclass.
    """
    provider = (getattr(settings, "geocoder_provider", "none") or "none").strip().lower()
    if provider not in _VALID_PROVIDERS:
        return ""
    return provider


def _base_url() -> str:
    """Return the configured geocoder base URL (no trailing slash), or ""."""
    return (getattr(settings, "geocoder_url", "") or "").strip().rstrip("/")


def _timeout_s() -> float:
    """HTTP timeout in seconds (defensive parse; falls back to 8.0)."""
    try:
        return float(getattr(settings, "geocoder_timeout_s", 8.0) or 8.0)
    except (TypeError, ValueError):
        return 8.0


def is_configured() -> bool:
    """True iff a supported provider AND a base URL are both set.

    This is the single gate every caller should check (directly or implicitly
    via ``geocode()`` returning ``[]``). With ``GEOCODER_PROVIDER`` unset or
    ``none`` this returns ``False`` and the whole feature stays dormant.
    """
    return bool(_provider()) and bool(_base_url())


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def geocode(query: str, limit: int = 5) -> list[Place]:
    """Forward-geocode ``query`` into up to ``limit`` ``Place`` results.

    Returns ``[]`` (never raises) when:
      * the geocoder is not configured,
      * ``query`` is blank,
      * the HTTP call fails / times out,
      * the response cannot be parsed,
      * or there are simply no matches.

    The heavy dependency (``httpx``) is imported inside ``_http_get_json`` so a
    missing dependency degrades to ``[]`` instead of an import-time crash.
    """
    if not is_configured():
        return []

    q = (query or "").strip()
    if not q:
        return []

    # Clamp limit into a sane range so a caller can't ask for thousands.
    try:
        capped = max(1, min(int(limit), 25))
    except (TypeError, ValueError):
        capped = 5

    provider = _provider()
    base = _base_url()

    try:
        if provider == _PHOTON:
            url, params, headers = _build_photon_request(base, q, capped)
        else:  # _PELIAS
            url, params, headers = _build_pelias_request(base, q, capped)
    except Exception:  # pragma: no cover - defensive, request building is simple
        logger.info("[geocode] failed to build %s request; skipping", provider)
        return []

    data = _http_get_json(url, params, headers)
    if not isinstance(data, dict):
        return []

    # Photon and Pelias both return GeoJSON FeatureCollections, but with
    # different property schemas, so each has its own parser.
    if provider == _PHOTON:
        return _parse_photon(data, capped)
    return _parse_pelias(data, capped)


# ---------------------------------------------------------------------------
# Request builders (pure — easy to unit test)
# ---------------------------------------------------------------------------
def _build_photon_request(
    base: str, query: str, limit: int
) -> tuple[str, dict[str, Any], dict[str, str]]:
    """Photon: ``GET {base}/api?q=<query>&limit=<n>``.

    Photon needs no API key. We hint English labels so ``display_name`` is
    stable regardless of the server's default language.
    """
    url = f"{base}/api"
    params: dict[str, Any] = {"q": query, "limit": limit, "lang": "en"}
    headers = {"Accept": "application/json"}
    return url, params, headers


def _build_pelias_request(
    base: str, query: str, limit: int
) -> tuple[str, dict[str, Any], dict[str, str]]:
    """Pelias: ``GET {base}/v1/search?text=<query>&size=<n>[&api_key=...]``.

    A hosted Pelias (e.g. geocode.earth) needs ``PELIAS_API_KEY``; a
    self-hosted instance usually does not, so the key is optional.
    """
    url = f"{base}/v1/search"
    params: dict[str, Any] = {"text": query, "size": limit}
    api_key = (getattr(settings, "pelias_api_key", "") or "").strip()
    if api_key:
        params["api_key"] = api_key
    headers = {"Accept": "application/json"}
    return url, params, headers


# ---------------------------------------------------------------------------
# HTTP layer (the ONLY place that touches the network / imports httpx)
# ---------------------------------------------------------------------------
def _http_get_json(
    url: str, params: dict[str, Any], headers: dict[str, str]
) -> dict[str, Any] | None:
    """Perform a GET and return parsed JSON, or ``None`` on any failure.

    ``httpx`` is imported here (lazily) so that:
      * importing ``app.geocode`` never requires ``httpx`` to be installed, and
      * if it is missing/broken we log once and degrade to ``None`` (-> ``[]``).

    Tests monkeypatch *this* function (or the ``httpx`` it imports) so no real
    network call is ever made in the suite.
    """
    try:
        import httpx  # lazy, optional heavy dependency
    except Exception:  # pragma: no cover - dep genuinely absent
        logger.info("[geocode] httpx not installed; geocoding disabled")
        return None

    try:
        resp = httpx.get(url, params=params, headers=headers, timeout=_timeout_s())
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:  # network error, non-2xx, bad JSON, timeout, ...
        # INFO not ERROR: an unreachable/optional geocoder must not look like a
        # crash. The caller simply gets [] and continues without enrichment.
        logger.info("[geocode] request to %s failed: %s", url, exc)
        return None


# ---------------------------------------------------------------------------
# Response parsers (pure functions — the heart of the unit tests)
# ---------------------------------------------------------------------------
def _coords_from_geometry(feature: dict[str, Any]) -> tuple[float, float] | None:
    """Extract (lat, lon) from a GeoJSON feature's Point geometry.

    GeoJSON stores coordinates as ``[lon, lat]`` (x, y) — note the order.
    Returns ``None`` if the geometry is missing or not a usable Point.
    """
    geometry = feature.get("geometry") or {}
    coords = geometry.get("coordinates")
    if not isinstance(coords, (list, tuple)) or len(coords) < 2:
        return None
    try:
        lon = float(coords[0])
        lat = float(coords[1])
    except (TypeError, ValueError):
        return None
    return lat, lon


def _first_str(props: dict[str, Any], *keys: str) -> str:
    """Return the first non-empty string among ``props[key]`` for each key."""
    for key in keys:
        val = props.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
        # Some backends return non-string scalars (e.g. numeric postcode).
        if val is not None and not isinstance(val, (list, dict)) and str(val).strip():
            return str(val).strip()
    return ""


def _parse_photon(data: dict[str, Any], limit: int) -> list[Place]:
    """Parse a Photon GeoJSON FeatureCollection into ``Place`` objects.

    Photon feature ``properties`` look like::

        {"name": "...", "city": "...", "postcode": "...",
         "countrycode": "NL", "state": "...", "country": "Netherlands", ...}
    """
    features = data.get("features")
    if not isinstance(features, list):
        return []

    places: list[Place] = []
    for feature in features:
        if not isinstance(feature, dict):
            continue
        coords = _coords_from_geometry(feature)
        if coords is None:
            continue
        lat, lon = coords
        props = feature.get("properties") or {}
        if not isinstance(props, dict):
            props = {}

        # Photon uses an ISO-2 "countrycode"; fall back to the country name
        # and run it through the project canonicaliser for safety.
        country_raw = _first_str(props, "countrycode", "country")
        place = Place(
            display_name=_photon_display_name(props),
            lat=lat,
            lon=lon,
            city=_first_str(props, "city", "town", "village", "name"),
            country=_to_iso2(country_raw) or country_raw.upper()[:2],
            postcode=_first_str(props, "postcode"),
            region=_first_str(props, "state", "county"),
            raw=feature,
        )
        places.append(place)
        if len(places) >= limit:
            break
    return places


def _photon_display_name(props: dict[str, Any]) -> str:
    """Build a readable label from Photon properties.

    Photon does not provide a single ``display_name`` field (unlike
    Nominatim), so we assemble one from name/street/city/country.
    """
    parts: list[str] = []
    name = _first_str(props, "name")
    if name:
        parts.append(name)
    street = _first_str(props, "street")
    housenumber = _first_str(props, "housenumber")
    if street:
        parts.append(f"{street} {housenumber}".strip())
    for key in ("postcode", "city", "state", "country"):
        val = _first_str(props, key)
        if val and val not in parts:
            parts.append(val)
    return ", ".join(p for p in parts if p)


def _parse_pelias(data: dict[str, Any], limit: int) -> list[Place]:
    """Parse a Pelias GeoJSON FeatureCollection into ``Place`` objects.

    Pelias feature ``properties`` look like::

        {"label": "...", "locality": "...", "postalcode": "...",
         "country_a": "NLD", "region": "...", ...}

    Note ``country_a`` is ISO-3 (alpha-3); ``country_code`` (when present) is
    ISO-2. We prefer the ISO-2, then canonicalise whatever we get.
    """
    features = data.get("features")
    if not isinstance(features, list):
        return []

    places: list[Place] = []
    for feature in features:
        if not isinstance(feature, dict):
            continue
        coords = _coords_from_geometry(feature)
        if coords is None:
            continue
        lat, lon = coords
        props = feature.get("properties") or {}
        if not isinstance(props, dict):
            props = {}

        # Prefer an explicit ISO-2; otherwise let the canonicaliser try to
        # map the country name. (ISO-3 like "NLD" won't canonicalise, so we
        # also keep the raw value as a last resort.)
        country_raw = _first_str(props, "country_code", "country_a", "country")
        country = _to_iso2(country_raw)
        if not country and country_raw:
            # ISO-3 -> best-effort: keep the name field instead, canonicalised.
            country = _to_iso2(_first_str(props, "country")) or country_raw.upper()[:2]

        place = Place(
            display_name=_first_str(props, "label", "name"),
            lat=lat,
            lon=lon,
            city=_first_str(props, "locality", "localadmin", "county", "name"),
            country=country,
            postcode=_first_str(props, "postalcode", "postcode"),
            region=_first_str(props, "region", "macroregion"),
            raw=feature,
        )
        places.append(place)
        if len(places) >= limit:
            break
    return places
