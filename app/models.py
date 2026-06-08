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
    # Schild-Inc-specific annotations from the historical Customer DB
    # (8.8k-row "All Combined 21-25" CSV — aggregated order lines)
    main_sector: Mapped[str] = mapped_column(Text, default="", index=True)
    sub_sector: Mapped[str] = mapped_column(Text, default="")
    customer_segment: Mapped[str] = mapped_column(Text, default="", index=True)  # 'B2B' / 'B2C'
    contact_person: Mapped[str] = mapped_column(Text, default="")
    phone_primary: Mapped[str] = mapped_column(Text, default="")
    website: Mapped[str] = mapped_column(Text, default="")
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
    kvk_number: Mapped[str] = mapped_column(Text, default="", index=True)
    kvk_establishment_number: Mapped[str] = mapped_column(Text, default="", index=True)
    kvk_company_entity_id: Mapped[str] = mapped_column(Text, default="", index=True)
    website_search_query: Mapped[str] = mapped_column(Text, default="")
    contact_search_query: Mapped[str] = mapped_column(Text, default="")
    company_name: Mapped[str] = mapped_column(Text, index=True)
    canonical_company_name_clean: Mapped[str] = mapped_column(Text, default="", index=True)
    canonical_name_geo_key: Mapped[str] = mapped_column(Text, default="", index=True)
    email: Mapped[str] = mapped_column(Text, default="", index=True)
    email_domain: Mapped[str] = mapped_column(Text, default="", index=True)
    email_discovery_status: Mapped[str] = mapped_column(Text, default="not_started", index=True)
    email_source_page: Mapped[str] = mapped_column(Text, default="")
    email_confidence: Mapped[int] = mapped_column(Integer, default=0, index=True)
    email_discovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    emails_found: Mapped[str] = mapped_column(Text, default="")
    pages_scanned: Mapped[str] = mapped_column(Text, default="")
    whatsapp_number: Mapped[str] = mapped_column(Text, default="")
    whatsapp_url: Mapped[str] = mapped_column(Text, default="")
    website: Mapped[str] = mapped_column(Text, default="")
    website_domain: Mapped[str] = mapped_column(Text, default="", index=True)
    linkedin_url: Mapped[str] = mapped_column(Text, default="")
    instagram_url: Mapped[str] = mapped_column(Text, default="")
    social_discovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    phone: Mapped[str] = mapped_column(Text, default="")
    city: Mapped[str] = mapped_column(Text, default="", index=True)
    state: Mapped[str] = mapped_column(Text, default="")
    country_code: Mapped[str] = mapped_column(Text, default="", index=True)
    address: Mapped[str] = mapped_column(Text, default="")
    google_maps_url: Mapped[str] = mapped_column(Text, default="")
    company_type: Mapped[str] = mapped_column(Text, default="")
    website_summary: Mapped[str] = mapped_column(Text, default="")
    discovery_highlights: Mapped[str] = mapped_column(Text, default="")
    discovery_error: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    bike_shop_tier: Mapped[str] = mapped_column(Text, default="Unclassified", index=True)
    bike_shop_segment: Mapped[str] = mapped_column(Text, default="")
    outreach_priority: Mapped[str] = mapped_column(Text, default="Manual Review", index=True)
    headquarters_required: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    franchise_or_buying_group: Mapped[str] = mapped_column(Text, default="")
    tier_reason: Mapped[str] = mapped_column(Text, default="")
    recommended_sales_angle: Mapped[str] = mapped_column(Text, default="")
    recommended_contact_type: Mapped[str] = mapped_column(Text, default="")
    custom_use_case: Mapped[str] = mapped_column(Text, default="")
    proof_line: Mapped[str] = mapped_column(Text, default="")
    manual_tier_override: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    review_status: Mapped[ProspectState] = mapped_column(Enum(ProspectState), default=ProspectState.pending, index=True)
    match_status: Mapped[MatchStatus] = mapped_column(Enum(MatchStatus), default=MatchStatus.new_prospect, index=True)
    match_method: Mapped[str] = mapped_column(Text, default="")
    match_score: Mapped[int] = mapped_column(Integer, default=0)
    match_reasons: Mapped[str] = mapped_column(Text, default="")
    existing_customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), nullable=True, index=True)
    approved_for_outreach: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    last_contacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_matched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    matched_customer: Mapped[Customer | None] = relationship(back_populates="matched_prospects")
    queue_items: Mapped[list["OutreachQueueItem"]] = relationship(back_populates="prospect", cascade="all, delete-orphan")
    activity_logs: Mapped[list["ProspectActivityLog"]] = relationship(back_populates="prospect", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("source", "source_reference", name="uq_prospect_source_reference"),
    )


