"""
Facebook Lead Ads + Historical Marketing Lead CSV
=================================================
Two input shapes flow into the same `facebook_leads` table:

  A. The team's live Google Sheet (Lead Ads sync) — 20 columns,
     industry-survey style. Pulled every N minutes by a background
     scheduler.
  B. The historical Marketing Lead CSV the team exported from FB —
     34 columns with sales-side annotations (Quality Score, Progress,
     PIC, Customer Segmentation, Total Amount of order, etc.).

Both are upserted by `fb_lead_id`. The importer is column-name
flexible so adding/removing survey questions in the sheet doesn't
break the pipeline.
"""
from __future__ import annotations

import csv
import io
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Iterable
from urllib.request import Request, urlopen

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.models import Customer, FacebookLead, KvkCompany


# Public CSV-export URL of the live Lead Ads sheet (no OAuth needed for
# a sheet shared with "anyone with link can view").
GOOGLE_SHEET_ID = "10k2UB3qefKvskF1YemikhVCPk0JI8xmScH2dj_I7h5g"
GOOGLE_SHEET_GID = "1219149797"
SHEET_CSV_URL = (
    f"https://docs.google.com/spreadsheets/d/{GOOGLE_SHEET_ID}"
    f"/export?format=csv&gid={GOOGLE_SHEET_GID}"
)


# ── Column name aliases ────────────────────────────────────────────────────
# Source CSVs use long question-style column names that drift over time
# (and differ between the live sheet and the historical export). We
# look up each model field against a list of candidate header names —
# first match wins. New question wordings can be added here.
_ALIAS: dict[str, list[str]] = {
    "industry": [
        "which_industry_best_describes_your_business?",
        "which_industry_best_describes_your_business",
        "industry",
    ],
    "estimated_order_size": [
        # Live Lead Ads form
        "the_moq_is_250_pieces._how_many_do_you_think_you'll_need?",
        "the_moq_is_250_pieces._how_many_do_you_think_you_ll_need",
        # Historical form
        "estimate_your_annual_metal_label_requirements",
        "estimated_order_size",
    ],
    "detailed_information": [
        "Detailed Information",
        "detailed_information",
        "could_you_specify_your_product_and_how_you_intend_to_use_schild_inc's_metal_labels_on_it?",
    ],
    "country":               ["Country", "country"],
    "email_quality":         ["Email Quality", "email_quality"],
    "quality_score":         ["Quality Score", "quality_score"],
    "leads_quality":         ["Leads Quality", "leads_quality"],
    "progress":              ["Progress", "progress"],
    "pic":                   ["PIC", "pic"],
    "customer_segmentation": ["Customer Segmentation", "customer_segmentation"],
    "total_order_amount":    ["Total Amount of order", "total_order_amount"],
    "email_marketing_consent":["Email Marketing Consent", "email_marketing_consent"],
    "lead_status":           ["lead_status", "Followup?"],
}


def _pick(row: dict[str, str], field: str) -> str:
    """Look up a model field's value by trying every alias header."""
    for header in _ALIAS.get(field, [field]):
        val = row.get(header)
        if val is not None and str(val).strip() != "":
            return str(val).strip()
    return ""


def _fetch_sheet_csv() -> str:
    req = Request(SHEET_CSV_URL, headers={"User-Agent": "schild-fb-importer/1.0"})
    with urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _parse_created_time(raw: str) -> datetime | None:
    """Facebook timestamps look like '2025-12-02T19:09:42+07:00'."""
    if not raw:
        return None
    try:
        clean = raw.strip()
        if len(clean) >= 6 and clean[-3] == ":":
            clean = clean[:-3] + clean[-2:]
        return datetime.strptime(clean, "%Y-%m-%dT%H:%M:%S%z").astimezone(timezone.utc)
    except Exception:
        return None


def _classify_lead(db: Session, lead: FacebookLead) -> str:
    """Cross-reference vs customers + KVK. Returns match_status."""
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


def _populate_lead(lead: FacebookLead, row: dict[str, str], source_url: str) -> None:
    """Set every column on the FacebookLead from a CSV row."""
    lead.created_time_utc = _parse_created_time(row.get("created_time", ""))
    lead.ad_name = (row.get("ad_name") or "").strip()
    lead.adset_name = (row.get("adset_name") or "").strip()
    lead.campaign_name = (row.get("campaign_name") or "").strip()
    lead.form_name = (row.get("form_name") or "").strip()
    lead.platform = (row.get("platform") or "").strip()
    lead.is_organic = (row.get("is_organic", "") or "").strip().lower() == "true"

    lead.full_name = (row.get("full_name") or "").strip()
    lead.email = (row.get("email") or "").strip().lower()
    phone = (row.get("phone_number") or "").strip()
    if phone.lower().startswith("p:"):
        phone = phone[2:].strip()
    lead.phone_number = phone
    lead.company_name = (row.get("company_name") or "").strip()

    # Survey + sales annotations — flexible column lookup
    lead.industry = _pick(row, "industry")
    lead.estimated_order_size = _pick(row, "estimated_order_size")
    lead.lead_status = _pick(row, "lead_status")
    lead.country = _pick(row, "country")
    lead.email_quality = _pick(row, "email_quality")
    lead.quality_score = _pick(row, "quality_score")
    lead.leads_quality = _pick(row, "leads_quality")
    lead.progress = _pick(row, "progress")
    lead.pic = _pick(row, "pic")
    lead.customer_segmentation = _pick(row, "customer_segmentation")
    lead.total_order_amount = _pick(row, "total_order_amount")
    lead.detailed_information = _pick(row, "detailed_information")
    lead.email_marketing_consent = _pick(row, "email_marketing_consent")

    lead.source_url = source_url
    lead.raw_row = " | ".join(f"{k}={v}" for k, v in row.items() if v)[:2000]


