"""
Unit tests for app/web_extract.py — PURE functions only.

No network, no DB, no Playwright. Everything here exercises the deterministic
parsing / ranking / normalization helpers so the suite runs offline and fast.
The fetch/orchestration paths (discover_contacts, fetch_html) are intentionally
NOT exercised against the network here; discover_contacts is covered with a
monkeypatched fetch to assert the pure wiring (provenance, status) only.
"""
from __future__ import annotations

import pytest

from app import web_extract as w


# ──────────────────────────────────────────────────────────────────────────
# extract_emails
# ──────────────────────────────────────────────────────────────────────────
def test_extract_emails_plain_and_mailto():
    html = (
        'Reach us: <a href="mailto:info@bikecity.nl?subject=Hi">Email</a> '
        "or sales@bikecity.nl for orders."
    )
    emails = w.extract_emails(html)
    assert "info@bikecity.nl" in emails
    assert "sales@bikecity.nl" in emails


def test_extract_emails_deobfuscated_at_dot():
    # "info [at] x [dot] nl" and "(at)/(dot)" and "at/dot" word forms
    assert "info@bikecity.nl" in w.extract_emails("info [at] bikecity [dot] nl")
    assert "sales@shop.de" in w.extract_emails("sales (at) shop (dot) de")
    assert "hello@store.fr" in w.extract_emails("hello at store dot fr")


def test_extract_emails_html_entity_at():
    assert "info@bikecity.nl" in w.extract_emails("info&#64;bikecity&#46;nl") or \
        "info@bikecity.nl" in w.extract_emails("info&#64;bikecity [dot] nl")


def test_extract_emails_dedupes_and_lowercases():
    emails = w.extract_emails("INFO@BikeCity.NL info@bikecity.nl")
    assert emails.count("info@bikecity.nl") == 1


def test_extract_emails_empty():
    assert w.extract_emails("") == []
    assert w.extract_emails(None) == []  # type: ignore[arg-type]


# ──────────────────────────────────────────────────────────────────────────
# rank_email — junk rejection
# ──────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "junk",
    [
        "noreply@bikecity.nl",
        "no-reply@bikecity.nl",
        "donotreply@bikecity.nl",
        "system@bikecity.nl",
        "postmaster@bikecity.nl",
        "mailer-daemon@bikecity.nl",
        "logo@2x.png",            # image filename masquerading as email
        "icon@sprite.svg",
        "info@shopify.com",        # vendor SaaS domain
        "noreply@klaviyo.com",
        "test@example.com",        # example/test domain
    ],
)
def test_rank_email_rejects_junk(junk):
    assert junk not in w.rank_email([junk, "info@bikecity.nl"])


def test_rank_email_rejects_only_junk_returns_empty():
    assert w.rank_email(["noreply@x.com", "logo@2x.png"]) == []


# ──────────────────────────────────────────────────────────────────────────
# rank_email — ordering
# ──────────────────────────────────────────────────────────────────────────
def test_rank_email_prefix_priority_order():
    emails = [
        "service@bikecity.nl",
        "hello@bikecity.nl",
        "contact@bikecity.nl",
        "sales@bikecity.nl",
        "info@bikecity.nl",
    ]
    ranked = w.rank_email(emails)
    assert ranked == [
        "info@bikecity.nl",
        "sales@bikecity.nl",
        "contact@bikecity.nl",
        "hello@bikecity.nl",
        "service@bikecity.nl",
    ]


def test_rank_email_generic_beats_personal():
    ranked = w.rank_email(["jan.devries@bikecity.nl", "info@bikecity.nl"])
    assert ranked[0] == "info@bikecity.nl"
    assert ranked[1] == "jan.devries@bikecity.nl"


def test_rank_email_other_generic_beats_personal():
    ranked = w.rank_email(["jan@bikecity.nl", "support@bikecity.nl"])
    assert ranked[0] == "support@bikecity.nl"


def test_rank_email_company_domain_beats_free_webmail():
    # both are personal mailboxes; the one on the company's own domain wins
    ranked = w.rank_email(["owner@gmail.com", "owner@bikecity.nl"])
    assert ranked[0] == "owner@bikecity.nl"