class OutreachQueueItem(Base):
    __tablename__ = "outreach_queue_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    prospect_id: Mapped[int] = mapped_column(ForeignKey("prospects.id"), index=True)
    queue_date: Mapped[date] = mapped_column(Date, index=True)
    state: Mapped[QueueState] = mapped_column(Enum(QueueState), default=QueueState.queued, index=True)
    channel: Mapped[str] = mapped_column(Text, default="email", index=True)
    campaign_name: Mapped[str] = mapped_column(Text, default="default")
    subject: Mapped[str] = mapped_column(Text, default="")
    body: Mapped[str] = mapped_column(Text, default="")
    body_html: Mapped[str] = mapped_column(Text, default="")
    reviewer_notes: Mapped[str] = mapped_column(Text, default="")
    approved_by: Mapped[str] = mapped_column(Text, default="")
    sent_to: Mapped[str] = mapped_column(Text, default="")
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False)
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
    channel: Mapped[str] = mapped_column(Text, default="email", index=True)
    provider: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(Text, default="", index=True)
    response_excerpt: Mapped[str] = mapped_column(Text, default="")
    html_excerpt: Mapped[str] = mapped_column(Text, default="")
    reply_to: Mapped[str] = mapped_column(Text, default="")
    unsubscribe_token: Mapped[str] = mapped_column(Text, default="", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    queue_item: Mapped[OutreachQueueItem | None] = relationship(back_populates="email_logs")


class ProspectActivityLog(Base):
    __tablename__ = "prospect_activity_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    prospect_id: Mapped[int | None] = mapped_column(ForeignKey("prospects.id"), nullable=True, index=True)
    action_type: Mapped[str] = mapped_column(Text, default="", index=True)
    status: Mapped[str] = mapped_column(Text, default="", index=True)
    source_url: Mapped[str] = mapped_column(Text, default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    prospect: Mapped[Prospect | None] = relationship(back_populates="activity_logs")


class WebhookLog(Base):
    __tablename__ = "webhook_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(Text, index=True)
    event_id: Mapped[str] = mapped_column(Text, default="", index=True)
    event_type: Mapped[str] = mapped_column(Text, default="", index=True)
    status: Mapped[str] = mapped_column(Text, default="")
    payload_excerpt: Mapped[str] = mapped_column(Text, default="")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class KvkCompany(Base):
    __tablename__ = "kvk_companies"

    id: Mapped[int] = mapped_column(primary_key=True)

    source_system: Mapped[str] = mapped_column(Text, default="kvk_bike_list")
    source_file: Mapped[str] = mapped_column(Text, default="")
    company_entity_id: Mapped[str] = mapped_column(Text, unique=True, index=True, default="")
    record_type: Mapped[str] = mapped_column(Text, default="company")

    kvk_number: Mapped[str] = mapped_column(Text, unique=True, index=True)
    company_name: Mapped[str] = mapped_column(Text, index=True)
    canonical_company_name_clean: Mapped[str] = mapped_column(Text, default="", index=True)
    search_company_name: Mapped[str] = mapped_column(Text, default="")

    main_activity_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    main_activity_description: Mapped[str] = mapped_column(Text, default="")
    date_of_establishment: Mapped[date | None] = mapped_column(Date, nullable=True)

    country_code: Mapped[str] = mapped_column(Text, default="NL", index=True)
    province_code: Mapped[str] = mapped_column(Text, default="")
    establishments_count: Mapped[int] = mapped_column(Integer, default=1)
    primary_establishment_number: Mapped[str] = mapped_column(Text, default="")
    primary_city: Mapped[str] = mapped_column(Text, default="", index=True)
    primary_postal_code: Mapped[str] = mapped_column(Text, default="")
    primary_address: Mapped[str] = mapped_column(Text, default="")

    website: Mapped[str] = mapped_column(Text, default="")
    website_domain: Mapped[str] = mapped_column(Text, default="", index=True)
    email_public: Mapped[str] = mapped_column(Text, default="", index=True)
    phone_public: Mapped[str] = mapped_column(Text, default="")
    email_source_url: Mapped[str] = mapped_column(Text, default="")
    phone_source_url: Mapped[str] = mapped_column(Text, default="")
    email_confidence: Mapped[str] = mapped_column(Text, default="")
    phone_confidence: Mapped[str] = mapped_column(Text, default="")

    # Populated by the local browser agent (scripts/email_agent.py) when
    # it scrapes Google results — same shape as Prospect's social fields.
    whatsapp_number: Mapped[str] = mapped_column(Text, default="")
    whatsapp_url: Mapped[str] = mapped_column(Text, default="")
    instagram_url: Mapped[str] = mapped_column(Text, default="")
    linkedin_url: Mapped[str] = mapped_column(Text, default="")

    # Owner / decision-maker enrichment (Google-snippet agent) — used to
    # personalize cold outreach ("Hi {{greeting_name}}").
    owner_name: Mapped[str] = mapped_column(Text, default="", index=True)
    owner_role: Mapped[str] = mapped_column(Text, default="")
    owner_source: Mapped[str] = mapped_column(Text, default="")  # url the name came from
    owner_status: Mapped[str] = mapped_column(Text, default="pending", index=True)  # pending|found|none
    owner_search_attempts: Mapped[int] = mapped_column(Integer, default=0, index=True)

    enrichment_status: Mapped[str] = mapped_column(Text, default="pending", index=True)
    google_maps_query: Mapped[str] = mapped_column(Text, default="")
    contact_search_query: Mapped[str] = mapped_column(Text, default="")
    last_enrichment_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # How many times the agent has searched this record. Lets the pending
    # endpoint prioritize records with the fewest attempts.
    search_attempts: Mapped[int] = mapped_column(Integer, default=0, index=True)

    already_client_flag: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    client_match_status: Mapped[str] = mapped_column(Text, default="unknown", index=True)
    matched_customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), nullable=True)
    match_confidence: Mapped[str] = mapped_column(Text, default="")
    best_match_reason: Mapped[str] = mapped_column(Text, default="")

    bike_shop_tier: Mapped[str] = mapped_column(Text, default="Unclassified", index=True)
    bike_shop_segment: Mapped[str] = mapped_column(Text, default="")
    outreach_priority: Mapped[str] = mapped_column(Text, default="")
    tier_reason: Mapped[str] = mapped_column(Text, default="")
    headquarters_required: Mapped[bool] = mapped_column(Boolean, default=False)
    franchise_or_buying_group: Mapped[str] = mapped_column(Text, default="")
    recommended_sales_angle: Mapped[str] = mapped_column(Text, default="")
    recommended_contact_type: Mapped[str] = mapped_column(Text, default="")

    approved_for_outreach: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    notes: Mapped[str] = mapped_column(Text, default="")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    matched_customer: Mapped[Customer | None] = relationship("Customer", foreign_keys=[matched_customer_id])
    establishments: Mapped[list["KvkEstablishment"]] = relationship(back_populates="company", cascade="all, delete-orphan")


