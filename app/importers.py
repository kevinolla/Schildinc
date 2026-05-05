from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

import uuid
from datetime import timezone

from app.models import Customer, Invoice, KvkCompany, KvkEstablishment, KvkImportLog, Prospect, ProspectState
from app.tiering import apply_bike_tier, score_kvk_company_tier
from app.utils import build_name_geo_key, email_domain, normalize_domain, normalize_email, normalize_text, parse_bool


def parse_dt(value: Any) -> datetime | None:
    if value in ("", None) or (isinstance(value, float) and pd.isna(value)):
        return None
    dt = pd.to_datetime(value, utc=True, errors="coerce")
    return None if pd.isna(dt) else dt.to_pydatetime()


@dataclass
class ImportSummary:
    inserted: int = 0
    updated: int = 0


def read_csv_upload(contents: bytes) -> pd.DataFrame:
    return pd.read_csv(BytesIO(contents)).fillna("")


def upsert_customers_from_dataframe(session: Session, df: pd.DataFrame) -> ImportSummary:
    summary = ImportSummary()
    entity_ids = [str(item).strip() for item in df.get("customer_entity_id", []) if str(item).strip()]
    existing = {
        customer.customer_entity_id: customer
        for customer in session.scalars(select(Customer).where(Customer.customer_entity_id.in_(entity_ids))).all()
    } if entity_ids else {}

    for record in df.to_dict(orient="records"):
        entity_id = str(record.get("customer_entity_id", "")).strip()
        if not entity_id:
            continue

        customer = existing.get(entity_id)
        if customer is None:
            customer = Customer(customer_entity_id=entity_id)
            session.add(customer)
            existing[entity_id] = customer
            summary.inserted += 1
        else:
            summary.updated += 1

        customer.source_system = str(record.get("source_system", "import"))
        customer.match_key_primary = str(record.get("match_key_primary", ""))
        customer.match_key_domain = normalize_domain(record.get("match_key_domain", ""))
        customer.canonical_name_geo_key = str(record.get("match_key_name_geo", "")) or build_name_geo_key(
            record.get("canonical_company_name"),
            record.get("city"),
            record.get("state"),
            record.get("country_code"),
        )
        customer.canonical_company_name = str(record.get("canonical_company_name", ""))
        customer.canonical_company_name_clean = normalize_text(record.get("canonical_company_name_clean") or record.get("canonical_company_name"))
        customer.customer_email_primary = normalize_email(record.get("customer_email_primary", ""))
        customer.email_domain_primary = normalize_domain(record.get("email_domain_primary", "")) or email_domain(record.get("customer_email_primary"))
        customer.website_domain_candidate = normalize_domain(record.get("website_domain_candidate", ""))
        customer.city = str(record.get("city", ""))
        customer.state = str(record.get("state", ""))
        customer.country_code = str(record.get("country_code", ""))
        customer.full_address = str(record.get("full_address", ""))
        customer.billing_names_seen = str(record.get("billing_names_seen", ""))
        customer.customer_name_variants = str(record.get("customer_name_variants", ""))
        customer.customer_email_variants = str(record.get("customer_email_variants", ""))
        customer.source_customer_ids = str(record.get("source_customer_ids", ""))
        customer.source_invoice_ids = str(record.get("source_invoice_ids", ""))
        customer.source_customer_id_count = int(record.get("source_customer_id_count", 0) or 0)
        customer.invoice_count = int(record.get("invoice_count", 0) or 0)
        customer.currencies = str(record.get("currencies", ""))
        customer.lifetime_amount_paid = float(record.get("lifetime_amount_paid", 0) or 0)
        customer.lifetime_total_invoiced = float(record.get("lifetime_total_invoiced", 0) or 0)
        customer.first_invoice_date_utc = parse_dt(record.get("first_invoice_date_utc"))
        customer.last_invoice_date_utc = parse_dt(record.get("last_invoice_date_utc"))
        customer.first_paid_at_utc = parse_dt(record.get("first_paid_at_utc"))
        customer.last_paid_at_utc = parse_dt(record.get("last_paid_at_utc"))
        customer.already_client_flag = parse_bool(record.get("already_client_flag"))
        customer.client_source = str(record.get("client_source", ""))
        if not customer.stripe_customer_id:
            customer.stripe_customer_id = str(record.get("source_customer_ids", "")).split("|")[0]
    return summary


