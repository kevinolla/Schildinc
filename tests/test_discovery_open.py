"""Unit tests for app.discovery_open (the non-Google discovery orchestrator).

All tests are PURE / offline:
  * No network. The sibling ``search_client`` and ``web_extract`` modules are
    replaced with in-memory fakes (injected via ``sys.modules``) so the
    orchestrator's decision logic is exercised without any HTTP.
  * No real DB. ``discover_for_company`` only passes ``session`` through to
    ``web_extract``; our fake ignores it, so we hand it a trivial sentinel.

Covered scenarios (per the assignment brief):
  1. existing-website path  -> input_type="existing_website", extraction runs.
  2. confident-pick path    -> best searched candidate clears the autopick bar.
  3. low-confidence path    -> top candidate below the bar -> needs_review=True
                                with the ranked candidates returned for review.
Plus: the google-engine marker, the unconfigured-backend fallback, the
fuzzy scorer demoting directory domains, and the query builder.
"""
from __future__ import annotations

import sys
import types

import pytest

from app import discovery_open
from app.discovery_open import (
    DiscoveryOutcome,
    WebsiteChoice,
    _build_query,
    _fuzzy_score,
    discover_for_company,
)


# ---------------------------------------------------------------------------
# Test doubles for the two parallel sibling modules.
# ---------------------------------------------------------------------------

class _FakeSearchClient:
    """Stand-in for app.search_client with controllable output."""

    def __init__(self, configured: bool, results: list[dict]):
        self._configured = configured
        self._results = results
        self.calls: list[tuple] = []

    def is_configured(self) -> bool:
        return self._configured

    def find_website(self, name, city="", country_code="", **kwargs):
        self.calls.append((name, city, country_code))
        # Return plain dicts to also prove the dict-coercion path works.
        return list(self._results)


class _FakeWebExtract:
    """Stand-in for app.web_extract; records the website it was asked about."""

    def __init__(self, payload: dict):
        self._payload = payload
        self.websites: list[str] = []

    def discover_contacts(self, session=None, website="", company_name="", city="", **kwargs):
        self.websites.append(website)
        out = dict(self._payload)
        out.setdefault("website", website)
        return out


@pytest.fixture
def patch_siblings(monkeypatch):
    """Install fakes for search_client + web_extract into sys.modules.

    Returns a helper that wires both fakes in one call and yields them so the
    test can assert on recorded calls.
    """
    # NOTE: discovery_open does `from app import search_client` at call time,
    # which binds the ALREADY-IMPORTED real submodule (an attribute on the `app`
    # package), so swapping sys.modules does not take effect. We must monkeypatch
    # the attributes on the real modules instead.
    import importlib

    def _install(search_client=None, web_extract=None):
        if search_client is not None:
            real = importlib.import_module("app.search_client")
            monkeypatch.setattr(real, "is_configured", search_client.is_configured)
            monkeypatch.setattr(real, "find_website", search_client.find_website)
        if web_extract is not None:
            real = importlib.import_module("app.web_extract")
            monkeypatch.setattr(real, "discover_contacts", web_extract.discover_contacts)

    yield _install
    # monkeypatch auto-reverts at fixture teardown.


_FOUND_PAYLOAD = {
    "email": "info@velocitybikes.nl",
    "email_source_url": "https://velocitybikes.nl/contact",
    "email_confidence": 90,
    "phone": "+31 6 12345678",
    "linkedin_url": "https://www.linkedin.com/company/velocity-bikes",
    "instagram_url": "",
    "emails_found": ["info@velocitybikes.nl", "sales@velocitybikes.nl"],
    "pages_scanned": ["https://velocitybikes.nl/", "https://velocitybikes.nl/contact"],
    "status": "found",
}


# ---------------------------------------------------------------------------
# 1. Existing-website path
# ---------------------------------------------------------------------------

