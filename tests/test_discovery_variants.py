"""Phase 2 discovery recall tests — and the critical precision-preservation lock.

The whole point: more query variants / more candidates must NEVER lower the
acceptance bar. A directory/junk domain surfaced by a recall variant is still
floored by _fuzzy_score and routed to review, exactly as before.
"""
from __future__ import annotations

import importlib
import types

from app import discovery_open
from app.discovery_open import _query_variants


def test_query_variants_include_name_city_and_sector_clue():
    variants = _query_variants("Velocity Bikes", "Amsterdam", "NL", "1011AB")
    assert "Velocity Bikes" in variants                      # bare name
    assert any("Amsterdam" in v for v in variants)           # name + city
    assert any("fietsenwinkel" in v.lower() for v in variants)  # sector clue
    assert len(variants) <= discovery_open._max_variants()
    # de-duplicated (case-insensitive)
    assert len({v.lower() for v in variants}) == len(variants)


def test_query_variants_empty_name_returns_nothing():
    assert _query_variants("", "Amsterdam", "NL", "") == []


def _result(url, domain, title="", score=0):
    return types.SimpleNamespace(url=url, domain=domain, title=title, snippet="", score=score, engine="bing")


def _patch_search(monkeypatch, results):
    """Make search_client.search return a fixed candidate set for every query."""
    sc = importlib.import_module("app.search_client")
    monkeypatch.setattr(sc, "is_configured", lambda: True)
    monkeypatch.setattr(sc, "search", lambda q, limit=10: list(results))


def _patch_extract(monkeypatch):
    we = importlib.import_module("app.web_extract")

    def fake_discover(session=None, website="", company_name="", city="", **kwargs):
        return {"email": "info@velocitybikes.nl", "status": "found",
                "email_source_url": website, "email_confidence": 90}

    monkeypatch.setattr(we, "discover_contacts", fake_discover)


def test_recall_dedupes_candidates_by_domain(monkeypatch):
    monkeypatch.setattr(discovery_open, "_recall_variants_enabled", lambda: True)
    # Every variant returns the same two domains -> must collapse to two.
    _patch_search(monkeypatch, [
        _result("https://velocitybikes.nl", "velocitybikes.nl", "Velocity Bikes"),
        _result("https://www.velocitybikes.nl/contact", "www.velocitybikes.nl", "Contact"),
        _result("https://telefoonboek.nl/x", "telefoonboek.nl", "Velocity Bikes - Telefoonboek"),
    ])
    cands = discovery_open._collect_candidates_multi("Velocity Bikes", "Amsterdam", "NL", "", None)
    domains = {c.domain.replace("www.", "") for c in cands}
    assert "velocitybikes.nl" in domains
    assert "telefoonboek.nl" in domains
    assert len(cands) <= discovery_open._max_candidates()


def test_recall_still_autoaccepts_only_distinctive_domain(monkeypatch):
    """A real own-domain among junk variants is accepted; junk is not."""
    monkeypatch.setattr(discovery_open, "_recall_variants_enabled", lambda: True)
    _patch_search(monkeypatch, [
        _result("https://telefoonboek.nl/x", "telefoonboek.nl", "Velocity Bikes"),
        _result("https://velocitybikes.nl", "velocitybikes.nl", "Velocity Bikes Amsterdam"),
        _result("https://marktplaats.nl/y", "marktplaats.nl", "Velocity Bikes"),
    ])
    _patch_extract(monkeypatch)
    outcome = discovery_open.discover_for_company(
        session=object(), name="Velocity Bikes", city="Amsterdam", country="NL"
    )
    assert outcome.needs_review is False
    assert outcome.website == "https://velocitybikes.nl"
    assert outcome.website_confidence >= discovery_open._autopick_score()


def test_recall_junk_only_never_autoaccepts(monkeypatch):
    """The precision lock: even with many variant candidates, an all-junk set
    must go to review — recall never lowers the acceptance bar."""
    monkeypatch.setattr(discovery_open, "_recall_variants_enabled", lambda: True)
    _patch_search(monkeypatch, [
        _result("https://telefoonboek.nl/x", "telefoonboek.nl", "Velocity Bikes"),
        _result("https://marktplaats.nl/y", "marktplaats.nl", "Velocity Bikes Amsterdam"),
        _result("https://cylex.nl/z", "cylex.nl", "Velocity Bikes"),
    ])
    _patch_extract(monkeypatch)
    outcome = discovery_open.discover_for_company(
        session=object(), name="Velocity Bikes", city="Amsterdam", country="NL"
    )
    assert outcome.needs_review is True
    assert outcome.status == "needs_review"
    assert outcome.website == ""