def upsert_invoices_from_dataframe(session: Session, df: pd.DataFrame) -> ImportSummary:
    summary = ImportSummary()
    invoice_ids = [str(item).strip() for item in df.get("invoice_id", []) if str(item).strip()]
    customer_entity_ids = [str(item).strip() for item in df.get("customer_entity_id", []) if str(item).strip()]
    existing_invoices = {
        invoice.invoice_id: invoice
        for invoice in session.scalars(select(Invoice).where(Invoice.invoice_id.in_(invoice_ids))).all()
    } if invoice_ids else {}
    customers = {
        customer.customer_entity_id: customer
        for customer in session.scalars(select(Customer).where(Customer.customer_entity_id.in_(customer_entity_ids))).all()
    } if customer_entity_ids else {}

    for record in df.to_dict(orient="records"):
        invoice_pk = str(record.get("invoice_id", "")).strip()
        if not invoice_pk:
            continue

        invoice = existing_invoices.get(invoice_pk)
        if invoice is None:
            invoice = Invoice(invoice_id=invoice_pk)
            session.add(invoice)
            existing_invoices[invoice_pk] = invoice
            summary.inserted += 1
        else:
            summary.updated += 1

        invoice.source_system = str(record.get("source_system", "import"))
        invoice.customer_entity_id = str(record.get("customer_entity_id", ""))
        invoice.source_customer_id = str(record.get("source_customer_id", ""))
        invoice.invoice_number = str(record.get("invoice_number", ""))
        invoice.status = str(record.get("status", ""))
        invoice.currency = str(record.get("currency", ""))
        invoice.invoice_date_utc = parse_dt(record.get("invoice_date_utc"))
        invoice.paid_at_utc = parse_dt(record.get("paid_at_utc"))
        invoice.finalized_at_utc = parse_dt(record.get("finalized_at_utc"))
        invoice.description = str(record.get("description", ""))
        invoice.billing_name = str(record.get("billing_name", ""))
        invoice.customer_name_raw = str(record.get("customer_name_raw", ""))
        invoice.customer_name_clean = normalize_text(record.get("customer_name_clean") or record.get("customer_name_raw"))
        invoice.customer_email = normalize_email(record.get("customer_email", ""))
        invoice.email_domain = normalize_domain(record.get("email_domain", "")) or email_domain(record.get("customer_email"))
        invoice.website_domain_candidate = normalize_domain(record.get("website_domain_candidate", ""))
        invoice.city = str(record.get("city", ""))
        invoice.state = str(record.get("state", ""))
        invoice.country_code = str(record.get("country_code", ""))
        invoice.amount_paid = float(record.get("amount_paid", 0) or 0)
        invoice.total_invoiced = float(record.get("total_invoiced", 0) or 0)
        invoice.subtotal = float(record.get("subtotal", 0) or 0)
        invoice.tax = float(record.get("tax", 0) or 0)
        invoice.discount_amount = float(record.get("discount_amount", 0) or 0)
        invoice.already_client_flag = parse_bool(record.get("already_client_flag"))

        customer = customers.get(invoice.customer_entity_id)
        if customer:
            invoice.customer = customer
    return summary