class KvkEstablishment(Base):
    __tablename__ = "kvk_establishments"

    id: Mapped[int] = mapped_column(primary_key=True)

    source_system: Mapped[str] = mapped_column(Text, default="kvk_bike_list")
    source_file: Mapped[str] = mapped_column(Text, default="")
    record_id: Mapped[str] = mapped_column(Text, unique=True, index=True, default="")
    record_type: Mapped[str] = mapped_column(Text, default="establishment")

    kvk_number: Mapped[str] = mapped_column(Text, index=True)
    establishment_number: Mapped[str] = mapped_column(Text, default="", index=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("kvk_companies.id"), nullable=True, index=True)

    company_name_raw: Mapped[str] = mapped_column(Text, default="")
    company_name: Mapped[str] = mapped_column(Text, index=True)
    canonical_company_name_clean: Mapped[str] = mapped_column(Text, default="", index=True)
    search_company_name: Mapped[str] = mapped_column(Text, default="")

    main_activity_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    main_activity_description: Mapped[str] = mapped_column(Text, default="")
    date_of_establishment: Mapped[date | None] = mapped_column(Date, nullable=True)

    country_code: Mapped[str] = mapped_column(Text, default="NL", index=True)
    province_code: Mapped[str] = mapped_column(Text, default="")
    non_mailing_indicator: Mapped[bool] = mapped_column(Boolean, default=False)

    visiting_street: Mapped[str] = mapped_column(Text, default="")
    visiting_house_number: Mapped[str] = mapped_column(Text, default="")
    visiting_house_letter: Mapped[str] = mapped_column(Text, default="")
    visiting_house_number_addition: Mapped[str] = mapped_column(Text, default="")
    visiting_location_addition: Mapped[str] = mapped_column(Text, default="")
    visiting_postal_code: Mapped[str] = mapped_column(Text, default="")
    visiting_city: Mapped[str] = mapped_column(Text, default="", index=True)
    visiting_municipality_code: Mapped[str] = mapped_column(Text, default="")
    visiting_municipality_name: Mapped[str] = mapped_column(Text, default="")

    postal_street: Mapped[str] = mapped_column(Text, default="")
    postal_house_number: Mapped[str] = mapped_column(Text, default="")
    postal_house_letter: Mapped[str] = mapped_column(Text, default="")
    postal_house_number_addition: Mapped[str] = mapped_column(Text, default="")
    postal_location_addition: Mapped[str] = mapped_column(Text, default="")
    postal_postal_code: Mapped[str] = mapped_column(Text, default="")
    postal_city: Mapped[str] = mapped_column(Text, default="")
    postal_municipality_code: Mapped[str] = mapped_column(Text, default="")
    postal_municipality_name: Mapped[str] = mapped_column(Text, default="")
    full_visiting_address: Mapped[str] = mapped_column(Text, default="")
    full_postal_address: Mapped[str] = mapped_column(Text, default="")

    website: Mapped[str] = mapped_column(Text, default="")
    website_domain: Mapped[str] = mapped_column(Text, default="")
    email_public: Mapped[str] = mapped_column(Text, default="")
    phone_public: Mapped[str] = mapped_column(Text, default="")
    email_source_url: Mapped[str] = mapped_column(Text, default="")
    phone_source_url: Mapped[str] = mapped_column(Text, default="")
    email_confidence: Mapped[str] = mapped_column(Text, default="")
    phone_confidence: Mapped[str] = mapped_column(Text, default="")

    enrichment_status: Mapped[str] = mapped_column(Text, default="pending", index=True)
    google_maps_query: Mapped[str] = mapped_column(Text, default="")
    contact_search_query: Mapped[str] = mapped_column(Text, default="")
    has_multiple_establishments: Mapped[bool] = mapped_column(Boolean, default=False)
    establishments_per_kvk: Mapped[int] = mapped_column(Integer, default=1)

    already_client_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    client_match_status: Mapped[str] = mapped_column(Text, default="unknown")
    matched_customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), nullable=True)

    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    company: Mapped[KvkCompany | None] = relationship(back_populates="establishments")
    matched_customer: Mapped[Customer | None] = relationship("Customer", foreign_keys=[matched_customer_id])

    __table_args__ = (
        UniqueConstraint("kvk_number", "establishment_number", name="uq_kvk_establishment"),
    )


