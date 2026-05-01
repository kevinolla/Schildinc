from __future__ import annotations

from datetime import datetime
from typing import Any

import stripe
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Customer, Invoice, WebhookLog
from app.utils import build_name_geo_key, email_domain, normalize_domain, normalize_email, normalize_text

stripe.api_key = settings.stripe_api_key or None


def sync_stripe_event(session: Session, event: dict[str, Any]) -> None:
    event_id = str(event.get("id", ""))
    event_type = str(event.get("type", ""))
    session.add(
        WebhookLog(
            provider="stripe",
            event_id=event_id,
            event_type=event_type,
            status="received",
            payload_excerpt=str(event)[:2000],
        )
    )

    payload = event.get("data", {}).get("object", {})
    if event_type.startswith("customer."):
        _upsert_customer_from_stripe_object(session, payload)
    elif event_type.startswith("invoice."):
        _upsert_invoice_from_stripe_object(session, payload)


def _upsert_customer_from_stripe_object(session: Session, obj: dict[str, Any]) -> Customer:
    stripe_customer_id = str(obj.get("id", ""))
    customer = session.scalar(select(Customer).where(Customer.stripe_customer_id == stripe_customer_id))
    if customer is None:
        customer = Customer(
            customer_entity_id=f"stripe:{stripe_customer_id}",
            stripe_customer_id=stripe_customer_id,
            source_system="stripe_webhook",
            already_client_flag=True,
            client_source="stripe_webhook",
        )
        session.add(customer)

    address = obj.get("address") or {}
    email = normalize_email(obj.get("email"))
    name = str(obj.get("name") or obj.get("description") or email or stripe_customer_id)
    customer.canonical_company_name = name
    customer.canonical_company_name_clean = normalize_text(name)
    customer.customer_email_primary = email
    customer.email_domain_primary = email_domain(email)
    customer.city = str(address.get("city") or "")
    customer.state = str(address.get("state") or "")
    customer.country_code = str(address.get("country") or "")
    customer.full_address = ", ".join(
        [part for part in [address.get("line1"), address.get("line2"), address.get("city"), address.get("postal_code"), address.get("country")] if part]
    )
    customer.match_key_domain = normalize_domain(email_domain(email))
    customer.website_domain_candidate = customer.website_domain_candidate or customer.email_domain_primary
    customer.canonical_name_geo_key = build_name_geo_key(name, customer.city, customer.state, customer.country_code)
    customer.updated_at = datetime.utcnow()
    return customer


def _upsert_invoice_from_stripe_object(session: Session, obj: dict[str, Any]) -> Invoice:
    invoice_id = str(obj.get("id", ""))
    invoice = session.scalar(select(Invoice).where(Invoice.invoice_id == invoice_id))
    is_new = invoice is None
    if invoice is None:
        invoice = Invoice(invoice_id=invoice_id, source_system="stripe_webhook")
        session.add(invoice)

    stripe_customer_id = str(obj.get("customer", ""))
    customer = None
    if stripe_customer_id:
        customer = session.scalar(select(Customer).where(Customer.stripe_customer_id == stripe_customer_id))
        if customer is None:
            customer = _upsert_customer_from_stripe_object(session, {"id": stripe_customer_id})

    invoice.customer = customer
    invoice.customer_entity_id = customer.customer_entity_id if customer else ""
    invoice.source_customer_id = stripe_customer_id
    invoice.invoice_number = str(obj.get("number") or "")
    invoice.status = str(obj.get("status") or "")
    invoice.currency = str(obj.get("currency") or "").upper()
    invoice.description = str(obj.get("description") or "")
    invoice.billing_name = str((obj.get("customer_name") or "") or "")
    invoice.customer_name_raw = invoice.billing_name
    invoice.customer_name_clean = normalize_text(invoice.billing_name)
    invoice.customer_email = normalize_email(obj.get("customer_email") or "")
    invoice.email_domain = email_domain(invoice.customer_email)
    invoice.website_domain_candidate = invoice.email_domain
    invoice.amount_paid = float((obj.get("amount_paid") or 0) / 100)
    invoice.total_invoiced = float((obj.get("total") or 0) / 100)
    invoice.subtotal = float((obj.get("subtotal") or 0) / 100)
    invoice.tax = float((obj.get("tax") or 0) / 100)
    invoice.discount_amount = float((obj.get("total_discount_amounts") or [{}])[0].get("amount", 0) / 100) if obj.get("total_discount_amounts") else 0
    invoice.already_client_flag = True

    if customer:
        customer.invoice_count = max(customer.invoice_count, len(customer.invoices) + (1 if is_new else 0))
        customer.last_invoice_date_utc = datetime.utcfromtimestamp(obj.get("created", 0)) if obj.get("created") else customer.last_invoice_date_utc
    return invoice