def upsert_prospects_from_dataframe(session: Session, df: pd.DataFrame, source: str = "google_maps_csv") -> ImportSummary:
    summary = ImportSummary()
    for record in df.to_dict(orient="records"):
        source_reference = str(
            record.get("source_reference")
            or record.get("place_id")
            or record.get("google_maps_url")
            or record.get("website")
            or record.get("company_name")
            or record.get("name")
            or ""
        ).strip()
        company_name = str(record.get("company_name") or record.get("name") or "").strip()
        if not company_name:
            continue

        prospect = None
        if source_reference:
            prospect = session.scalar(
                select(Prospect).where(Prospect.source == source, Prospect.source_reference == source_reference)
            )
        if prospect is None:
            prospect = Prospect(source=source, source_reference=source_reference, company_name=company_name)
            session.add(prospect)
            summary.inserted += 1
        else:
            summary.updated += 1

        prospect.company_name = company_name
        prospect.canonical_company_name_clean = normalize_text(company_name)
        prospect.email = normalize_email(record.get("email") or record.get("best_email") or "")
        prospect.email_domain = email_domain(prospect.email)
        prospect.whatsapp_number = str(record.get("whatsapp_number") or prospect.whatsapp_number or "")
        prospect.whatsapp_url = str(record.get("whatsapp_url") or prospect.whatsapp_url or "")
        if prospect.email:
            prospect.email_discovery_status = "imported"
            prospect.email_confidence = max(prospect.email_confidence, 60)
        elif prospect.whatsapp_number or record.get("linkedin_url") or record.get("instagram_url"):
            prospect.email_discovery_status = "partial"
        prospect.website = str(record.get("website") or "")
        prospect.website_domain = normalize_domain(record.get("website_domain") or prospect.website)
        prospect.phone = str(record.get("phone") or "")
        prospect.city = str(record.get("city") or record.get("google_maps_match_city") or "")
        prospect.state = str(record.get("state") or record.get("google_maps_match_state") or "")
        prospect.country_code = str(record.get("country_code") or record.get("country") or record.get("google_maps_match_country") or "")
        prospect.address = str(record.get("address") or record.get("formatted_address") or "")
        prospect.google_maps_url = str(record.get("google_maps_url") or record.get("maps_url") or "")
        prospect.company_type = str(record.get("company_type") or record.get("type") or "")
        prospect.linkedin_url = str(record.get("linkedin_url") or prospect.linkedin_url or "")
        prospect.instagram_url = str(record.get("instagram_url") or prospect.instagram_url or "")
        prospect.notes = str(record.get("notes") or prospect.notes or "")
        if prospect.review_status is None:
            prospect.review_status = ProspectState.pending
        prospect.approved_for_outreach = False if prospect.review_status != ProspectState.approved else prospect.approved_for_outreach
        prospect.canonical_name_geo_key = build_name_geo_key(company_name, prospect.city, prospect.state, prospect.country_code)
        apply_bike_tier(prospect)
    return summary


@dataclass
class KvkImportSummary:
    inserted: int = 0
    updated: int = 0
    failed: int = 0
    batch_id: str = ""


def _parse_date(value: Any) -> "date | None":
    from datetime import date
    if value in ("", None) or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        parsed = pd.to_datetime(value, errors="coerce")
        if pd.isna(parsed):
            return None
        return parsed.date()
    except Exception:
        return None


def _parse_int(value: Any, default: int = 0) -> int:
    try:
        if value in ("", None) or (isinstance(value, float) and pd.isna(value)):
            return default
        return int(float(value))
    except (ValueError, TypeError):
        return default