class KvkImportLog(Base):
    __tablename__ = "kvk_import_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    import_batch_id: Mapped[str] = mapped_column(Text, index=True)
    file_name: Mapped[str] = mapped_column(Text, default="")
    record_type: Mapped[str] = mapped_column(Text, default="")
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    successful_upserts: Mapped[int] = mapped_column(Integer, default=0)
    failed_rows: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(Text, default="in_progress")
    notes: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class FacebookLead(Base):
    """Lead from the Facebook Lead Ads CRM spreadsheet."""

    __tablename__ = "facebook_leads"

    id: Mapped[int] = mapped_column(primary_key=True)
    fb_lead_id: Mapped[str] = mapped_column(Text, unique=True, index=True)
    created_time_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ad_name: Mapped[str] = mapped_column(Text, default="")
    adset_name: Mapped[str] = mapped_column(Text, default="")
    campaign_name: Mapped[str] = mapped_column(Text, default="")
    form_name: Mapped[str] = mapped_column(Text, default="")
    platform: Mapped[str] = mapped_column(Text, default="")
    is_organic: Mapped[bool] = mapped_column(Boolean, default=False)

    full_name: Mapped[str] = mapped_column(Text, default="", index=True)
    email: Mapped[str] = mapped_column(Text, default="", index=True)
    phone_number: Mapped[str] = mapped_column(Text, default="")
    company_name: Mapped[str] = mapped_column(Text, default="", index=True)

    industry: Mapped[str] = mapped_column(Text, default="", index=True)
    estimated_order_size: Mapped[str] = mapped_column(Text, default="")
    lead_status: Mapped[str] = mapped_column(Text, default="", index=True)

    # Sales-side annotations (from the historical Marketing Lead CSV).
    # Empty for leads from the live Lead Ads sheet that don't have these
    # fields yet.
    country: Mapped[str] = mapped_column(Text, default="", index=True)
    email_quality: Mapped[str] = mapped_column(Text, default="")
    quality_score: Mapped[str] = mapped_column(Text, default="")
    leads_quality: Mapped[str] = mapped_column(Text, default="", index=True)
    progress: Mapped[str] = mapped_column(Text, default="", index=True)
    pic: Mapped[str] = mapped_column(Text, default="")
    customer_segmentation: Mapped[str] = mapped_column(Text, default="")
    total_order_amount: Mapped[str] = mapped_column(Text, default="")
    detailed_information: Mapped[str] = mapped_column(Text, default="")
    email_marketing_consent: Mapped[str] = mapped_column(Text, default="")
    # Set by the classifier daemon (app/lead_classifier.py)
    main_sector: Mapped[str] = mapped_column(Text, default="", index=True)
    sub_sector: Mapped[str] = mapped_column(Text, default="")
    classifier_version: Mapped[int] = mapped_column(Integer, default=0, index=True)

    matched_customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), nullable=True, index=True)
    matched_kvk_company_id: Mapped[int | None] = mapped_column(ForeignKey("kvk_companies.id"), nullable=True, index=True)
    match_status: Mapped[str] = mapped_column(Text, default="new", index=True)

    source_url: Mapped[str] = mapped_column(Text, default="")
    raw_row: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)


