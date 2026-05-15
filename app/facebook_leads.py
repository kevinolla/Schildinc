"""
Facebook Lead Ads Importer
==========================
Pulls leads from the team's Google Sheet (CSV export endpoint is public,
no OAuth needed) and upserts them into `facebook_leads`. After import
each row is cross-referenced against the existing `customers` and
`kvk_companies` tables so we can tell apart:

  - 'existing_customer'  → email or company name already a paying customer
  - 'known_prospect'     → already enriched in the KVK pipeline
  - 'new'                → genuinely fresh lead — gets fed into prospects

Run via the /admin/facebook-leads/import endpoint or directly from a
shell with `python -m app.facebook_leads`.
"""
from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from typing import Any
from urllib.request import Request, urlopen

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Customer, FacebookLead, KvkCompany


# Public CSV-export URL of the team's spreadsheet (gid is the worksheet
# tab id from the share URL). Change here if the spreadsheet ever moves.
GOOGLE_SHEET_ID = "10k2UB3qefKvskF1YemikhVCPk0JI8xmScH2dj_I7h5g"
GOOGLE_SHEET_GID = "1219149797"
SHEET_CSV_URL = (
    f"https://docs.google.com/spreadsheets/d/{GOOGLE_SHEET_ID}"
    f"/export?format=csv&gid={GOOGLE_SHEET_GID}"
)


def _fetch_sheet_csv() -> str:
    req = Request(SHEET_CSV_URL, headers={"User-Agent": "schild-fb-importer/1.0"})
    with urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _parse_created_time(raw: str) -> datetime | None:
    """Facebook timestamps look like '2025-12-02T19:09:42+07:00'."""
    if not raw:
        return None
    try:
        # Python <3.11 needs the colon-in-offset normalized
        clean = raw.strip()
        if len(clean) >= 6 and clean[-3] == ":":
            clean = clean[:-3] + clean[-2:]
        return datetime.strptime(clean, "%Y-%m-%dT%H:%M:%S%z").astimezone(timezone.utc)
    except Exception:
        return None


def _classify_lead(db: Session, lead: FacebookLead) -> str:
    """
    Cross-reference this lead against customers + KVK by email and by
    company name. Returns 'existing_customer' / 'known_prospect' / 'new'.
    Also sets matched_customer_id / matched_kvk_company_id when found.
    """
    email = (lead.email or "").strip().lower()
    company = (lead.company_name or "").strip().lower()

    customer: Customer | None = None
    if email:
        customer = db.scalars(
            select(Customer).where(func.lower(Customer.customer_email_primary) == email).limit(1)
        ).first()
    if not customer and company:
        customer = db.scalars(
            select(Customer).where(func.lower(Customer.canonical_company_name) == company).limit(1)
        ).first()

    kvk: KvkCompany | None = None
    if email:
        kvk = db.scalars(
            select(KvkCompany).where(func.lower(KvkCompany.email_public) == email).limit(1)
        ).first()
    if not kvk and company:
        kvk = db.scalars(
            select(KvkCompany).where(func.lower(KvkCompany.company_name) == company).limit(1)
        ).first()

    lead.matched_customer_id = customer.id if customer else None
    lead.matched_kvk_company_id = kvk.id if kvk else None

    if customer:
        return "existing_customer"
    if kvk:
        return "known_prospect"
    return "new"


def import_facebook_leads(db: Session) -> dict[str, int]:
    """
    Fetch the Google Sheet, upsert every row by fb_lead_id, classify
    each. Returns a count summary the route hands back to the UI.
    """
    csv_text = _fetch_sheet_csv()
    reader = csv.DictReader(io.StringIO(csv_text))

    inserted = 0
    updated = 0
    skipped = 0
    matched_customer = 0
    matched_kvk = 0
    new_leads = 0

    for row in reader:
        fb_id = (row.get("id") or "").strip()
        if not fb_id:
            skipped += 1
            continue

        existing = db.scalars(
            select(FacebookLead).where(FacebookLead.fb_lead_id == fb_id).limit(1)
        ).first()
        is_new = existing is None
        lead = existing or FacebookLead(fb_lead_id=fb_id)

        lead.created_time_utc = _parse_created_time(row.get("created_time", ""))
        lead.ad_name = (row.get("ad_name") or "").strip()
        lead.adset_name = (row.get("adset_name") or "").strip()
        lead.campaign_name = (row.get("campaign_name") or "").strip()
        lead.form_name = (row.get("form_name") or "").strip()
        lead.platform = (row.get("platform") or "").strip()
        lead.is_organic = (row.get("is_organic", "") or "").strip().lower() == "true"

        lead.full_name = (row.get("full_name") or "").strip()
        lead.email = (row.get("email") or "").strip().lower()
        # Phone is prefixed with 'p:' in the sheet ("p:+31612345678") — strip
        phone = (row.get("phone_number") or "").strip()
        if phone.lower().startswith("p:"):
            phone = phone[2:].strip()
        lead.phone_number = phone
        lead.company_name = (row.get("company_name") or "").strip()

        # Survey columns have long question-style names — match flexibly
        lead.industry = (
            row.get("which_industry_best_describes_your_business?")
            or row.get("which_industry_best_describes_your_business")
            or ""
        ).strip()
        lead.estimated_order_size = (
            row.get("the_moq_is_250_pieces._how_many_do_you_think_you'll_need?")
            or row.get("the_moq_is_250_pieces._how_many_do_you_think_you_ll_need")
            or ""
        ).strip()
        lead.lead_status = (row.get("lead_status") or "").strip()

        lead.source_url = SHEET_CSV_URL
        # Keep a compact debug copy of the original row
        lead.raw_row = " | ".join(f"{k}={v}" for k, v in row.items() if v)[:2000]

        if is_new:
            db.add(lead)
            inserted += 1
        else:
            updated += 1

        # Classify (needs the lead's email / company set above)
        lead.match_status = _classify_lead(db, lead)
        if lead.match_status == "existing_customer":
            matched_customer += 1
        elif lead.match_status == "known_prospect":
            matched_kvk += 1
        else:
            new_leads += 1

    db.commit()
    return {
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "existing_customer_matches": matched_customer,
        "known_prospect_matches": matched_kvk,
        "new_leads": new_leads,
        "total_in_sheet": inserted + updated,
    }


if __name__ == "__main__":
    # Allow running directly: `python -m app.facebook_leads`
    from app.db import SessionLocal
    db = SessionLocal()
    try:
        summary = import_facebook_leads(db)
        print(summary)
    finally:
        db.close()
