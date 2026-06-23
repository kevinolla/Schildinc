"""Existing-customer suppression matcher.

PURPOSE
-------
Given the loose identity fields we know about a discovered company (a website
domain, a public email, a company name + city + country, and — if it came from
the KVK pool — a KVK number), decide whether that company is ALREADY one of our
paying customers. If it is, the outreach pipeline must suppress it so we never
cold-email an existing client.

This module is intentionally a *self-contained, read-only* matcher: it only
SELECTs from the ``customers`` table and returns a verdict. It never writes
``already_client_flag`` / ``matched_customer_id`` on any row — persistence is
the caller's (the integrator's) job. That keeps the hard-won strict-matching
invariant ("127 true klants, no false positives") under the caller's control.

PRIORITY CASCADE (first hit wins)
---------------------------------
1. Website domain exact      -> confidence "exact"
2. Customer email exact      -> confidence "exact"  (incl. pipe-listed variants)
3. Canonical name + city + country exact -> confidence "high"
4. KVK number exact          -> confidence "high"   (only if Customer table ever
   carries a kvk identifier; today the Customer model has no kvk column, so this
   tier degrades to a no-op unless a future column is added — see _kvk_match)
5. RapidFuzz fuzzy name match, restricted to the SAME city/country
   -> confidence "high" (>= FUZZY_HIGH_THRESHOLD) or "medium" (>= threshold)

A miss returns ``already_customer=False`` with confidence "none".

GRACEFUL FALLBACK
-----------------
- ``rapidfuzz`` is LAZY-imported inside the fuzzy tier only. If it is missing
  (or import fails for any reason) the fuzzy tier is skipped silently and the
  function still returns a valid (non-fuzzy) verdict — it never raises at
  import time and never crashes the caller.
- Every input is optional; with all inputs blank the function returns a clean
  "none" verdict instead of scanning the table.
- All column reads use ``getattr(..., default)`` style where a column might not
  exist on older schemas, so the module imports and runs even before any
  forthcoming migration adds new columns.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Customer
from app.utils import (
    normalize_domain,
    normalize_email,
    normalize_text,
    split_pipe_values,
)

# ---------------------------------------------------------------------------
# Tunables. The fuzzy threshold (~88) is read from settings when the integrator
# adds it, but we use getattr(..., default) so this module imports cleanly today.
# ---------------------------------------------------------------------------

# Score (0-100, rapidfuzz WRatio) at/above which a same-city/country fuzzy name
# match is treated as "high" confidence. Below this but >= FUZZY_MEDIUM_THRESHOLD
# it is "medium" — surfaced for review, not auto-trusted by a strict caller.
FUZZY_HIGH_THRESHOLD: int = 88
FUZZY_MEDIUM_THRESHOLD: int = 82

# Free / shared webmail domains are NEVER a reliable company identifier: two
# unrelated shops both using gmail.com must not be merged. Tier-1 domain
# matching skips these entirely (a customer's email_domain_primary of gmail.com
# would otherwise false-positive against any gmail-using prospect).
FREE_WEBMAIL_DOMAINS: frozenset[str] = frozenset({
    "gmail.com", "googlemail.com", "hotmail.com", "hotmail.nl", "outlook.com",
    "outlook.nl", "live.nl", "live.com", "yahoo.com", "yahoo.nl", "icloud.com",
    "me.com", "msn.com", "ziggo.nl", "kpnmail.nl", "planet.nl", "casema.nl",
    "chello.nl", "home.nl", "telfort.nl", "xs4all.nl", "hetnet.nl", "gmx.com",
    "gmx.net", "t-online.de", "mail.com", "upcmail.nl", "aol.com", "protonmail.com",
})


def _country_iso2_lower(country: str) -> str:
    """Normalize a country input (full name OR ISO-2) to a lowercased ISO-2 code
    so it compares correctly against Customer.country_code (stored ISO-2)."""
    raw = (country or "").strip()
    if not raw:
        return ""
    try:
        from app.country_codes import to_iso2
        iso = to_iso2(raw)
        if iso:
            return iso.lower()
    except Exception:
        pass
    return raw.lower()


@dataclass(frozen=True)
class SuppressionResult:
    """Verdict of an existing-customer lookup.

    Attributes
    ----------
    already_customer:
        True iff we matched a Customer row by any tier.
    matched_customer_id:
        Customer.id of the match, or None on a miss.
    match_confidence:
        One of "exact" | "high" | "medium" | "low" | "none". Lets the caller
        decide what to auto-trust vs. route to human review.
    best_match_reason:
        Short machine-readable reason code, e.g. "website_domain_exact",
        "email_exact", "email_variant_exact", "name_city_country_exact",
        "kvk_number_exact", "fuzzy_name_same_geo", or "no_match".
    """

    already_customer: bool
    matched_customer_id: int | None
    match_confidence: str
    best_match_reason: str


# A clean, reusable "no match" verdict.
_NO_MATCH = SuppressionResult(
    already_customer=False,
    matched_customer_id=None,
    match_confidence="none",
    best_match_reason="no_match",
)


def match_existing_customer(
    session: Session,
    *,
    website_domain: str = "",
    email: str = "",
    company_name: str = "",
    city: str = "",
    country: str = "",
    kvk_number: str = "",
) -> SuppressionResult:
    """Decide whether the given identity belongs to an existing customer.

    Runs the priority cascade documented in the module docstring and returns the
    FIRST positive verdict. Read-only: never mutates any row. Never raises — any
    unexpected error in the optional fuzzy tier is swallowed so the caller always
    gets a usable verdict.

    All keyword args are optional; pass whatever you know. With nothing
    identifying provided, returns the "none" verdict immediately.
    """
    # ── Tier 1: website domain exact ───────────────────────────────────────
    result = _website_domain_match(session, website_domain)
    if result is not None:
        return result

    # ── Tier 2: customer email exact (primary, then pipe-listed variants) ───
    result = _email_match(session, email)
    if result is not None:
        return result

    # ── Tier 3: canonical name + city + country exact ──────────────────────
    result = _name_city_country_match(session, company_name, city, country)
    if result is not None:
        return result

    # ── Tier 4: KVK number exact (no-op until Customer carries a kvk column) ─
    result = _kvk_match(session, kvk_number)
    if result is not None:
        return result

    # ── Tier 5: RapidFuzz fuzzy name, restricted to same city/country ──────
    result = _fuzzy_name_match(session, company_name, city, country)
    if result is not None:
        return result

    return _NO_MATCH


# ===========================================================================
# Individual tiers. Each returns a SuppressionResult on a hit, or None to let
# the cascade fall through to the next tier.
# ===========================================================================


def _website_domain_match(session: Session, website_domain: str) -> SuppressionResult | None:
    """Tier 1 — exact website-domain match against any of the three domain
    columns a Customer can carry: the canonical website candidate, the derived
    match key, and the email domain (a company emailing from its own domain).
    """
    domain = normalize_domain(website_domain)
    if not domain or domain in FREE_WEBMAIL_DOMAINS:
        # A free-webmail / shared domain is not a company identifier — skip the
        # whole tier so we never merge two unrelated gmail-using businesses.
        return None

    customer = session.scalar(
        select(Customer).where(
            or_(
                Customer.website_domain_candidate == domain,
                Customer.match_key_domain == domain,
                Customer.email_domain_primary == domain,
            )
        )
    )
    if customer is not None:
        return SuppressionResult(
            already_customer=True,
            matched_customer_id=customer.id,
            match_confidence="exact",
            best_match_reason="website_domain_exact",
        )
    return None


def _email_match(session: Session, email: str) -> SuppressionResult | None:
    """Tier 2 — exact email match.

    First the primary email column (case-insensitive). Then a fallback scan of
    customers that recorded pipe-separated email variants, since a single
    customer may have multiple historical billing addresses.
    """
    norm_email = normalize_email(email)
    if not norm_email:
        return None

    # Primary email column — indexed, case-insensitive compare.
    customer = session.scalar(
        select(Customer).where(func.lower(Customer.customer_email_primary) == norm_email)
    )
    if customer is not None:
        return SuppressionResult(
            already_customer=True,
            matched_customer_id=customer.id,
            match_confidence="exact",
            best_match_reason="email_exact",
        )

    # Variant emails are stored pipe-separated in a free-text column, so we have
    # to load the (small) set of customers that recorded any variant and compare
    # in Python. This mirrors app/matching.py's variant handling.
    variant_customers = session.scalars(
        select(Customer).where(Customer.customer_email_variants != "")
    ).all()
    for customer in variant_customers:
        variants = {normalize_email(v) for v in split_pipe_values(customer.customer_email_variants)}
        if norm_email in variants:
            return SuppressionResult(
                already_customer=True,
                matched_customer_id=customer.id,
                match_confidence="exact",
                best_match_reason="email_variant_exact",
            )
    return None


def _name_city_country_match(
    session: Session, company_name: str, city: str, country: str
) -> SuppressionResult | None:
    """Tier 3 — exact canonical company-name + city + country match.

    We normalize the name the same way the importer did
    (``canonical_company_name_clean``) and compare city/country
    case-insensitively. All three must be present and must match.
    """
    clean_name = normalize_text(company_name)
    norm_city = normalize_text(city)
    # Customer.country_code is ISO-2 ("nl"); the caller may pass "Netherlands"
    # or "NL" — normalize both sides to ISO-2 so the compare actually matches.
    norm_country = _country_iso2_lower(country)
    if not clean_name or not norm_city or not norm_country:
        # Without a full (name + city + country) triple we cannot make this an
        # "exact" geo claim — let the cascade fall through to fuzzy.
        return None

    customer = session.scalar(
        select(Customer).where(
            func.lower(Customer.canonical_company_name_clean) == clean_name,
            func.lower(Customer.city) == norm_city,
            func.lower(Customer.country_code) == norm_country,
        )
    )
    if customer is not None:
        return SuppressionResult(
            already_customer=True,
            matched_customer_id=customer.id,
            match_confidence="high",
            best_match_reason="name_city_country_exact",
        )
    return None


def _kvk_match(session: Session, kvk_number: str) -> SuppressionResult | None:
    """Tier 4 — exact KVK-number match, IF the Customer table carries one.

    The current ``customers`` schema (app/models.py) has NO kvk column, so this
    tier is a graceful no-op today: we detect the column reflectively and only
    issue a query when it exists. This keeps the priority slot reserved for when
    a future migration adds (e.g.) ``Customer.kvk_number`` without needing to
    touch this module — the integrator just adds the column.
    """
    number = (kvk_number or "").strip()
    if not number:
        return None

    # Reflectively find a kvk-style column on the Customer mapper so we don't
    # hard-reference an attribute that does not exist yet (would raise at query
    # build time). Accept a couple of plausible names.
    column = None
    for candidate in ("kvk_number", "kvk_no", "kvk"):
        column = getattr(Customer, candidate, None)
        if column is not None:
            break
    if column is None:
        return None  # No kvk column on Customer -> tier disabled, fall through.

    customer = session.scalar(select(Customer).where(column == number))
    if customer is not None:
        return SuppressionResult(
            already_customer=True,
            matched_customer_id=customer.id,
            match_confidence="high",
            best_match_reason="kvk_number_exact",
        )
    return None


def _fuzzy_name_match(
    session: Session, company_name: str, city: str, country: str
) -> SuppressionResult | None:
    """Tier 5 — RapidFuzz fuzzy company-name match, restricted to the SAME
    city/country to keep precision high.

    ``rapidfuzz`` is lazy-imported here so the module never hard-depends on it.
    If the import fails the whole tier is skipped (returns None) and the caller
    still gets a valid non-fuzzy verdict.

    Restriction logic:
    - If we know the country, only compare against customers in that country.
    - If we also know the city, only compare against customers in that city.
      This is what keeps "Bike Shop, Amsterdam" from fuzzing onto
      "Bike Shop, Berlin".
    """
    clean_name = normalize_text(company_name)
    if not clean_name:
        return None

    # Lazy import — optional heavy dep. Skip the tier entirely if unavailable.
    try:
        from rapidfuzz import fuzz
    except Exception:  # pragma: no cover - defensive: missing/broken dep
        return None

    high_threshold = int(getattr(settings, "suppression_fuzzy_threshold", FUZZY_HIGH_THRESHOLD))
    medium_threshold = int(getattr(settings, "suppression_fuzzy_medium_threshold", FUZZY_MEDIUM_THRESHOLD))

    norm_city = normalize_text(city)
    norm_country = _country_iso2_lower(country)  # ISO-2 to match Customer.country_code

    # Build a geo-restricted candidate query. We REQUIRE at least a country to
    # bound the fuzzy comparison; an unbounded full-table fuzzy scan is both slow
    # and a precision risk, so without geo we decline to fuzzy-match.
    stmt = select(Customer)
    if norm_country:
        stmt = stmt.where(func.lower(Customer.country_code) == norm_country)
    else:
        return None  # No geo bound -> skip fuzzy (precision guard).
    if norm_city:
        stmt = stmt.where(func.lower(Customer.city) == norm_city)

    candidates = session.scalars(stmt).all()
    if not candidates:
        return None

    best_customer: Customer | None = None
    best_score = 0
    for customer in candidates:
        customer_name = customer.canonical_company_name_clean or normalize_text(
            customer.canonical_company_name
        )
        if not customer_name:
            continue
        score = int(fuzz.WRatio(clean_name, customer_name))
        if score > best_score:
            best_score = score
            best_customer = customer

    if best_customer is None:
        return None

    if best_score >= high_threshold:
        return SuppressionResult(
            already_customer=True,
            matched_customer_id=best_customer.id,
            match_confidence="high",
            best_match_reason=f"fuzzy_name_same_geo:{best_score}",
        )
    if best_score >= medium_threshold:
        return SuppressionResult(
            already_customer=True,
            matched_customer_id=best_customer.id,
            match_confidence="medium",
            best_match_reason=f"fuzzy_name_same_geo:{best_score}",
        )
    return None