def test_existing_website_skips_search_and_extracts(patch_siblings):
    search = _FakeSearchClient(configured=True, results=[])  # must NOT be called
    extract = _FakeWebExtract(_FOUND_PAYLOAD)
    patch_siblings(search_client=search, web_extract=extract)

    outcome = discover_for_company(
        session=object(),
        name="Velocity Bikes",
        city="Amsterdam",
        country="NL",
        website="https://velocitybikes.nl",
    )

    assert isinstance(outcome, DiscoveryOutcome)
    assert outcome.discovery_input_type == "existing_website"
    assert outcome.website == "https://velocitybikes.nl"
    assert outcome.website_domain == "velocitybikes.nl"
    assert outcome.website_confidence == 100
    assert outcome.needs_review is False
    assert outcome.backend == "open"
    assert outcome.status == "found"
    assert outcome.email_public == "info@velocitybikes.nl"
    assert outcome.email_source_page == "https://velocitybikes.nl/contact"
    # search was bypassed entirely
    assert search.calls == []
    # extraction ran against the provided website
    assert extract.websites == ["https://velocitybikes.nl"]


# ---------------------------------------------------------------------------
# 2. Confident-pick path (search -> strong match -> extract)
# ---------------------------------------------------------------------------

def test_confident_search_pick_extracts(patch_siblings, monkeypatch):
    # Lower the autopick bar deterministically so we don't depend on whether
    # rapidfuzz is installed in the test env.
    monkeypatch.setattr(discovery_open, "_autopick_score", lambda: 80)

    results = [
        {
            "url": "https://velocitybikes.nl",
            "domain": "velocitybikes.nl",
            "title": "Velocity Bikes Amsterdam",
            "snippet": "Premium bicycle shop",
            "score": 95,
            "engine": "duckduckgo",
        },
        {
            "url": "https://facebook.com/velocitybikes",
            "domain": "facebook.com",
            "title": "Velocity Bikes | Facebook",
            "score": 70,
            "engine": "bing",
        },
    ]
    search = _FakeSearchClient(configured=True, results=results)
    extract = _FakeWebExtract(_FOUND_PAYLOAD)
    patch_siblings(search_client=search, web_extract=extract)

    outcome = discover_for_company(
        session=object(),
        name="Velocity Bikes",
        city="Amsterdam",
        country="NL",
    )

    assert outcome.discovery_input_type == "search"
    assert outcome.needs_review is False
    assert outcome.website == "https://velocitybikes.nl"
    assert outcome.website_confidence >= 80
    assert outcome.status == "found"
    assert outcome.email_public == "info@velocitybikes.nl"
    # The directory (facebook) candidate must rank below the own-domain site.
    assert outcome.candidates[0].domain == "velocitybikes.nl"
    assert search.calls and search.calls[0][0] == "Velocity Bikes"
    assert extract.websites == ["https://velocitybikes.nl"]


# ---------------------------------------------------------------------------
# 3. Low-confidence path -> needs_review, no extraction
# ---------------------------------------------------------------------------

def test_low_confidence_routes_to_review(patch_siblings, monkeypatch):
    # Force a high autopick bar so even a decent match falls short.
    monkeypatch.setattr(discovery_open, "_autopick_score", lambda: 99)

    results = [
        {
            "url": "https://some-unrelated-directory.example/listing/123",
            "domain": "some-unrelated-directory.example",
            "title": "Business listing",
            "score": 40,
            "engine": "bing",
        }
    ]
    search = _FakeSearchClient(configured=True, results=results)
    extract = _FakeWebExtract(_FOUND_PAYLOAD)
    patch_siblings(search_client=search, web_extract=extract)

    outcome = discover_for_company(
        session=object(),
        name="Totally Different Name BV",
        city="Rotterdam",
        country="NL",
    )

    assert outcome.needs_review is True
    assert outcome.status == "needs_review"
    assert outcome.website == ""  # nothing trusted
    assert outcome.candidates  # candidates surfaced for the human queue
    # Extraction must NOT have run on a low-confidence guess.
    assert extract.websites == []


# ---------------------------------------------------------------------------
# Extra: backend / configuration edge cases
# ---------------------------------------------------------------------------

def test_google_engine_returns_fallback_marker(monkeypatch):
    monkeypatch.setattr(discovery_open, "_engine_mode", lambda: "google")
    outcome = discover_for_company(session=object(), name="Anything", city="X")
    assert outcome.backend == "google"
    assert outcome.status == "use_google_fallback"
    assert outcome.needs_review is False


