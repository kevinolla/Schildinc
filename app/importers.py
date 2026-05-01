from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Customer, Invoice, Prospect, ProspectState
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
        prospect.website = str(record.get("website") or "")
        prospect.website_domain = normalize_domain(record.get("website_domain") or prospect.website)
        prospect.phone = str(record.get("phone") or "")
        prospect.city = str(record.get("city") or record.get("google_maps_match_city") or "")
        prospect.state = str(record.get("state") or record.get("google_maps_match_state") or "")
        prospect.country_code = str(record.get("country_code") or record.get("country") or record.get("google_maps_match_country") or "")
        prospect.address = str(record.get("address") or record.get("formatted_address") or "")
        prospect.google_maps_url = str(record.get("google_maps_url") or record.get("maps_url") or "")
        prospect.company_type = str(record.get("company_type") or record.get("type") or "")
        prospect.notes = str(record.get("notes") or "")
        prospect.review_status = ProspectState.pending
        prospect.approved_for_outreach = False
        prospect.canonical_name_geo_key = build_name_geo_key(company_name, prospect.city, prospect.state, prospect.country_code)
    return summary
