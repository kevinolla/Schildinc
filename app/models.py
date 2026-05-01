from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Enum, ForeignKey, Integer, Numeric, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class ProspectState(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class MatchStatus(str, enum.Enum):
    existing_customer = "existing_customer"
    possible_match = "possible_match"
    new_prospect = "new_prospect"


class QueueState(str, enum.Enum):
    queued = "queued"
    ready = "ready"
    sent = "sent"
    skipped = "skipped"
    suppressed = "suppressed"


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_entity_id: Mapped[str] = mapped_column(Text, unique=True, index=True)
    source_system: Mapped[str] = mapped_column(Text, default="import")
    canonical_company_name: Mapped[str] = mapped_column(Text)
    canonical_company_name_clean: Mapped[str] = mapped_column(Text, index=True)
    canonical_name_geo_key: Mapped[str] = mapped_column(Text, default="", index=True)
    match_key_primary: Mapped[str] = mapped_column(Text, default="")
    match_key_domain: Mapped[str] = mapped_column(Text, default="", index=True)
    customer_email_primary: Mapped[str] = mapped_column(Text, default="", index=True)
    email_domain_primary: Mapped[str] = mapped_column(Text, default="", index=True)
    website_domain_candidate: Mapped[str] = mapped_column(Text, default="", index=True)
    city: Mapped[str] = mapped_column(Text, default="", index=True)
    state: Mapped[str] = mapped_column(Text, default="")
    country_code: Mapped[str] = mapped_column(Text, default="", index=True)
    full_address: Mapped[str] = mapped_column(Text, default="")
    billing_names_seen: Mapped[str] = mapped_column(Text, default="")
    customer_name_variants: Mapped[str] = mapped_column(Text, default="")
    customer_email_variants: Mapped[str] = mapped_column(Text, default="")
    source_customer_ids: Mapped[str] = mapped_column(Text, default="")
    source_invoice_ids: Mapped[str] = mapped_column(Text, default="")
    source_customer_id_count: Mapped[int] = mapped_column(Integer, default=0)
    invoice_count: Mapped[int] = mapped_column(Integer, default=0)
    currencies: Mapped[str] = mapped_column(Text, default="")
    lifetime_amount_paid: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    lifetime_total_invoiced: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    first_invoice_date_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_invoice_date_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_paid_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_paid_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    already_client_flag: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    client_source: Mapped[str] = mapped_column(Text, default="")
    stripe_customer_id: Mapped[str] = mapped_column(Text, default="", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    invoices: Mapped[list["Invoice"]] = relationship(back_populates="customer", cascade="all, delete-orphan")
    matched_prospects: Mapped[list["Prospect"]] = relationship(back_populates="matched_customer")


class Invoice(Base):
    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[str] = mapped_column(Text, unique=True, index=True)
    source_system: Mapped[str] = mapped_column(Text, default="import")
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), nullable=True, index=True)
    customer_entity_id: Mapped[str] = mapped_column(Text, default="", index=True)
    source_customer_id: Mapped[str] = mapped_column(Text, default="", index=True)
    invoice_number: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(Text, default="")
    currency: Mapped[str] = mapped_column(Text, default="")
    invoice_date_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finalized_at_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    description: Mapped[str] = mapped_column(Text, default="")
    billing_name: Mapped[str] = mapped_column(Text, default="")
    customer_name_raw: Mapped[str] = mapped_column(Text, default="")
    customer_name_clean: Mapped[str] = mapped_column(Text, default="", index=True)
    customer_email: Mapped[str] = mapped_column(Text, default="")
    email_domain: Mapped[str] = mapped_column(Text, default="", index=True)
    website_domain_candidate: Mapped[str] = mapped_column(Text, default="", index=True)
    city: Mapped[str] = mapped_column(Text, default="")
    state: Mapped[str] = mapped_column(Text, default="")
    country_code: Mapped[str] = mapped_column(Text, default="", index=True)
    amount_paid: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    total_invoiced: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    subtotal: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    tax: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    discount_amount: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    already_client_flag: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    customer: Mapped[Customer | None] = relationship(back_populates="invoices")