def test_rank_email_company_token_tiebreaker():
    # generic prefix on both → domain whose token matches company name wins
    ranked = w.rank_email(
        ["info@randomhost.com", "info@bikecity.nl"], company_name="Bike City"
    )
    assert ranked[0] == "info@bikecity.nl"


# ──────────────────────────────────────────────────────────────────────────
# email_confidence
# ──────────────────────────────────────────────────────────────────────────
def test_email_confidence_on_site_domain_is_high():
    high = w.email_confidence("info@bikecity.nl", website_domain="bikecity.nl")
    low = w.email_confidence("owner@gmail.com", website_domain="bikecity.nl")
    assert high > low
    assert 0 <= low <= 100 and 0 <= high <= 100


def test_email_confidence_junk_is_zero():
    assert w.email_confidence("noreply@bikecity.nl") == 0
    assert w.email_confidence("logo@2x.png") == 0


# ──────────────────────────────────────────────────────────────────────────
# normalize_nl_phone
# ──────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("020-1234567", "+31201234567"),       # NL national → +31
        ("020 123 4567", "+31201234567"),
        ("0031 20 1234567", "+31201234567"),    # 00 international prefix → +
        ("+31 20 123 4567", "+31201234567"),    # already international
        ("06-12345678", "+31612345678"),        # NL mobile
        ("", ""),                                  # empty
        ("12345", ""),                            # too short → rejected
        ("abc", ""),                              # no digits
    ],
)
def test_normalize_nl_phone(raw, expected):
    assert w.normalize_nl_phone(raw) == expected


def test_normalize_nl_phone_keeps_foreign_country_code():
    # A clearly-international number keeps its own country code, not forced to +31
    assert w.normalize_nl_phone("+49 30 12345678") == "+493012345678"


# ──────────────────────────────────────────────────────────────────────────
# extract_phones
# ──────────────────────────────────────────────────────────────────────────
def test_extract_phones_finds_and_normalizes():
    text = "Tel: 020-1234567 — also reach +31 20 765 4321."
    phones = w.extract_phones(text, country="NL")
    assert "+31201234567" in phones
    assert "+31207654321" in phones


def test_extract_phones_dedupes():
    text = "Call 020-1234567 or 020 123 4567"  # same number two ways
    phones = w.extract_phones(text, country="NL")
    assert phones == ["+31201234567"]


def test_extract_phones_ignores_short_runs():
    assert w.extract_phones("Order #12345 placed", country="NL") == []


# ──────────────────────────────────────────────────────────────────────────
# rank_phones — context-aware (contact/header/footer up, fax down)
# ──────────────────────────────────────────────────────────────────────────
def test_rank_phones_prefers_contact_context_over_fax():
    phones = ["+31201111111", "+31202222222"]
    ctx = {
        "+31201111111": "send your fax to this number",
        "+31202222222": "contact us by phone at",
    }
    ranked = w.rank_phones(phones, ctx)
    assert ranked[0] == "+31202222222"   # contact-context wins
    assert ranked[-1] == "+31201111111"  # fax sinks


def test_rank_phones_stable_without_context():
    phones = ["+31201111111", "+31202222222"]
    assert w.rank_phones(phones) == phones


def test_rank_phones_dedupes():
    assert w.rank_phones(["+31201111111", "+31201111111"]) == ["+31201111111"]


# ──────────────────────────────────────────────────────────────────────────
# contact_page_links
# ──────────────────────────────────────────────────────────────────────────
def test_contact_page_links_keeps_same_domain_and_orders_contact_first():
    html = (
        '<a href="/about-us">About</a>'
        '<a href="/contact">Contact</a>'
        '<a href="https://elsewhere.com/contact">External</a>'
        '<a href="/assets/app.js">script</a>'
        '<a href="/impressum">Impressum</a>'
    )
    links = w.contact_page_links(html, "https://bikecity.nl")
    # contact ranks before about before impressum; external + asset dropped
    assert links[0] == "https://bikecity.nl/contact"
    assert "https://bikecity.nl/about-us" in links
    assert "https://bikecity.nl/impressum" in links
    assert all("elsewhere.com" not in link for link in links)
    assert all(not link.endswith(".js") for link in links)


def test_contact_page_links_dedupes_and_strips_fragment():
    html = '<a href="/contact#form">a</a><a href="/contact">b</a>'
    links = w.contact_page_links(html, "https://bikecity.nl")
    assert links == ["https://bikecity.nl/contact"]


