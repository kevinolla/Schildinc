"""Unit tests for app/search_client.py.

PURE / offline only — no network, no real SearXNG, no DB. We exercise:

  * the unconfigured fallback (SEARXNG_URL unset -> [] / None, no raise),
  * URL -> domain normalization on parsed results,
  * directory/social demotion + ccTLD boost in ranking,
  * the in-process cache (second call with fetch removed still returns),
  * best_website threshold gating.

All "network" is faked by monkeypatching ``search_client._fetch_json`` so a
single fake payload is returned. settings are frozen, so we monkeypatch the
attribute on the imported ``settings`` object via ``setattr`` (object.__setattr__
is needed because the dataclass is frozen).
"""
from __future__ import annotations

import pytest

from app import search_client
from app.config import settings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _set_setting(monkeypatch, name: str, value) -> None:
    """Set an attribute on the frozen settings dataclass for the test only.

    ``Settings`` is ``frozen=True`` so normal setattr raises; we go through
    ``object.__setattr__`` and restore via monkeypatch's undo by capturing the
    original first. Using monkeypatch.setattr with raising=False handles the
    teardown for attributes that already exist; for ones that don't yet exist
    (added later by the integrator) we set + register an explicit undo.
    """
    sentinel = object()
    original = getattr(settings, name, sentinel)

    object.__setattr__(settings, name, value)

    def _undo():
        if original is sentinel:
            try:
                object.__delattr__(settings, name)
            except AttributeError:
                pass
        else:
            object.__setattr__(settings, name, original)

    monkeypatch.setattr(  # piggyback monkeypatch teardown ordering
        search_client, "_TEST_UNDO_" + name, _undo, raising=False
    )
    # Register the undo so pytest runs it at teardown.
    request_finalizer = getattr(_set_setting, "_finalizers", None)
    if request_finalizer is None:
        _set_setting._finalizers = []  # type: ignore[attr-defined]
    _set_setting._finalizers.append(_undo)  # type: ignore[attr-defined]


@pytest.fixture(autouse=True)
def _isolation():
    """Clear cache + restore settings undos around every test."""
    search_client.clear_cache()
    search_client.set_db_cache_backend(None)
    yield
    # Run any settings undos registered during the test.
    finalizers = getattr(_set_setting, "_finalizers", [])
    for undo in reversed(finalizers):
        try:
            undo()
        except Exception:
            pass
    finalizers.clear()
    search_client.clear_cache()
    search_client.set_db_cache_backend(None)


def _payload(*rows: dict) -> dict:
    return {"results": list(rows)}


# ---------------------------------------------------------------------------
# Unconfigured fallback
# ---------------------------------------------------------------------------

def test_unconfigured_is_not_configured(monkeypatch):
    _set_setting(monkeypatch, "searxng_url", "")
    assert search_client.is_configured() is False


def test_unconfigured_search_returns_empty_and_does_not_fetch(monkeypatch):
    _set_setting(monkeypatch, "searxng_url", "")

    called = {"n": 0}

    def _boom(*a, **k):
        called["n"] += 1
        raise AssertionError("must not fetch when unconfigured")

    monkeypatch.setattr(search_client, "_fetch_json", _boom)

    assert search_client.search("anything") == []
    assert search_client.find_website("Bike City", "Utrecht", "NL") == []
    assert search_client.best_website("Bike City", "Utrecht", "NL") is None
    assert called["n"] == 0


def test_empty_query_returns_empty(monkeypatch):
    _set_setting(monkeypatch, "searxng_url", "https://searx.example.com")
    monkeypatch.setattr(
        search_client, "_fetch_json",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no fetch for empty q")),
    )
    assert search_client.search("   ") == []
    assert search_client.find_website("") == []


# ---------------------------------------------------------------------------
# URL / domain normalization on parsed results
# ---------------------------------------------------------------------------

def test_search_normalizes_domain_strips_www_and_path(monkeypatch):
    _set_setting(monkeypatch, "searxng_url", "https://searx.example.com")
    monkeypatch.setattr(
        search_client, "_fetch_json",
        lambda *a, **k: _payload(
            {"url": "https://www.bikecity.nl/contact", "title": "Bike City", "content": "x"},
        ),
    )
    results = search_client.search("bike city")
    assert len(results) == 1
    # www. stripped, path dropped -> bare registrable host
    assert results[0].domain == "bikecity.nl"
    assert results[0].url == "https://www.bikecity.nl/contact"


def test_search_skips_rows_without_url(monkeypatch):
    _set_setting(monkeypatch, "searxng_url", "https://searx.example.com")
    monkeypatch.setattr(
        search_client, "_fetch_json",
        lambda *a, **k: _payload(
            {"title": "no url here"},
            {"url": "https://realsite.nl", "title": "Real"},
        ),
    )
    results = search_client.search("real")
    assert [r.domain for r in results] == ["realsite.nl"]


