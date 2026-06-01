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
from sqlalchemy.dialects.postgresql import insert as pg_insert
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


def _row_to_dict(row: dict[str, str], source_url: str) -> dict[str, Any] | None:
    """
    Translate one CSV row into a plain dict matching FacebookLead's
    columns — ready for the ON CONFLICT bulk upsert. Returns None if
    the row has no usable fb_lead_id.
    """
    fb_id = (row.get("id") or "").strip()
    if not fb_id:
        return None

    phone = (row.get("phone_number") or "").strip()
    if phone.lower().startswith("p:"):
        phone = phone[2:].strip()

    return {
        "fb_lead_id":              fb_id,
        "created_time_utc":        _parse_created_time(row.get("created_time", "")),
        "ad_name":                 (row.get("ad_name") or "").strip(),
        "adset_name":              (row.get("adset_name") or "").strip(),
        "campaign_name":           (row.get("campaign_name") or "").strip(),
        "form_name":               (row.get("form_name") or "").strip(),
        "platform":                (row.get("platform") or "").strip(),
        "is_organic":              (row.get("is_organic", "") or "").strip().lower() == "true",
        "full_name":               (row.get("full_name") or "").strip(),
        "email":                   (row.get("email") or "").strip().lower(),
        "phone_number":            phone,
        "company_name":            (row.get("company_name") or "").strip(),
        "industry":                _pick(row, "industry"),
        "estimated_order_size":    _pick(row, "estimated_order_size"),
        "lead_status":             _pick(row, "lead_status"),
        "country":                 _pick(row, "country"),
        "email_quality":           _pick(row, "email_quality"),
        "quality_score":           _pick(row, "quality_score"),
        "leads_quality":           _pick(row, "leads_quality"),
        "progress":                _pick(row, "progress"),
        "pic":                     _pick(row, "pic"),
        "customer_segmentation":   _pick(row, "customer_segmentation"),
        "total_order_amount":      _pick(row, "total_order_amount"),
        "detailed_information":    _pick(row, "detailed_information"),
        "email_marketing_consent": _pick(row, "email_marketing_consent"),
        "source_url":              source_url,
        "raw_row":                 (" | ".join(f"{k}={v}" for k, v in row.items() if v))[:2000],
        "match_status":            "new",  # bulk-set; classified later in batch
    }


def import_facebook_leads_from_csv(
    db: Session,
    csv_text: str,
    source_url: str,
    *,
    batch_size: int = 500,
    progress_print: bool = False,
) -> dict[str, int]:
    """
    Stream-parse a CSV blob and upsert every row by fb_lead_id using
    Postgres `INSERT ... ON CONFLICT (fb_lead_id) DO UPDATE` (true
    server-side upsert).

    Why ON CONFLICT instead of select-then-insert:
      - Race-free: no read/write gap that lets duplicates slip through
      - Bulk-safe: 500-row INSERT works even when 100% of the rows
        already exist in the DB (UPDATE path takes over per-row)
      - Handles the case of 50k-row historical CSV against a DB that
        already has overlapping IDs from the live sheet sync

    Cross-referencing (customer / KVK match) happens in a separate
    pass at the end on just the affected fb_lead_ids — keeps the
    upsert batch lean.
    """
    reader = csv.DictReader(io.StringIO(csv_text))

    # Phase 1: walk CSV → list of dicts, dedupe in memory by fb_lead_id
    # (last occurrence wins so a re-export overrides stale values)
    rows_by_id: dict[str, dict[str, Any]] = {}
    csv_skipped = 0
    for row in reader:
        record = _row_to_dict(row, source_url)
        if record is None:
            csv_skipped += 1
            continue
        rows_by_id[record["fb_lead_id"]] = record

    total = len(rows_by_id)
    if progress_print:
        print(f"[fb-import] CSV deduped to {total} unique fb_lead_ids ({csv_skipped} skipped)")

    # Phase 2: batched ON CONFLICT upsert. Every column EXCEPT the
    # immutables (id, fb_lead_id, created_at) is updated on conflict.
    UPDATE_COLS = [
        "created_time_utc", "ad_name", "adset_name", "campaign_name",
        "form_name", "platform", "is_organic",
        "full_name", "email", "phone_number", "company_name",
        "industry", "estimated_order_size", "lead_status",
        "country", "email_quality", "quality_score", "leads_quality",
        "progress", "pic", "customer_segmentation", "total_order_amount",
        "detailed_information", "email_marketing_consent",
        "source_url", "raw_row",
    ]

    all_records = list(rows_by_id.values())
    inserted_or_updated = 0
    for offset in range(0, total, batch_size):
        chunk = all_records[offset : offset + batch_size]
        stmt = pg_insert(FacebookLead).values(chunk)
        excluded = stmt.excluded
        do_update = {col: getattr(excluded, col) for col in UPDATE_COLS}
        do_update["updated_at"] = func.now()
        stmt = stmt.on_conflict_do_update(
            index_elements=["fb_lead_id"], set_=do_update
        )
        db.execute(stmt)
        db.commit()
        inserted_or_updated += len(chunk)
        if progress_print:
            print(f"[fb-import] upserted {inserted_or_updated}/{total} rows")

    # Phase 3: classify match status for the affected rows. We do this
    # OUTSIDE the upsert loop because _classify_lead needs the in-DB
    # row + customer/KVK joins per record (slow), and we want each
    # upsert batch itself to stay fast.
    matched_customer = matched_kvk = new_leads = 0
    if progress_print:
        print(f"[fb-import] classifying {total} leads against customers + KVK…")
    chunk_size = 200
    fb_ids = list(rows_by_id.keys())
    for offset in range(0, total, chunk_size):
        ids_chunk = fb_ids[offset : offset + chunk_size]
        leads = db.scalars(
            select(FacebookLead).where(FacebookLead.fb_lead_id.in_(ids_chunk))
        ).all()
        for lead in leads:
            lead.match_status = _classify_lead(db, lead)
            if lead.match_status == "existing_customer":
                matched_customer += 1
            elif lead.match_status == "known_prospect":
                matched_kvk += 1
            else:
                new_leads += 1
        db.commit()

    return {
        "inserted": inserted_or_updated,  # ON CONFLICT collapses insert+update count
        "updated": 0,
        "skipped": csv_skipped,
        "existing_customer_matches": matched_customer,
        "known_prospect_matches": matched_kvk,
        "new_leads": new_leads,
        "total_in_sheet": total,
    }