class Prospect(Base):
    __tablename__ = "prospects"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(Text, default="google_maps")
    source_reference: Mapped[str] = mapped_column(Text, default="", index=True)
    company_name: Mapped[str] = mapped_column(Text, index=True)
    canonical_company_name_clean: Mapped[str] = mapped_column(Text, default="", index=True)
    canonical_name_geo_key: Mapped[str] = mapped_column(Text, default="", index=True)
    email: Mapped[str] = mapped_column(Text, default="", index=True)
    email_domain: Mapped[str] = mapped_column(Text, default="", index=True)
    website: Mapped[str] = mapped_column(Text, default="")
    website_domain: Mapped[str] = mapped_column(Text, default="", index=True)
    phone: Mapped[str] = mapped_column(Text, default="")
    city: Mapped[str] = mapped_column(Text, default="", index=True)
    state: Mapped[str] = mapped_column(Text, default="")
    country_code: Mapped[str] = mapped_column(Text, default="", index=True)
    address: Mapped[str] = mapped_column(Text, default="")
    google_maps_url: Mapped[str] = mapped_column(Text, default="")
    company_type: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    review_status: Mapped[ProspectState] = mapped_column(Enum(ProspectState), default=ProspectState.pending, index=True)
    match_status: Mapped[MatchStatus] = mapped_column(Enum(MatchStatus), default=MatchStatus.new_prospect, index=True)
    match_method: Mapped[str] = mapped_column(Text, default="")
    match_score: Mapped[int] = mapped_column(Integer, default=0)
    match_reasons: Mapped[str] = mapped_column(Text, default="")
    existing_customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), nullable=True, index=True)
    approved_for_outreach: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    last_matched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    matched_customer: Mapped[Customer | None] = relationship(back_populates="matched_prospects")
    queue_items: Mapped[list["OutreachQueueItem"]] = relationship(back_populates="prospect", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("source", "source_reference", name="uq_prospect_source_reference"),
    )


class OutreachQueueItem(Base):
    __tablename__ = "outreach_queue_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    prospect_id: Mapped[int] = mapped_column(ForeignKey("prospects.id"), index=True)
    queue_date: Mapped[date] = mapped_column(Date, index=True)
    state: Mapped[QueueState] = mapped_column(Enum(QueueState), default=QueueState.queued, index=True)
    subject: Mapped[str] = mapped_column(Text, default="")
    body: Mapped[str] = mapped_column(Text, default="")
    reviewer_notes: Mapped[str] = mapped_column(Text, default="")
    approved_by: Mapped[str] = mapped_column(Text, default="")
    sent_to: Mapped[str] = mapped_column(Text, default="")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    prospect: Mapped[Prospect] = relationship(back_populates="queue_items")
    email_logs: Mapped[list["EmailLog"]] = relationship(back_populates="queue_item", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("prospect_id", "queue_date", name="uq_queue_per_prospect_per_day"),
    )


class SuppressionEntry(Base):
    __tablename__ = "suppression_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(Text, default="", index=True)
    domain: Mapped[str] = mapped_column(Text, default="", index=True)
    company_name: Mapped[str] = mapped_column(Text, default="")
    reason: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(Text, default="manual")
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class EmailLog(Base):
    __tablename__ = "email_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    queue_item_id: Mapped[int | None] = mapped_column(ForeignKey("outreach_queue_items.id"), nullable=True, index=True)
    prospect_id: Mapped[int | None] = mapped_column(ForeignKey("prospects.id"), nullable=True, index=True)
    to_email: Mapped[str] = mapped_column(Text, default="")
    subject: Mapped[str] = mapped_column(Text, default="")
    provider: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(Text, default="", index=True)
    response_excerpt: Mapped[str] = mapped_column(Text, default="")
    reply_to: Mapped[str] = mapped_column(Text, default="")
    unsubscribe_token: Mapped[str] = mapped_column(Text, default="", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    queue_item: Mapped[OutreachQueueItem | None] = relationship(back_populates="email_logs")


class WebhookLog(Base):
    __tablename__ = "webhook_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(Text, index=True)
    event_id: Mapped[str] = mapped_column(Text, default="", index=True)
    event_type: Mapped[str] = mapped_column(Text, default="", index=True)
    status: Mapped[str] = mapped_column(Text, default="")
    payload_excerpt: Mapped[str] = mapped_column(Text, default="")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