def upsert_kvk_companies_from_dataframe(session: Session, df: pd.DataFrame, file_name: str = "") -> KvkImportSummary:
    batch_id = str(uuid.uuid4())
    summary = KvkImportSummary(batch_id=batch_id)

    log = KvkImportLog(
        import_batch_id=batch_id,
        file_name=file_name,
        record_type="companies",
        row_count=len(df),
        started_at=datetime.now(tz=timezone.utc),
    )
    session.add(log)
    session.flush()

    kvk_numbers = [str(r.get("kvk_number", "")).strip() for r in df.to_dict(orient="records") if str(r.get("kvk_number", "")).strip()]
    existing = {
        c.kvk_number: c
        for c in session.scalars(select(KvkCompany).where(KvkCompany.kvk_number.in_(kvk_numbers))).all()
    } if kvk_numbers else {}

    for record in df.to_dict(orient="records"):
        kvk_number = str(record.get("kvk_number", "")).strip()
        company_name = str(record.get("company_name", "")).strip()
        if not kvk_number or not company_name:
            summary.failed += 1
            continue
        try:
            company = existing.get(kvk_number)
            if company is None:
                company = KvkCompany(kvk_number=kvk_number)
                session.add(company)
                existing[kvk_number] = company
                summary.inserted += 1
            else:
                summary.updated += 1

            company.source_system = str(record.get("source_system", "kvk_bike_list"))
            company.source_file = str(record.get("source_file", file_name))
            company.company_entity_id = str(record.get("company_entity_id", f"kvk_company_{kvk_number}"))
            company.company_name = company_name
            company.canonical_company_name_clean = str(record.get("canonical_company_name_clean", "") or normalize_text(company_name))
            company.search_company_name = str(record.get("search_company_name", company_name))
            company.main_activity_code = _parse_int(record.get("main_activity_code"), 0) or None
            company.main_activity_description = str(record.get("main_activity_description", ""))
            company.date_of_establishment = _parse_date(record.get("date_of_establishment"))
            company.country_code = str(record.get("country_code", "NL"))
            company.province_code = str(record.get("province_code", ""))
            company.establishments_count = _parse_int(record.get("establishments_count"), 1)
            company.primary_establishment_number = str(record.get("primary_establishment_number", ""))
            company.primary_city = str(record.get("primary_city", ""))
            company.primary_postal_code = str(record.get("primary_postal_code", ""))
            company.primary_address = str(record.get("primary_address", ""))
            # Only update contact fields if CSV has them (don't overwrite enriched data)
            if str(record.get("website", "")).strip():
                company.website = str(record.get("website", ""))
                company.website_domain = normalize_domain(company.website) or str(record.get("website_domain", ""))
            if str(record.get("email_public", "")).strip():
                company.email_public = normalize_email(str(record.get("email_public", "")))
                company.email_source_url = str(record.get("email_source_url", ""))
                company.email_confidence = str(record.get("email_confidence", ""))
            if str(record.get("phone_public", "")).strip():
                company.phone_public = str(record.get("phone_public", ""))
                company.phone_source_url = str(record.get("phone_source_url", ""))
                company.phone_confidence = str(record.get("phone_confidence", ""))
            company.google_maps_query = str(record.get("google_maps_query", ""))
            company.contact_search_query = str(record.get("contact_search_query", ""))
            if company.enrichment_status == "pending":
                company.enrichment_status = str(record.get("enrichment_status", "pending"))
            company.notes = str(record.get("notes", ""))
            company.updated_at = datetime.now(tz=timezone.utc)
            if not company.created_at:
                company.created_at = datetime.now(tz=timezone.utc)

            # Apply tiering on import
            decision = score_kvk_company_tier(company)
            company.bike_shop_tier = decision.bike_shop_tier
            company.bike_shop_segment = decision.bike_shop_segment
            company.outreach_priority = decision.outreach_priority
            company.headquarters_required = decision.headquarters_required
            company.franchise_or_buying_group = decision.franchise_or_buying_group
            company.tier_reason = decision.tier_reason
            company.recommended_sales_angle = decision.recommended_sales_angle
            company.recommended_contact_type = decision.recommended_contact_type

        except Exception as exc:
            summary.failed += 1
            session.rollback()
            session.add(log)
            continue

    session.flush()
    log.successful_upserts = summary.inserted + summary.updated
    log.failed_rows = summary.failed
    log.status = "success"
    log.completed_at = datetime.now(tz=timezone.utc)
    return summary