def test_contact_page_links_ignores_non_contact_pages():
    html = '<a href="/products">Products</a><a href="/cart">Cart</a>'
    assert w.contact_page_links(html, "https://bikecity.nl") == []


# ──────────────────────────────────────────────────────────────────────────
# main_text — fallback tag-strip (works even without trafilatura)
# ──────────────────────────────────────────────────────────────────────────
def test_main_text_strips_scripts_styles_and_tags():
    html = (
        "<html><head><style>.x{color:red}</style></head>"
        "<body><script>var x=1;</script>"
        "<h1>Bike City</h1><p>We sell &amp; repair bikes.</p></body></html>"
    )
    text = w.main_text(html)
    assert "Bike City" in text
    assert "We sell & repair bikes." in text
    assert "var x" not in text
    assert "color:red" not in text


def test_main_text_empty():
    assert w.main_text("") == ""
    assert w.main_text(None) == ""  # type: ignore[arg-type]


# ──────────────────────────────────────────────────────────────────────────
# URL helpers
# ──────────────────────────────────────────────────────────────────────────
def test_ensure_http_url():
    assert w.ensure_http_url("bikecity.nl") == "https://bikecity.nl"
    assert w.ensure_http_url("http://bikecity.nl") == "http://bikecity.nl"
    assert w.ensure_http_url("") == ""


def test_domain_of_strips_www_and_port():
    assert w._domain_of("https://www.bikecity.nl:8443/contact") == "bikecity.nl"


# ──────────────────────────────────────────────────────────────────────────
# discover_contacts — wiring with monkeypatched fetch (no real network)
# ──────────────────────────────────────────────────────────────────────────
def test_discover_contacts_no_url():
    res = w.discover_contacts("")
    assert res.status == w.STATUS_NO_WEBSITE
    assert res.email_public == ""


def test_discover_contacts_wires_email_and_provenance(monkeypatch):
    home = (
        '<html><body><h1>Bike City</h1>'
        '<a href="/contact">Contact</a></body></html>'
    )
    contact = (
        '<html><body>Reach us at info@bikecity.nl. '
        "Tel: 020-1234567 (contact).</body></html>"
    )

    def fake_fetch(url: str) -> str:
        if url.rstrip("/").endswith("/contact"):
            return contact
        return home

    # Avoid the MX guesser doing DNS work in any branch.
    monkeypatch.setattr(w, "fetch_html", fake_fetch)
    monkeypatch.setattr(w, "_apply_email_guess_fallback", lambda *a, **k: None)

    res = w.discover_contacts("https://bikecity.nl", company_name="Bike City")
    assert res.status == w.STATUS_FOUND
    assert res.email_public == "info@bikecity.nl"
    assert res.email_source_page.rstrip("/").endswith("/contact")  # provenance
    assert res.website_confidence == 100
    assert res.phone_public == "+31201234567"
    assert "https://bikecity.nl/contact" in res.pages_scanned
    assert res.email_confidence > 0


def test_discover_contacts_unreachable_site(monkeypatch):
    monkeypatch.setattr(w, "fetch_html", lambda url: "")
    # No email guesser side-effects in this test.
    monkeypatch.setattr(w, "_apply_email_guess_fallback", lambda *a, **k: None)
    res = w.discover_contacts("https://dead.example")
    assert res.website_confidence == 0
    assert res.status == w.STATUS_NO_CONTACTS
    assert res.email_public == ""


def test_discover_contacts_falls_back_to_guess(monkeypatch):
    # Page reachable but contains no email → guesser supplies info@domain.
    monkeypatch.setattr(w, "fetch_html", lambda url: "<html><body>No email here</body></html>")

    def fake_fallback(result, domain, company_name):
        result.email_public = f"info@{domain}"
        result.email_source_page = "pattern:info@"
        result.email_confidence = 70
        result.emails_found = [f"info@{domain}"]

    monkeypatch.setattr(w, "_apply_email_guess_fallback", fake_fallback)
    res = w.discover_contacts("https://bikecity.nl")
    assert res.email_public == "info@bikecity.nl"
    assert res.status == w.STATUS_FOUND
    assert res.email_confidence == 70