def import_facebook_leads_from_csv(
    db: Session,
    csv_text: str,
    source_url: str,
    *,
    batch_size: int = 500,
    progress_print: bool = False,
) -> dict[str, int]:
    """
    Stream-parse a CSV blob and upsert every row by fb_lead_id.

    Strategy (handles 50k+ rows + duplicate fb_lead_ids in same file):
      1. Walk the CSV once, keeping the LAST row per fb_lead_id in
         memory (a dict). Last-wins matches the user's expectation that
         a re-export overrides earlier values.
      2. Pre-load every existing fb_lead_id from the DB into a set so
         we know up-front which rows are updates vs inserts.
      3. For each unique row, either fetch the existing FacebookLead
         (update) or instantiate a new one (insert). Add to session.
      4. Flush every `batch_size` rows so SQLAlchemy's session sees
         our newly-added rows on subsequent lookups (and so RAM
         pressure stays bounded). Commit at the end of each batch.
    """
    reader = csv.DictReader(io.StringIO(csv_text))

    # Phase 1: dedupe by fb_lead_id in memory — last occurrence wins
    rows_by_id: dict[str, dict[str, str]] = {}
    csv_skipped = 0
    for row in reader:
        fb_id = (row.get("id") or "").strip()
        if not fb_id:
            csv_skipped += 1
            continue
        rows_by_id[fb_id] = row

    if progress_print:
        print(f"[fb-import] CSV had {len(rows_by_id)} unique fb_lead_ids ({csv_skipped} skipped)")

    # Phase 2: pre-load existing fb_lead_ids (cheap — one column)
    existing_ids = {
        row[0] for row in db.execute(
            select(FacebookLead.fb_lead_id).where(
                FacebookLead.fb_lead_id.in_(list(rows_by_id.keys()))
            )
        ).all()
    }
    if progress_print:
        print(f"[fb-import] {len(existing_ids)} already in DB — will update; {len(rows_by_id) - len(existing_ids)} new")

    inserted = 0
    updated = 0
    matched_customer = 0
    matched_kvk = 0
    new_leads = 0
    seen_in_batch = 0
    processed = 0

    for fb_id, row in rows_by_id.items():
        if fb_id in existing_ids:
            lead = db.scalars(
                select(FacebookLead).where(FacebookLead.fb_lead_id == fb_id).limit(1)
            ).first()
            if lead is None:
                lead = FacebookLead(fb_lead_id=fb_id)
                db.add(lead)
                inserted += 1
            else:
                updated += 1
        else:
            lead = FacebookLead(fb_lead_id=fb_id)
            db.add(lead)
            inserted += 1
            existing_ids.add(fb_id)  # track within this run

        _populate_lead(lead, row, source_url)
        lead.match_status = _classify_lead(db, lead)
        if lead.match_status == "existing_customer":
            matched_customer += 1
        elif lead.match_status == "known_prospect":
            matched_kvk += 1
        else:
            new_leads += 1

        seen_in_batch += 1
        processed += 1
        if seen_in_batch >= batch_size:
            db.commit()
            if progress_print:
                print(f"[fb-import] committed {processed}/{len(rows_by_id)} rows")
            seen_in_batch = 0

    db.commit()
    return {
        "inserted": inserted,
        "updated": updated,
        "skipped": csv_skipped,
        "existing_customer_matches": matched_customer,
        "known_prospect_matches": matched_kvk,
        "new_leads": new_leads,
        "total_in_sheet": inserted + updated,
    }


def import_facebook_leads(db: Session) -> dict[str, int]:
    """Convenience wrapper that fetches the live sheet and imports it."""
    csv_text = _fetch_sheet_csv()
    return import_facebook_leads_from_csv(db, csv_text, SHEET_CSV_URL)


# ── Background auto-sync scheduler ─────────────────────────────────────────
_fb_sync_started = False
_fb_sync_lock = threading.Lock()


def _fb_sync_loop() -> None:
    """Pull the live sheet every N seconds. Idempotent — safe to repeat."""
    interval = getattr(settings, "fb_leads_auto_sync_interval", 900)
    while True:
        try:
            time.sleep(interval)
            db = SessionLocal()
            try:
                summary = import_facebook_leads(db)
                print(
                    f"[fb-leads-sync] {summary['inserted']} new, "
                    f"{summary['updated']} updated"
                )
            finally:
                db.close()
        except Exception as exc:
            print(f"[fb-leads-sync] error (will retry next cycle): {exc}")


def start_facebook_leads_scheduler() -> None:
    """Idempotent — only spawns the daemon once."""
    global _fb_sync_started
    if not getattr(settings, "fb_leads_auto_sync_enabled", True):
        return
    with _fb_sync_lock:
        if _fb_sync_started:
            return
        _fb_sync_started = True
    threading.Thread(target=_fb_sync_loop, daemon=True, name="fb-leads-sync").start()


if __name__ == "__main__":
    db = SessionLocal()
    try:
        summary = import_facebook_leads(db)
        print(summary)
    finally:
        db.close()