# ===========================================================================
# Email engine — Gmail-backed campaigns with open/click/unsubscribe tracking
# ===========================================================================


class EmailTemplate(Base):
    """Reusable email template. Subject + HTML body with {{merge_fields}}."""

    __tablename__ = "email_templates"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text, index=True)
    # 'cold' | 'warm' | 'followup' | 'vip' | 'custom'
    category: Mapped[str] = mapped_column(Text, default="custom", index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    subject: Mapped[str] = mapped_column(Text, default="")
    body_html: Mapped[str] = mapped_column(Text, default="")
    body_text: Mapped[str] = mapped_column(Text, default="")
    # Comma-separated merge fields this template expects (for the UI hint).
    merge_fields: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    # Marks a built-in starter template (seeded, not user-created).
    is_starter: Mapped[bool] = mapped_column(Boolean, default=False)
    # Bump in code to re-seed starter templates on change.
    seed_version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)


class EmailCampaign(Base):
    """A send to a selected audience using one template."""

    __tablename__ = "email_campaigns"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text, index=True)
    template_id: Mapped[int | None] = mapped_column(ForeignKey("email_templates.id"), nullable=True, index=True)
    # Snapshot of the template at build time (so edits don't change in-flight sends).
    subject: Mapped[str] = mapped_column(Text, default="")
    body_html: Mapped[str] = mapped_column(Text, default="")
    body_text: Mapped[str] = mapped_column(Text, default="")
    # 'kvk' | 'lead' | 'customer' | 'mixed'
    audience_type: Mapped[str] = mapped_column(Text, default="kvk", index=True)
    # 'warm' | 'cold' — informational label for the operator.
    lead_temperature: Mapped[str] = mapped_column(Text, default="cold", index=True)
    # 'draft' | 'scheduled' | 'sending' | 'paused' | 'sent' | 'cancelled'
    status: Mapped[str] = mapped_column(Text, default="draft", index=True)
    sender_alias: Mapped[str] = mapped_column(Text, default="")
    sender_name: Mapped[str] = mapped_column(Text, default="")
    reply_to: Mapped[str] = mapped_column(Text, default="")
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    total_recipients: Mapped[int] = mapped_column(Integer, default=0)
    sent_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    open_count: Mapped[int] = mapped_column(Integer, default=0)
    click_count: Mapped[int] = mapped_column(Integer, default=0)
    unsubscribe_count: Mapped[int] = mapped_column(Integer, default=0)
    bounce_count: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    recipients: Mapped[list["EmailCampaignRecipient"]] = relationship(
        back_populates="campaign", cascade="all, delete-orphan"
    )