def upsert_kvk_establishments_from_dataframe(session: Session, df: pd.DataFrame, file_name: str = "") -> KvkImportSummary:
    batch_id = str(uuid.uuid4())
    summary = KvkImportSummary(batch_id=batch_id)

    log = KvkImportLog(
        import_batch_id=batch_id,
        file_name=file_name,
        record_type="establishments",
        row_count=len(df),
        started_at=datetime.now(tz=timezone.utc),
    )
    session.add(log)
    session.flush()

    record_ids = [str(r.get("record_id", "")).strip() for r in df.to_dict(orient="records") if str(r.get("record_id", "")).strip()]
    existing = {
        e.record_id: e
        for e in session.scalars(select(KvkEstablishment).where(KvkEstablishment.record_id.in_(record_ids))).all()
    } if record_ids else {}

    # Build kvk_number → company.id map for FK linking
    kvk_numbers = list({str(r.get("kvk_number", "")).strip() for r in df.to_dict(orient="records")})
    kvk_company_map = {
        c.kvk_number: c.id
        for c in session.scalars(select(KvkCompany).where(KvkCompany.kvk_number.in_(kvk_numbers))).all()
    } if kvk_numbers else {}

    for record in df.to_dict(orient="records"):
        kvk_number = str(record.get("kvk_number", "")).strip()
        record_id = str(record.get("record_id", "")).strip()
        company_name = str(record.get("company_name", "")).strip()
        if not kvk_number or not company_name:
            summary.failed += 1
            continue
        try:
            est = existing.get(record_id)
            if est is None:
                est = KvkEstablishment(record_id=record_id or f"kvk_est_{kvk_number}_{str(record.get('establishment_number',''))}")
                session.add(est)
                existing[record_id] = est
                summary.inserted += 1
            else:
                summary.updated += 1

            est.source_system = str(record.get("source_system", "kvk_bike_list"))
            est.source_file = str(record.get("source_file", file_name))
            est.kvk_number = kvk_number
            est.establishment_number = str(record.get("establishment_number", ""))
            est.company_id = kvk_company_map.get(kvk_number)
            est.company_name_raw = str(record.get("company_name_raw", company_name))
            est.company_name = company_name
            est.canonical_company_name_clean = str(record.get("canonical_company_name_clean", "") or normalize_text(company_name))
            est.search_company_name = str(record.get("search_company_name", company_name))
            est.main_activity_code = _parse_int(record.get("main_activity_code"), 0) or None
            est.main_activity_description = str(record.get("main_activity_description", ""))
            est.date_of_establishment = _parse_date(record.get("date_of_establishment"))
            est.country_code = str(record.get("country_code", "NL"))
            est.province_code = str(record.get("province_code", ""))
            est.non_mailing_indicator = parse_bool(record.get("non_mailing_indicator"))
            est.visiting_street = str(record.get("visiting_street", ""))
            est.visiting_house_number = str(record.get("visiting_house_number", ""))
            est.visiting_house_letter = str(record.get("visiting_house_letter", ""))
            est.visiting_house_number_addition = str(record.get("visiting_house_number_addition", ""))
            est.visiting_location_addition = str(record.get("visiting_location_addition", ""))
            est.visiting_postal_code = str(record.get("visiting_postal_code", ""))
            est.visiting_city = str(record.get("visiting_city", ""))
            est.visiting_municipality_code = str(record.get("visiting_municipality_code", ""))
            est.visiting_municipality_name = str(record.get("visiting_municipality_name", ""))
            est.postal_street = str(record.get("postal_street", ""))
            est.postal_house_number = str(record.get("postal_house_number", ""))
            est.postal_house_letter = str(record.get("postal_house_letter", ""))
            est.postal_house_number_addition = str(record.get("postal_house_number_addition", ""))
            est.postal_location_addition = str(record.get("postal_location_addition", ""))
            est.postal_postal_code = str(record.get("postal_postal_code", ""))
            est.postal_city = str(record.get("postal_city", ""))
            est.postal_municipality_code = str(record.get("postal_municipality_code", ""))
            est.postal_municipality_name = str(record.get("postal_municipality_name", ""))
            est.full_visiting_address = str(record.get("full_visiting_address", ""))
            est.full_postal_address = str(record.get("full_postal_address", ""))
            if str(record.get("website", "")).strip():
                est.website = str(record.get("website", ""))
                est.website_domain = normalize_domain(est.website)
            if str(record.get("email_public", "")).strip():
                est.email_public = normalize_email(str(record.get("email_public", "")))
                est.email_source_url = str(record.get("email_source_url", ""))
                est.email_confidence = str(record.get("email_confidence", ""))
            if str(record.get("phone_public", "")).strip():
                est.phone_public = str(record.get("phone_public", ""))
                est.phone_source_url = str(record.get("phone_source_url", ""))
                est.phone_confidence = str(record.get("phone_confidence", ""))
            est.google_maps_query = str(record.get("google_maps_query", ""))
            est.contact_search_query = str(record.get("contact_search_query", ""))
            est.has_multiple_establishments = parse_bool(record.get("has_multiple_establishments"))
            est.establishments_per_kvk = _parse_int(record.get("establishments_per_kvk"), 1)
            est.notes = str(record.get("notes", ""))
            est.updated_at = datetime.now(tz=timezone.utc)
            if not est.created_at:
                est.created_at = datetime.now(tz=timezone.utc)

        except Exception:
            summary.failed += 1
            session.rollback()
            session.add(log)
            continue

    session.flush()
    log.successful_upserts = summary.inserted + summary.updated
    log.failed_rows = summary.failed
    log.status = "success"
    log.completed_at = datetime.now(tz=timezone.utc)
    return summary
