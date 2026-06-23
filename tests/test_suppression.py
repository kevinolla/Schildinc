"""Unit tests for app.suppression.match_existing_customer.

Pure, offline tests: an in-memory SQLite database is seeded with a handful of
Customer rows, then each priority tier is asserted independently, plus a clean
non-match. No network, no real infra, no migrations — we just create the ORM
tables directly from the metadata.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base
from app.models import Customer
from app.suppression import SuppressionResult, match_existing_customer


@pytest.fixture()
def session() -> Session:
    """In-memory SQLite session with only the tables we touch created.

    We create the FULL metadata (cheap for SQLite) so any FK/relationship the
    Customer mapper references resolves, then yield a session and tear down.
    """
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, expire_on_commit=False)
    db = TestingSession()
    try:
        _seed(db)
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(engine)


def _make_customer(**overrides) -> Customer:
    """Build a Customer with sensible non-null defaults; override per test row.

    customer_entity_id is unique+indexed, so each row needs a distinct one.
    """
    base = dict(
        customer_entity_id=overrides.pop("customer_entity_id", "entity-default"),
        canonical_company_name="Default Co",
        canonical_company_name_clean="default co",
        customer_email_primary="",
        email_domain_primary="",
        website_domain_candidate="",
        match_key_domain="",
        customer_email_variants="",
        city="",
        country_code="",
    )
    base.update(overrides)
    return Customer(**base)


def _seed(db: Session) -> None:
    """Create one customer per match scenario."""
    db.add_all(
        [
            # Tier 1 — website domain (lives on website_domain_candidate).
            _make_customer(
                customer_entity_id="c-domain",
                canonical_company_name="Domain Bikes",
                canonical_company_name_clean="domain bikes",
                website_domain_candidate="domainbikes.nl",
            ),
            # Tier 2 — primary email.
            _make_customer(
                customer_entity_id="c-email",
                canonical_company_name="Email Bikes",
                canonical_company_name_clean="email bikes",
                customer_email_primary="hello@emailbikes.nl",
            ),
            # Tier 2 — email variant (pipe-separated).
            _make_customer(
                customer_entity_id="c-variant",
                canonical_company_name="Variant Bikes",
                canonical_company_name_clean="variant bikes",
                customer_email_primary="primary@variantbikes.nl",
                customer_email_variants="old@variantbikes.nl | sales@variantbikes.nl",
            ),
            # Tier 3 — canonical name + city + country.
            _make_customer(
                customer_entity_id="c-geo",
                canonical_company_name="Geo Bikes",
                canonical_company_name_clean="geo bikes",
                city="Amsterdam",
                country_code="NL",
            ),
            # Tier 5 — fuzzy: a near-but-not-exact name in a specific geo.
            _make_customer(
                customer_entity_id="c-fuzzy",
                canonical_company_name="Fuzzy Bike Shop B.V.",
                canonical_company_name_clean="fuzzy bike shop bv",
                city="Utrecht",
                country_code="NL",
            ),
            # Decoy: same fuzzy name but a DIFFERENT city — must not match when
            # the query is restricted to Utrecht.
            _make_customer(
                customer_entity_id="c-fuzzy-decoy",
                canonical_company_name="Fuzzy Bike Shop B.V.",
                canonical_company_name_clean="fuzzy bike shop bv",
                city="Berlin",
                country_code="DE",
            ),
        ]
    )
    db.commit()


# ---------------------------------------------------------------------------
# Tier 1 — website domain exact
# ---------------------------------------------------------------------------


def test_website_domain_exact(session: Session) -> None:
    result = match_existing_customer(session, website_domain="https://www.domainbikes.nl/contact")
    assert isinstance(result, SuppressionResult)
    assert result.already_customer is True
    assert result.match_confidence == "exact"
    assert result.best_match_reason == "website_domain_exact"
    assert result.matched_customer_id is not None


def test_website_domain_via_email_domain_column(session: Session) -> None:
    # email_domain_primary is one of the three domain columns checked.
    session.add(
        _make_customer(
            customer_entity_id="c-emaildomain",
            canonical_company_name="EmailDomain Co",
            canonical_company_name_clean="emaildomain co",
            email_domain_primary="emaildomainco.com",
        )
    )
    session.commit()
    result = match_existing_customer(session, website_domain="emaildomainco.com")
    assert result.already_customer is True
    assert result.best_match_reason == "website_domain_exact"


# ---------------------------------------------------------------------------
# Tier 2 — email exact (primary + variant)
# ---------------------------------------------------------------------------


def test_email_primary_exact_case_insensitive(session: Session) -> None:
    result = match_existing_customer(session, email="HELLO@EmailBikes.NL")
    assert result.already_customer is True
    assert result.match_confidence == "exact"
    assert result.best_match_reason == "email_exact"


def test_email_variant_exact(session: Session) -> None:
    result = match_existing_customer(session, email="sales@variantbikes.nl")
    assert result.already_customer is True
    assert result.match_confidence == "exact"
    assert result.best_match_reason == "email_variant_exact"


# ---------------------------------------------------------------------------
# Tier 3 — canonical name + city + country exact
# ---------------------------------------------------------------------------


def test_name_city_country_exact(session: Session) -> None:
    result = match_existing_customer(
        session,
        company_name="Geo Bikes",
        city="amsterdam",  # case-insensitive
        country="nl",
    )
    assert result.already_customer is True
    assert result.match_confidence == "high"
    assert result.best_match_reason == "name_city_country_exact"


def test_name_without_full_geo_does_not_exact_match(session: Session) -> None:
    # Name present but no city -> tier 3 must NOT fire as exact. Geo Bikes has a
    # distinct enough name that it won't fuzzy onto another row either.
    result = match_existing_customer(session, company_name="Geo Bikes", country="NL")
    assert result.best_match_reason != "name_city_country_exact"


# ---------------------------------------------------------------------------
# Tier 4 — KVK number (no-op today: Customer has no kvk column)
# ---------------------------------------------------------------------------


def test_kvk_tier_is_graceful_noop(session: Session) -> None:
    # Customer model has no kvk column yet, so a kvk-only lookup must fall
    # through to a clean miss rather than raising.
    result = match_existing_customer(session, kvk_number="12345678")
    assert result.already_customer is False
    assert result.match_confidence == "none"


# ---------------------------------------------------------------------------
# Tier 5 — fuzzy, restricted to same city/country
# ---------------------------------------------------------------------------


def test_fuzzy_name_same_geo(session: Session) -> None:
    # Slightly different spelling, same city+country as the Utrecht customer.
    result = match_existing_customer(
        session,
        company_name="Fuzzy Bikeshop",
        city="Utrecht",
        country="NL",
    )
    assert result.already_customer is True
    assert result.match_confidence in {"high", "medium"}
    assert result.best_match_reason.startswith("fuzzy_name_same_geo")
    assert result.matched_customer_id is not None


def test_fuzzy_requires_geo_bound(session: Session) -> None:
    # Same fuzzy name but NO country -> the precision guard skips fuzzy entirely.
    result = match_existing_customer(session, company_name="Fuzzy Bikeshop")
    assert result.already_customer is False
    assert result.match_confidence == "none"


def test_fuzzy_respects_city_restriction(session: Session) -> None:
    # An obviously different company in Amsterdam should NOT fuzzy-match the
    # Utrecht/Berlin "Fuzzy Bike Shop" rows because of the city restriction.
    result = match_existing_customer(
        session,
        company_name="Completely Unrelated Widgets",
        city="Amsterdam",
        country="NL",
    )
    assert result.already_customer is False
    assert result.match_confidence == "none"


# ---------------------------------------------------------------------------
# Clean non-match + empty inputs
# ---------------------------------------------------------------------------


def test_non_match_returns_false(session: Session) -> None:
    result = match_existing_customer(
        session,
        website_domain="nobody-here.example",
        email="stranger@nowhere.example",
        company_name="Nonexistent Trading Ltd",
        city="Paris",
        country="FR",
    )
    assert result.already_customer is False
    assert result.matched_customer_id is None
    assert result.match_confidence == "none"
    assert result.best_match_reason == "no_match"


def test_all_blank_inputs_return_none(session: Session) -> None:
    result = match_existing_customer(session)
    assert result.already_customer is False
    assert result.match_confidence == "none"


# ---------------------------------------------------------------------------
# Priority ordering — website domain beats everything else
# ---------------------------------------------------------------------------


def test_priority_domain_beats_name(session: Session) -> None:
    # Provide a domain that hits c-domain AND a name/geo that would hit c-geo;
    # domain (tier 1) must win.
    result = match_existing_customer(
        session,
        website_domain="domainbikes.nl",
        company_name="Geo Bikes",
        city="Amsterdam",
        country="NL",
    )
    assert result.best_match_reason == "website_domain_exact"