def test_malformed_payload_returns_empty(monkeypatch):
    _set_setting(monkeypatch, "searxng_url", "https://searx.example.com")
    # _fetch_json returns something that is not a dict-with-results
    monkeypatch.setattr(search_client, "_fetch_json", lambda *a, **k: {"unexpected": True})
    assert search_client.search("x") == []


def test_fetch_failure_returns_empty_and_does_not_cache(monkeypatch):
    _set_setting(monkeypatch, "searxng_url", "https://searx.example.com")
    monkeypatch.setattr(search_client, "_fetch_json", lambda *a, **k: None)
    assert search_client.search("bike city") == []
    # A miss must NOT be cached (so a transient outage isn't pinned).
    assert search_client._cache_get(search_client._cache_key("bike city", 10)) is None


# ---------------------------------------------------------------------------
# Ranking: directory demotion + ccTLD boost + name overlap
# ---------------------------------------------------------------------------

def test_find_website_demotes_directories_below_own_domain(monkeypatch):
    _set_setting(monkeypatch, "searxng_url", "https://searx.example.com")
    _set_setting(monkeypatch, "discovery_review_threshold", 50)
    monkeypatch.setattr(
        search_client, "_fetch_json",
        lambda *a, **k: _payload(
            {"url": "https://www.facebook.com/bikecity", "title": "Bike City | Facebook"},
            {"url": "https://www.bikecity.nl", "title": "Bike City - Home"},
            {"url": "https://kvk.nl/bikecity", "title": "Bike City - KVK"},
        ),
    )
    results = search_client.find_website("Bike City", "Utrecht", "NL")
    # Own domain must rank first; facebook + kvk demoted.
    assert results[0].domain == "bikecity.nl"
    fb = next(r for r in results if r.domain == "facebook.com")
    kvk = next(r for r in results if r.domain == "kvk.nl")
    assert results[0].score > fb.score
    assert results[0].score > kvk.score
    assert fb.score <= 15
    assert kvk.score <= 15


def test_cctld_boost_prefers_country_domain(monkeypatch):
    _set_setting(monkeypatch, "searxng_url", "https://searx.example.com")
    # Use a partial name match (only ONE of two tokens lands in the domain) so
    # the base overlap score is ~50, leaving headroom below the 100 cap for the
    # +10 ccTLD bonus to be observable.
    monkeypatch.setattr(
        search_client, "_fetch_json",
        lambda *a, **k: _payload(
            {"url": "https://bikeshop.com", "title": "Bike Shop"},
            {"url": "https://bikeshop.nl", "title": "Bike Shop"},
        ),
    )
    nl_results = search_client.find_website("Bike Amsterdam", "Utrecht", "NL")
    by_domain = {r.domain: r.score for r in nl_results}
    # The .nl domain gets the +10 ccTLD bonus for an NL prospect; neither is at
    # the 100 cap so the bonus is visible.
    assert by_domain["bikeshop.nl"] > by_domain["bikeshop.com"]


def test_best_website_respects_threshold(monkeypatch):
    _set_setting(monkeypatch, "searxng_url", "https://searx.example.com")
    monkeypatch.setattr(
        search_client, "_fetch_json",
        lambda *a, **k: _payload(
            {"url": "https://totally-unrelated-domain.xyz", "title": "Random Blog"},
        ),
    )
    # High threshold -> the weak candidate is rejected -> None (manual review).
    _set_setting(monkeypatch, "discovery_review_threshold", 90)
    assert search_client.best_website("Bike City", "Utrecht", "NL") is None

    # Low threshold + strong match -> a result is returned.
    search_client.clear_cache()
    monkeypatch.setattr(
        search_client, "_fetch_json",
        lambda *a, **k: _payload(
            {"url": "https://bikecity.nl", "title": "Bike City"},
        ),
    )
    _set_setting(monkeypatch, "discovery_review_threshold", 40)
    top = search_client.best_website("Bike City", "Utrecht", "NL")
    assert top is not None
    assert top.domain == "bikecity.nl"


# ---------------------------------------------------------------------------
# Cache behaviour
# ---------------------------------------------------------------------------

def test_in_process_cache_avoids_second_fetch(monkeypatch):
    _set_setting(monkeypatch, "searxng_url", "https://searx.example.com")
    calls = {"n": 0}

    def _fake(*a, **k):
        calls["n"] += 1
        return _payload({"url": "https://bikecity.nl", "title": "Bike City"})

    monkeypatch.setattr(search_client, "_fetch_json", _fake)

    first = search_client.search("bike city")
    second = search_client.search("bike city")  # identical normalized key
    assert calls["n"] == 1  # served from cache the 2nd time
    assert first == second
    assert first[0].domain == "bikecity.nl"