class EmailCampaignRecipient(Base):
    """One recipient inside a campaign, with per-recipient tracking state."""

    __tablename__ = "email_campaign_recipients"

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("email_campaigns.id"), index=True)
    # Origin of the recipient — for write-back + dedupe.
    source_type: Mapped[str] = mapped_column(Text, default="kvk", index=True)  # kvk|lead|customer|manual
    kvk_company_id: Mapped[int | None] = mapped_column(ForeignKey("kvk_companies.id"), nullable=True, index=True)
    facebook_lead_id: Mapped[int | None] = mapped_column(ForeignKey("facebook_leads.id"), nullable=True, index=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), nullable=True, index=True)

    to_email: Mapped[str] = mapped_column(Text, default="", index=True)
    company_name: Mapped[str] = mapped_column(Text, default="")
    contact_name: Mapped[str] = mapped_column(Text, default="")
    # JSON blob of merge values resolved at build time.
    merge_data: Mapped[str] = mapped_column(Text, default="{}")

    # 'pending' | 'sent' | 'failed' | 'bounced' | 'suppressed' | 'skipped'
    status: Mapped[str] = mapped_column(Text, default="pending", index=True)
    # Opaque token used in the tracking pixel + click links + unsubscribe.
    tracking_token: Mapped[str] = mapped_column(Text, default="", unique=True, index=True)
    gmail_message_id: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")

    open_count: Mapped[int] = mapped_column(Integer, default=0)
    click_count: Mapped[int] = mapped_column(Integer, default=0)
    first_opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_clicked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    unsubscribed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    campaign: Mapped[EmailCampaign] = relationship(back_populates="recipients")

    __table_args__ = (
        UniqueConstraint("campaign_id", "to_email", name="uq_campaign_recipient_email"),
    )


class EmailEvent(Base):
    """Raw open/click/unsubscribe/bounce event for analytics."""

    __tablename__ = "email_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    recipient_id: Mapped[int | None] = mapped_column(
        ForeignKey("email_campaign_recipients.id"), nullable=True, index=True
    )
    campaign_id: Mapped[int | None] = mapped_column(ForeignKey("email_campaigns.id"), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(Text, default="", index=True)  # open|click|unsubscribe|bounce
    url: Mapped[str] = mapped_column(Text, default="")
    user_agent: Mapped[str] = mapped_column(Text, default="")
    ip_address: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, index=True)


class GmailAccount(Base):
    """Stored OAuth credentials for the connected Gmail account (singleton row).

    Railway's filesystem is ephemeral, so the refresh token lives in Postgres.
    """

    __tablename__ = "gmail_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_email: Mapped[str] = mapped_column(Text, default="", index=True)
    # JSON-serialized google.oauth2.credentials.Credentials.to_json()
    token_json: Mapped[str] = mapped_column(Text, default="")
    scopes: Mapped[str] = mapped_column(Text, default="")
    # Verified send-as aliases discovered from the Gmail settings API.
    send_as_aliases: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)
    last_error: Mapped[str] = mapped_column(Text, default="")
    # Two-way email: timestamp of the last inbound poll (for incremental fetch).
    last_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ===========================================================================