def test_unconfigured_backend_falls_back_to_review(patch_siblings):
    search = _FakeSearchClient(configured=False, results=[])
    extract = _FakeWebExtract(_FOUND_PAYLOAD)
    patch_siblings(search_client=search, web_extract=extract)

    outcome = discover_for_company(session=object(), name="No Backend Co", city="Utrecht")
    assert outcome.needs_review is True
    assert outcome.status == "needs_review"
    assert outcome.candidates == []
    # is_configured() short-circuits before find_website is called.
    assert search.calls == []


def test_missing_name_and_website_needs_review():
    # No siblings needed: we never reach search.
    outcome = discover_for_company(session=object(), name="", city="X")
    assert outcome.needs_review is True
    assert outcome.status == "needs_review"


def test_no_candidates_returned_needs_review(patch_siblings):
    search = _FakeSearchClient(configured=True, results=[])
    extract = _FakeWebExtract(_FOUND_PAYLOAD)
    patch_siblings(search_client=search, web_extract=extract)

    outcome = discover_for_company(session=object(), name="Empty Results Co", city="Den Haag")
    assert outcome.needs_review is True
    assert outcome.status == "needs_review"
    assert outcome.discovery_query_used  # query was built and recorded
    assert extract.websites == []


def test_siblings_missing_entirely_does_not_raise():
    # With no fakes installed and the real modules absent, importing them fails
    # gracefully -> needs_review, never an exception.
    sys.modules.pop("app.search_client", None)
    sys.modules.pop("app.web_extract", None)
    outcome = discover_for_company(session=object(), name="Lonely Co", city="Eindhoven")
    assert isinstance(outcome, DiscoveryOutcome)
    assert outcome.needs_review is True


# ---------------------------------------------------------------------------
# Extra: pure helper coverage
# ---------------------------------------------------------------------------

def test_fuzzy_score_demotes_directories():
    own = WebsiteChoice(url="https://acme.nl", domain="acme.nl", title="Acme BV")
    fb = WebsiteChoice(url="https://facebook.com/acme", domain="facebook.com", title="Acme BV")
    assert _fuzzy_score("Acme", own) > _fuzzy_score("Acme", fb)
    assert _fuzzy_score("Acme", fb) <= 5


def test_fuzzy_score_rejects_substring_in_bigger_brand():
    """Regression: "Dolf Wallet & Zn." must NOT match nerdwallet.com — 'wallet'
    is a substring but 'nerd' is unexplained brand content -> below autopick."""
    nerd = WebsiteChoice(url="https://nerdwallet.com", domain="nerdwallet.com", title="NerdWallet")
    assert _fuzzy_score("Dolf Wallet & Zn.", nerd) < 80
    # Legit cases still auto-accept (distinctive token + only generic leftover):
    own_a = WebsiteChoice(url="https://elgersmarijwielen.nl", domain="elgersmarijwielen.nl", title="Elgersma")
    assert _fuzzy_score("Elgersma Rijwielen", own_a) >= 85
    own_b = WebsiteChoice(url="https://rijwielhandelbakker.nl", domain="rijwielhandelbakker.nl", title="Bakker")
    assert _fuzzy_score("Rijwielhandel Bakker", own_b) >= 85
    # The lead's own exact-brand domain still works even if a word is a dict word:
    own_c = WebsiteChoice(url="https://dolfwallet.nl", domain="dolfwallet.nl", title="Dolf Wallet")
    assert _fuzzy_score("Dolf Wallet & Zn.", own_c) >= 85


def test_build_query_includes_city_and_postal():
    q = _build_query("Acme Bikes", "Amsterdam", "NL", "1011AB")
    assert "Acme Bikes" in q
    assert "Amsterdam" in q
    assert "1011AB" in q
    # NL is the default home market -> not appended as a token.
    assert q.split() and "NL" not in q.split()


def test_build_query_appends_foreign_country():
    q = _build_query("Acme Bikes", "Berlin", "DE", "")
    assert "DE" in q