def test_cache_key_normalizes_query_casing_and_spacing(monkeypatch):
    _set_setting(monkeypatch, "searxng_url", "https://searx.example.com")
    calls = {"n": 0}

    def _fake(*a, **k):
        calls["n"] += 1
        return _payload({"url": "https://bikecity.nl", "title": "Bike City"})

    monkeypatch.setattr(search_client, "_fetch_json", _fake)

    search_client.search("Bike   City")
    search_client.search("bike city")  # same after normalize_text
    assert calls["n"] == 1


def test_clear_cache_forces_refetch(monkeypatch):
    _set_setting(monkeypatch, "searxng_url", "https://searx.example.com")
    calls = {"n": 0}

    def _fake(*a, **k):
        calls["n"] += 1
        return _payload({"url": "https://bikecity.nl", "title": "Bike City"})

    monkeypatch.setattr(search_client, "_fetch_json", _fake)

    search_client.search("bike city")
    search_client.clear_cache()
    search_client.search("bike city")
    assert calls["n"] == 2


def test_cache_eviction_on_overflow(monkeypatch):
    _set_setting(monkeypatch, "searxng_url", "https://searx.example.com")
    # Shrink the cap so we don't have to insert thousands of entries.
    monkeypatch.setattr(search_client, "_CACHE_MAX", 3)
    monkeypatch.setattr(
        search_client, "_fetch_json",
        lambda *a, **k: _payload({"url": "https://x.nl", "title": "X"}),
    )
    for i in range(5):
        search_client.search(f"query number {i}")
    # The cache cleared at the cap boundary; never exceeds the cap.
    assert len(search_client._CACHE) <= 3


def test_expired_cache_entry_is_a_miss(monkeypatch):
    _set_setting(monkeypatch, "searxng_url", "https://searx.example.com")
    monkeypatch.setattr(
        search_client, "_fetch_json",
        lambda *a, **k: _payload({"url": "https://x.nl", "title": "X"}),
    )
    # TTL of 0 means every cached entry is already expired on read.
    monkeypatch.setattr(search_client, "_CACHE_TTL_SECONDS", 0.0)
    calls = {"n": 0}
    real = search_client._fetch_json

    def _counting(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(search_client, "_fetch_json", _counting)

    search_client.search("bike city")
    search_client.search("bike city")
    assert calls["n"] == 2  # expired immediately -> refetch


# ---------------------------------------------------------------------------
# Optional DB cache backend hook
# ---------------------------------------------------------------------------

def test_db_cache_backend_get_short_circuits_network(monkeypatch):
    _set_setting(monkeypatch, "searxng_url", "https://searx.example.com")

    class FakeBackend:
        def get(self, key):
            return [{"url": "https://fromdb.nl", "domain": "fromdb.nl",
                     "title": "From DB", "score": 99, "snippet": "", "engine": "db"}]

        def set(self, key, results):
            raise AssertionError("set should not be called on a get hit")

    search_client.set_db_cache_backend(FakeBackend())
    monkeypatch.setattr(
        search_client, "_fetch_json",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no network on DB hit")),
    )
    results = search_client.search("bike city")
    assert results and results[0].domain == "fromdb.nl"


def test_db_cache_backend_errors_are_swallowed(monkeypatch):
    _set_setting(monkeypatch, "searxng_url", "https://searx.example.com")

    class BrokenBackend:
        def get(self, key):
            raise RuntimeError("db down")

        def set(self, key, results):
            raise RuntimeError("db down")

    search_client.set_db_cache_backend(BrokenBackend())
    monkeypatch.setattr(
        search_client, "_fetch_json",
        lambda *a, **k: _payload({"url": "https://bikecity.nl", "title": "Bike City"}),
    )
    # Broken backend must not break the call — falls through to network.
    results = search_client.search("bike city")
    assert results and results[0].domain == "bikecity.nl"


# ---------------------------------------------------------------------------
# Result objects + dataclass shape
# ---------------------------------------------------------------------------

def test_searchresult_fields_match_orchestrator_contract():
    # discovery_open._coerce_candidate reads url/domain/title/score (+snippet/engine).
    r = search_client.SearchResult(
        title="t", url="https://x.nl", domain="x.nl", score=50, snippet="s", engine="e"
    )
    assert r.title == "t" and r.url == "https://x.nl" and r.domain == "x.nl"
    assert r.score == 50 and r.snippet == "s" and r.engine == "e"
    # Alias used by the design doc / orchestrator.
    assert search_client.WebsiteCandidate is search_client.SearchResult