# In-house CRM — Phase 1: Unified Contact Hub (360° audience record)
# ===========================================================================


class Contact(Base):
    """Master identity per person/company, merging Customer + KVK + Lead +
    Prospect rows so support sees one unified record.

    Identity resolution (app/contacts.py) merges on exact email / phone (E.164)
    / name+country — the same strict, no-fuzzy philosophy as KVK matching.
    """

    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    display_name: Mapped[str] = mapped_column(Text, default="", index=True)
    company_name: Mapped[str] = mapped_column(Text, default="", index=True)
    contact_person: Mapped[str] = mapped_column(Text, default="")
    primary_email: Mapped[str] = mapped_column(Text, default="", index=True)
    primary_phone: Mapped[str] = mapped_column(Text, default="", index=True)  # E.164 where possible

    city: Mapped[str] = mapped_column(Text, default="", index=True)
    country_code: Mapped[str] = mapped_column(Text, default="", index=True)
    sector: Mapped[str] = mapped_column(Text, default="", index=True)
    tier: Mapped[str] = mapped_column(Text, default="", index=True)
    website: Mapped[str] = mapped_column(Text, default="")
    lifetime_value: Mapped[float] = mapped_column(Numeric(12, 2), default=0)

    is_customer: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    do_not_contact: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    # Source links (any subset may be set).
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), nullable=True, index=True)
    kvk_company_id: Mapped[int | None] = mapped_column(ForeignKey("kvk_companies.id"), nullable=True, index=True)
    facebook_lead_id: Mapped[int | None] = mapped_column(ForeignKey("facebook_leads.id"), nullable=True, index=True)
    prospect_id: Mapped[int | None] = mapped_column(ForeignKey("prospects.id"), nullable=True, index=True)
    # Comma-joined list of contributing sources, e.g. "customer,kvk".
    source_summary: Mapped[str] = mapped_column(Text, default="", index=True)

    # Owner agent (Phase 2 fills this; plain int now to keep Phase 1 standalone).
    owner_agent_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    channels: Mapped[list["ContactChannel"]] = relationship(
        back_populates="contact", cascade="all, delete-orphan"
    )
    activities: Mapped[list["Activity"]] = relationship(
        back_populates="contact", cascade="all, delete-orphan"
    )


