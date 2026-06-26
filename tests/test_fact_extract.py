"""Phase 2 fact-extraction tests — concrete matches only, no invented facts."""
from __future__ import annotations

from app.fact_extract import detect_facts_from_text


def _by_field(facts):
    return {f["field_name"]: f for f in facts}


def test_detects_premium_brand_high_confidence():
    text = "Bij ons vindt u Gazelle en Batavus elektrische fietsen."
    facts = _by_field(detect_facts_from_text(text, source_url="https://shop.nl"))
    assert "premium_brand_signal" in facts
    assert facts["premium_brand_signal"]["confidence"] >= 80   # auto-trustable
    assert "gazelle" in facts["premium_brand_signal"]["extracted_value"].lower()
    assert "batavus" in facts["premium_brand_signal"]["extracted_value"].lower()


def test_detects_soft_signals_as_low_confidence():
    text = "Onze werkplaats verzorgt onderhoud en reparatie. Ook tweedehands fietsen."
    facts = _by_field(detect_facts_from_text(text, source_url="https://shop.nl"))
    assert "workshop_focus" in facts and facts["workshop_focus"]["confidence"] < 80
    assert "second_hand_signal" in facts and facts["second_hand_signal"]["confidence"] < 80


def test_no_invented_facts_for_empty_text():
    assert detect_facts_from_text("", source_url="https://x.nl") == []
    assert detect_facts_from_text("   ", source_url="https://x.nl") == []


def test_neutral_text_yields_only_description_not_signals():
    text = "Welkom op onze website. Wij zijn al jaren actief in de regio en helpen u graag verder."
    facts = _by_field(detect_facts_from_text(text, source_url="https://x.nl"))
    assert "premium_brand_signal" not in facts
    assert "second_hand_signal" not in facts
    assert "business_description" in facts  # their own prose is a legit fact


def test_brand_word_boundary_no_false_positive():
    # "focus"/"giant" are NOT in the distinctive list; a sentence using common
    # words must not yield a premium-brand fact.
    text = "We focus on giving giant value to every customer."
    facts = _by_field(detect_facts_from_text(text, source_url="https://x.nl"))
    assert "premium_brand_signal" not in facts


def test_every_fact_carries_provenance():
    text = "Sinds 1923 verkopen wij Gazelle fietsen met eigen werkplaats."
    facts = detect_facts_from_text(text, source_url="https://shop.nl/over-ons")
    assert facts
    for f in facts:
        assert f["source_url"] == "https://shop.nl/over-ons"
        assert f["extraction_method"] == "web_extract"
        assert isinstance(f["confidence"], int)
    fields = {f["field_name"] for f in facts}
    assert "public_store_fact" in fields  # "Sinds 1923"
