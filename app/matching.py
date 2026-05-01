from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Customer, MatchStatus, Prospect
from app.utils import build_name_geo_key, email_domain, normalize_domain, normalize_email, normalize_text, split_pipe_values


@dataclass
class MatchResult:
    status: MatchStatus
    method: str
    score: int
    reasons: list[str]
    customer: Customer | None


def apply_matching(session: Session, prospect: Prospect) -> MatchResult:
    result = match_prospect(session, prospect)
    prospect.match_status = result.status
    prospect.match_method = result.method
    prospect.match_score = result.score
    prospect.match_reasons = "\n".join(result.reasons)
    prospect.existing_customer_id = result.customer.id if result.customer else None
    prospect.approved_for_outreach = result.status == MatchStatus.new_prospect and prospect.review_status.value == "approved"
    prospect.last_matched_at = datetime.utcnow()
    return result


def match_prospect(session: Session, prospect: Prospect) -> MatchResult:
    website_domain = normalize_domain(prospect.website_domain or prospect.website)
    if website_domain:
      domain_match = session.scalar(
          select(Customer).where(
              (Customer.match_key_domain == website_domain)
              | (Customer.website_domain_candidate == website_domain)
              | (Customer.email_domain_primary == website_domain)
          )
      )
      if domain_match:
          return MatchResult(
              status=MatchStatus.existing_customer,
              method="exact_domain",
              score=100,
              reasons=[f"Exact website domain match: {website_domain}"],
              customer=domain_match,
          )

    exact_email = normalize_email(prospect.email)
    if exact_email:
        customers = session.scalars(select(Customer).where(Customer.customer_email_primary == exact_email)).all()
        if customers:
            return MatchResult(
                status=MatchStatus.existing_customer,
                method="exact_email",
                score=100,
                reasons=[f"Exact email match: {exact_email}"],
                customer=customers[0],
            )

        variant_customers = session.scalars(select(Customer).where(Customer.customer_email_variants != "")).all()
        for customer in variant_customers:
            if exact_email in {normalize_email(item) for item in split_pipe_values(customer.customer_email_variants)}:
                return MatchResult(
                    status=MatchStatus.existing_customer,
                    method="exact_email_variant",
                    score=98,
                    reasons=[f"Matched email variant: {exact_email}"],
                    customer=customer,
                )

    name_geo = build_name_geo_key(prospect.company_name, prospect.city, prospect.state, prospect.country_code)
    name_geo_match = session.scalar(select(Customer).where(Customer.canonical_name_geo_key == name_geo))
    if name_geo_match:
        return MatchResult(
            status=MatchStatus.existing_customer,
            method="canonical_name_geo",
            score=96,
            reasons=[f"Canonical company + city + country match: {name_geo}"],
            customer=name_geo_match,
        )

    candidates = session.scalars(select(Customer).where(Customer.country_code == (prospect.country_code or ""))).all()
    if not candidates:
        candidates = session.scalars(select(Customer)).all()

    best_customer = None
    best_score = 0
    reasons: list[str] = []
    clean_name = normalize_text(prospect.company_name)
    for customer in candidates:
        customer_name = customer.canonical_company_name_clean or normalize_text(customer.canonical_company_name)
        if not customer_name:
            continue
        score = int(fuzz.WRatio(clean_name, customer_name))
        if prospect.city and customer.city and normalize_text(prospect.city) == normalize_text(customer.city):
            score += 4
        if prospect.country_code and customer.country_code and normalize_text(prospect.country_code) == normalize_text(customer.country_code):
            score += 2
        if website_domain and customer.website_domain_candidate and website_domain == normalize_domain(customer.website_domain_candidate):
            score += 6
        if email_domain(prospect.email) and customer.email_domain_primary and email_domain(prospect.email) == normalize_domain(customer.email_domain_primary):
            score += 4
        if score > best_score:
            best_score = score
            best_customer = customer

    if best_customer and best_score >= 92:
        reasons = [f"High-confidence fuzzy company match ({best_score}) with {best_customer.canonical_company_name}."]
        return MatchResult(MatchStatus.existing_customer, "fuzzy_name_match", min(best_score, 99), reasons, best_customer)

    if best_customer and best_score >= 82:
        reasons = [f"Possible fuzzy match ({best_score}) with {best_customer.canonical_company_name}. Needs review."]
        return MatchResult(MatchStatus.possible_match, "fuzzy_name_match_review", best_score, reasons, best_customer)

    return MatchResult(
        status=MatchStatus.new_prospect,
        method="no_match",
        score=0,
        reasons=["No exact customer match was found. This looks like a net-new prospect."],
        customer=None,
    )