def import_facebook_leads(db: Session) -> dict[str, int]:
    """Convenience wrapper that fetches the live sheet and imports it."""
    csv_text = _fetch_sheet_csv()
    return import_facebook_leads_from_csv(db, csv_text, SHEET_CSV_URL)


# Bump this whenever SECTOR_KEYWORDS in lead_classifier.py changes
# meaningfully. Any row with classifier_version < CURRENT gets re-classified.
CURRENT_CLASSIFIER_VERSION = 1


# ── Background auto-sync scheduler ─────────────────────────────────────────
_fb_sync_started = False
_fb_sync_lock = threading.Lock()
_classifier_started = False
_classifier_lock = threading.Lock()


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


# ── Background sector-classifier daemon ────────────────────────────────────
# Every N seconds, scoop up any facebook_leads row whose
# classifier_version is below CURRENT, run the keyword classifier,
# write main_sector + classifier_version back. Fast — pure regex, no
# network. 10k rows / second easy.

def classify_pending_leads(db: Session, *, batch_size: int = 1000) -> dict[str, int]:
    """Classify any rows that haven't seen the current classifier version."""
    from app.lead_classifier import classify_lead

    pending = db.scalars(
        select(FacebookLead)
        .where(FacebookLead.classifier_version < CURRENT_CLASSIFIER_VERSION)
        .limit(batch_size)
    ).all()
    if not pending:
        return {"classified": 0, "remaining": 0}

    n_real = 0
    for row in pending:
        row.main_sector = classify_lead(row)
        row.classifier_version = CURRENT_CLASSIFIER_VERSION
        if row.main_sector != "Uncategorized":
            n_real += 1
    db.commit()

    remaining = db.scalar(
        select(func.count(FacebookLead.id)).where(
            FacebookLead.classifier_version < CURRENT_CLASSIFIER_VERSION
        )
    ) or 0
    return {"classified": len(pending), "real_sector_hits": n_real, "remaining": remaining}


def _classifier_loop() -> None:
    """Daemon — runs every CLASSIFIER_INTERVAL seconds."""
    interval = getattr(settings, "fb_leads_classifier_interval", 60)
    while True:
        try:
            db = SessionLocal()
            try:
                summary = classify_pending_leads(db, batch_size=2000)
                if summary["classified"]:
                    print(
                        f"[fb-classifier] {summary['classified']} classified "
                        f"({summary.get('real_sector_hits', 0)} hits), "
                        f"{summary.get('remaining', 0)} still pending"
                    )
            finally:
                db.close()
        except Exception as exc:
            print(f"[fb-classifier] error (will retry next cycle): {exc}")
        time.sleep(interval)


def start_lead_classifier_scheduler() -> None:
    """Idempotent. Off by default if FB_LEADS_CLASSIFIER_ENABLED=false."""
    global _classifier_started
    if not getattr(settings, "fb_leads_classifier_enabled", True):
        return
    with _classifier_lock:
        if _classifier_started:
            return
        _classifier_started = True
    threading.Thread(
        target=_classifier_loop, daemon=True, name="fb-leads-classifier"
    ).start()


if __name__ == "__main__":
    db = SessionLocal()
    try:
        summary = import_facebook_leads(db)
        print(summary)
    finally:
        db.close()