class ContactChannel(Base):
    """A reachable channel value for a contact (email/phone/whatsapp/social)."""

    __tablename__ = "contact_channels"

    id: Mapped[int] = mapped_column(primary_key=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id"), index=True)
    channel_type: Mapped[str] = mapped_column(Text, default="email", index=True)  # email|phone|whatsapp|instagram|linkedin|website
    value: Mapped[str] = mapped_column(Text, default="")
    value_normalized: Mapped[str] = mapped_column(Text, default="", index=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(Text, default="")  # customer|kvk|lead|prospect|manual
    label: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    contact: Mapped[Contact] = relationship(back_populates="channels")

    __table_args__ = (
        UniqueConstraint("contact_id", "channel_type", "value_normalized", name="uq_contact_channel_value"),
    )


class Activity(Base):
    """Unified timeline event for a contact (email/whatsapp/call/note/system)."""

    __tablename__ = "activities"

    id: Mapped[int] = mapped_column(primary_key=True)
    contact_id: Mapped[int] = mapped_column(ForeignKey("contacts.id"), index=True)
    # email_sent|email_open|email_click|email_reply|wa_in|wa_out|call|note|status|import|unsubscribe
    activity_type: Mapped[str] = mapped_column(Text, default="", index=True)
    channel: Mapped[str] = mapped_column(Text, default="system")  # email|whatsapp|call|system
    direction: Mapped[str] = mapped_column(Text, default="none")  # in|out|none
    title: Mapped[str] = mapped_column(Text, default="")
    body: Mapped[str] = mapped_column(Text, default="")
    agent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Generic link to the source row (e.g. email_campaign_recipient/message/call).
    ref_type: Mapped[str] = mapped_column(Text, default="")
    ref_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    contact: Mapped[Contact] = relationship(back_populates="activities")


# ===========================================================================
# In-house CRM — Phase 2: Shared Inbox (Trengo-style) + Agents + two-way email
# ===========================================================================


class Agent(Base):
    """A team member who works the shared inbox."""

    __tablename__ = "agents"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text, default="", index=True)
    email: Mapped[str] = mapped_column(Text, default="", unique=True, index=True)
    role: Mapped[str] = mapped_column(Text, default="agent", index=True)  # admin|agent
    team: Mapped[str] = mapped_column(Text, default="", index=True)  # support team name
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    # Per-agent login (Phase 6). Empty hash = cannot log in yet (admin sets it).
    password_hash: Mapped[str] = mapped_column(Text, default="")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class Conversation(Base):
    """A thread with one contact on one channel (email/whatsapp/call/note)."""

    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id"), nullable=True, index=True)
    channel: Mapped[str] = mapped_column(Text, default="email", index=True)  # email|whatsapp|call|note
    subject: Mapped[str] = mapped_column(Text, default="")
    # open|pending|snoozed|closed
    # open|pending|snoozed|closed|spam
    status: Mapped[str] = mapped_column(Text, default="open", index=True)
    assignee_agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True, index=True)
    labels: Mapped[str] = mapped_column(Text, default="")  # comma-separated
    unread: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    is_favorite: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_message_preview: Mapped[str] = mapped_column(Text, default="")
    last_direction: Mapped[str] = mapped_column(Text, default="")  # in|out
    snoozed_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Provider thread identifier (Gmail threadId / WhatsApp wa_id) for threading.
    external_thread_id: Mapped[str] = mapped_column(Text, default="", index=True)
    contact_email: Mapped[str] = mapped_column(Text, default="", index=True)
    contact_phone: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )
    contact: Mapped[Contact | None] = relationship()
    assignee: Mapped[Agent | None] = relationship()


class Message(Base):
    """One message (or internal note) inside a conversation."""

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), index=True)
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id"), nullable=True, index=True)
    direction: Mapped[str] = mapped_column(Text, default="in", index=True)  # in|out
    channel: Mapped[str] = mapped_column(Text, default="email")
    is_internal_note: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    from_addr: Mapped[str] = mapped_column(Text, default="")
    to_addr: Mapped[str] = mapped_column(Text, default="")
    subject: Mapped[str] = mapped_column(Text, default="")
    body_text: Mapped[str] = mapped_column(Text, default="")
    body_html: Mapped[str] = mapped_column(Text, default="")
    agent_id: Mapped[int | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    agent_name: Mapped[str] = mapped_column(Text, default="")
    external_message_id: Mapped[str] = mapped_column(Text, default="", index=True)
    external_thread_id: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(Text, default="")  # sent|received|failed
    error: Mapped[str] = mapped_column(Text, default="")
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class CannedReply(Base):
    """Saved quick response for the inbox."""

    __tablename__ = "canned_replies"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(Text, default="", index=True)
    category: Mapped[str] = mapped_column(Text, default="general")
    body: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    is_starter: Mapped[bool] = mapped_column(Boolean, default=False)
    seed_version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class WhatsappTemplate(Base):
    """A Meta-approved WhatsApp message template registered for use outside the
    24h service window. (Templates themselves are created/approved in Meta
    Business Manager; this just records name + language so operators can pick one.)
    """

    __tablename__ = "whatsapp_templates"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text, default="", index=True)
    language: Mapped[str] = mapped_column(Text, default="en")
    category: Mapped[str] = mapped_column(Text, default="")  # marketing|utility|authentication
    body_preview: Mapped[str] = mapped_column(Text, default="")
    # Number of {{n}} body parameters the template expects (for the UI form).
    param_count: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class AuditLog(Base):
    """Who did what — security/accountability trail for sensitive actions."""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    actor: Mapped[str] = mapped_column(Text, default="", index=True)  # agent name or 'owner'
    action: Mapped[str] = mapped_column(Text, default="", index=True)  # e.g. campaign.delete
    target_type: Mapped[str] = mapped_column(Text, default="")
    target_id: Mapped[str] = mapped_column(Text, default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, index=True)
