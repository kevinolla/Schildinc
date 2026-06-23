"""Unit tests for app.geocode — PURE response-parsing only.

These tests never touch the network. We monkeypatch the single HTTP entry
point (``app.geocode._http_get_json``) to return canned Photon / Pelias
GeoJSON payloads, and we toggle configuration by monkeypatching the
``settings`` attributes the module reads via ``getattr``.

Covered:
  * Photon GeoJSON parsing -> Place fields (incl. GeoJSON [lon, lat] order).
  * Pelias GeoJSON parsing -> Place fields (ISO-3 country handling, label).
  * Unconfigured (GEOCODER_PROVIDER unset / "none") -> [] and is_configured False.
  * Empty query -> [].
  * HTTP failure (parser sees None) -> [] (no exception).
  * limit clamps the number of results.
"""
from __future__ import annotations

import app.geocode as geocode
from app.geocode import Place


# ---------------------------------------------------------------------------
# Sample payloads (trimmed real-world shapes)
# ---------------------------------------------------------------------------
SAMPLE_PHOTON = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [4.895168, 52.370216]},
            "properties": {
                "name": "Amsterdam",
                "city": "Amsterdam",
                "postcode": "1011",
                "countrycode": "NL",
                "country": "Netherlands",
                "state": "North Holland",
                "street": "Damrak",
                "housenumber": "1",
            },
        },
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [6.957616, 50.941278]},
            "properties": {
                "name": "Koln",
                "city": "Cologne",
                "postcode": "50667",
                "countrycode": "DE",
                "country": "Germany",
                "state": "North Rhine-Westphalia",
            },
        },
    ],
}

SAMPLE_PELIAS = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [4.895168, 52.370216]},
            "properties": {
                "label": "Damrak 1, Amsterdam, Netherlands",
                "name": "Damrak 1",
                "locality": "Amsterdam",
                "postalcode": "1011AB",
                "country_a": "NLD",  # ISO-3
                "country_code": "NL",  # ISO-2 (preferred)
                "country": "Netherlands",
                "region": "Noord-Holland",
            },
        }
    ],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _configure(monkeypatch, provider="photon", url="http://geo.local", pelias_api_key=""):
    """Point the module at a fake configured geocoder.

    ``app.config.Settings`` is a FROZEN dataclass, so we cannot monkeypatch its
    attributes. Instead we patch the module-level config accessors that wrap
    those ``getattr(settings, ...)`` reads — this is the seam designed for it
    and keeps tests fully decoupled from the real settings object.
    """
    # ``_provider`` already normalises/validates, so emulate the real result:
    # an unsupported provider resolves to "" (disabled).
    resolved = provider if provider in geocode._VALID_PROVIDERS else ""
    monkeypatch.setattr(geocode, "_provider", lambda: resolved)
    monkeypatch.setattr(geocode, "_base_url", lambda: url.rstrip("/"))
    monkeypatch.setattr(geocode, "_timeout_s", lambda: 8.0)
    if provider == "pelias":
        # Pelias request builder reads pelias_api_key via getattr; emulate it.
        monkeypatch.setattr(
            geocode,
            "_build_pelias_request",
            lambda base, query, limit: (
                f"{base}/v1/search",
                {"text": query, "size": limit, **({"api_key": pelias_api_key} if pelias_api_key else {})},
                {"Accept": "application/json"},
            ),
        )


def _stub_http(monkeypatch, payload):
    """Replace the only network function with one returning ``payload``."""
    monkeypatch.setattr(geocode, "_http_get_json", lambda *a, **k: payload)


# ---------------------------------------------------------------------------
# is_configured / disabled paths
# ---------------------------------------------------------------------------
def test_not_configured_when_provider_none(monkeypatch):
    # "none"/unsupported provider -> _provider() returns "".
    monkeypatch.setattr(geocode, "_provider", lambda: "")
    monkeypatch.setattr(geocode, "_base_url", lambda: "http://geo.local")
    assert geocode.is_configured() is False
    # And geocode() short-circuits to [] without ever hitting HTTP.
    assert geocode.geocode("Amsterdam") == []


def test_not_configured_when_url_missing(monkeypatch):
    monkeypatch.setattr(geocode, "_provider", lambda: "photon")
    monkeypatch.setattr(geocode, "_base_url", lambda: "")
    assert geocode.is_configured() is False
    assert geocode.geocode("Amsterdam") == []


def test_configured_true(monkeypatch):
    _configure(monkeypatch, provider="photon")
    assert geocode.is_configured() is True


def test_unknown_provider_is_disabled(monkeypatch):
    _configure(monkeypatch, provider="nominatim")  # unsupported -> disabled
    assert geocode.is_configured() is False
    assert geocode.geocode("Amsterdam") == []


# ---------------------------------------------------------------------------
# Photon parsing
# ---------------------------------------------------------------------------
def test_photon_parsing(monkeypatch):
    _configure(monkeypatch, provider="photon")
    _stub_http(monkeypatch, SAMPLE_PHOTON)

    results = geocode.geocode("Damrak Amsterdam", limit=5)
    assert len(results) == 2
    assert all(isinstance(r, Place) for r in results)

    first = results[0]
    # GeoJSON is [lon, lat]; we must surface lat=52.37, lon=4.89.
    assert first.lat == 52.370216
    assert first.lon == 4.895168
    assert first.city == "Amsterdam"
    assert first.country == "NL"
    assert first.postcode == "1011"
    assert first.region == "North Holland"
    assert "Amsterdam" in first.display_name
    # raw GeoJSON feature is preserved for downstream use.
    assert first.raw["geometry"]["type"] == "Point"

    second = results[1]
    assert second.country == "DE"
    assert second.city == "Cologne"


def test_photon_limit_clamps_results(monkeypatch):
    _configure(monkeypatch, provider="photon")
    _stub_http(monkeypatch, SAMPLE_PHOTON)
    results = geocode.geocode("anything", limit=1)
    assert len(results) == 1
    assert results[0].country == "NL"


# ---------------------------------------------------------------------------
# Pelias parsing
# ---------------------------------------------------------------------------
def test_pelias_parsing(monkeypatch):
    _configure(monkeypatch, provider="pelias")
    _stub_http(monkeypatch, SAMPLE_PELIAS)

    results = geocode.geocode("Damrak 1 Amsterdam", limit=5)
    assert len(results) == 1
    place = results[0]
    assert isinstance(place, Place)
    assert place.lat == 52.370216
    assert place.lon == 4.895168
    assert place.city == "Amsterdam"
    # country_code (ISO-2) preferred over country_a (ISO-3 "NLD").
    assert place.country == "NL"
    assert place.postcode == "1011AB"
    assert place.region == "Noord-Holland"
    assert place.display_name == "Damrak 1, Amsterdam, Netherlands"


def test_pelias_iso3_only_falls_back_to_name(monkeypatch):
    """When only country_a (ISO-3) + country name are present, still resolve ISO-2."""
    payload = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [2.3522, 48.8566]},
                "properties": {
                    "label": "Paris, France",
                    "locality": "Paris",
                    "postalcode": "75001",
                    "country_a": "FRA",  # ISO-3, no ISO-2 field
                    "country": "France",
                    "region": "Ile-de-France",
                },
            }
        ],
    }
    _configure(monkeypatch, provider="pelias")
    _stub_http(monkeypatch, payload)
    results = geocode.geocode("Paris")
    assert len(results) == 1
    # to_iso2("France") -> "FR"
    assert results[0].country == "FR"


# ---------------------------------------------------------------------------
# Robustness: empty query, HTTP failure, malformed payloads
# ---------------------------------------------------------------------------
def test_empty_query_returns_empty(monkeypatch):
    _configure(monkeypatch, provider="photon")
    _stub_http(monkeypatch, SAMPLE_PHOTON)
    assert geocode.geocode("") == []
    assert geocode.geocode("   ") == []


def test_http_failure_returns_empty(monkeypatch):
    _configure(monkeypatch, provider="photon")
    # Simulate a failed request: the HTTP helper returns None on any error.
    monkeypatch.setattr(geocode, "_http_get_json", lambda *a, **k: None)
    assert geocode.geocode("Amsterdam") == []


def test_malformed_payload_returns_empty(monkeypatch):
    _configure(monkeypatch, provider="photon")
    # Not a dict / no "features" list -> [] (never raises).
    monkeypatch.setattr(geocode, "_http_get_json", lambda *a, **k: {"oops": True})
    assert geocode.geocode("Amsterdam") == []
    monkeypatch.setattr(geocode, "_http_get_json", lambda *a, **k: ["not", "a", "dict"])
    assert geocode.geocode("Amsterdam") == []


def test_feature_without_geometry_is_skipped(monkeypatch):
    payload = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"city": "Ghost"}},  # no geometry
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [4.9, 52.3]},
                "properties": {"city": "Amsterdam", "countrycode": "NL"},
            },
        ],
    }
    _configure(monkeypatch, provider="photon")
    _stub_http(monkeypatch, payload)
    results = geocode.geocode("x")
    assert len(results) == 1
    assert results[0].city == "Amsterdam"


# ---------------------------------------------------------------------------
# Pure-function spot checks (no settings / no HTTP needed)
# ---------------------------------------------------------------------------
def test_coords_from_geometry_order():
    feat = {"geometry": {"type": "Point", "coordinates": [4.9, 52.3]}}
    assert geocode._coords_from_geometry(feat) == (52.3, 4.9)


def test_coords_from_geometry_bad_input():
    assert geocode._coords_from_geometry({}) is None
    assert geocode._coords_from_geometry({"geometry": {"coordinates": [1]}}) is None
    assert geocode._coords_from_geometry({"geometry": {"coordinates": ["a", "b"]}}) is None


def test_first_str_picks_first_nonempty():
    props = {"a": "", "b": None, "c": "value", "d": "later"}
    assert geocode._first_str(props, "a", "b", "c", "d") == "value"
    assert geocode._first_str(props, "missing") == ""
    # Non-string scalar (e.g. numeric postcode) is coerced to str.
    assert geocode._first_str({"postcode": 1011}, "postcode") == "1011"
