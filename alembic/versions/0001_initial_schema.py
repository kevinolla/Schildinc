"""Initial schema for Schild Inc CRM MVP

Revision ID: 0001_initial_schema
Revises: 
Create Date: 2026-05-01 00:00:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    prospect_state = postgresql.ENUM("pending", "approved", "rejected", name="prospectstate", create_type=False)
    match_status = postgresql.ENUM("existing_customer", "possible_match", "new_prospect", name="matchstatus", create_type=False)
    queue_state = postgresql.ENUM("queued", "ready", "sent", "skipped", "suppressed", name="queuestate", create_type=False)

    prospect_state.create(op.get_bind(), checkfirst=True)
    match_status.create(op.get_bind(), checkfirst=True)
    queue_state.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "customers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("customer_entity_id", sa.Text(), nullable=False),
        sa.Column("source_system", sa.Text(), nullable=False),
        sa.Column("canonical_company_name", sa.Text(), nullable=False),
        sa.Column("canonical_company_name_clean", sa.Text(), nullable=False),
        sa.Column("canonical_name_geo_key", sa.Text(), nullable=False),
        sa.Column("match_key_primary", sa.Text(), nullable=False),
        sa.Column("match_key_domain", sa.Text(), nullable=False),
        sa.Column("customer_email_primary", sa.Text(), nullable=False),
        sa.Column("email_domain_primary", sa.Text(), nullable=False),
        sa.Column("website_domain_candidate", sa.Text(), nullable=False),
        sa.Column("city", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("country_code", sa.Text(), nullable=False),
        sa.Column("full_address", sa.Text(), nullable=False),
        sa.Column("billing_names_seen", sa.Text(), nullable=False),
        sa.Column("customer_name_variants", sa.Text(), nullable=False),
        sa.Column("customer_email_variants", sa.Text(), nullable=False),
        sa.Column("source_customer_ids", sa.Text(), nullable=False),
        sa.Column("source_invoice_ids", sa.Text(), nullable=False),
        sa.Column("source_customer_id_count", sa.Integer(), nullable=False),
        sa.Column("invoice_count", sa.Integer(), nullable=False),
        sa.Column("currencies", sa.Text(), nullable=False),
        sa.Column("lifetime_amount_paid", sa.Numeric(12, 2), nullable=False),
        sa.Column("lifetime_total_invoiced", sa.Numeric(12, 2), nullable=False),
        sa.Column("first_invoice_date_utc", sa.DateTime(timezone=True)),
        sa.Column("last_invoice_date_utc", sa.DateTime(timezone=True)),
        sa.Column("first_paid_at_utc", sa.DateTime(timezone=True)),
        sa.Column("last_paid_at_utc", sa.DateTime(timezone=True)),
        sa.Column("already_client_flag", sa.Boolean(), nullable=False),
        sa.Column("client_source", sa.Text(), nullable=False),
        sa.Column("stripe_customer_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("customer_entity_id"),
    )
    op.create_index("ix_customers_customer_entity_id", "customers", ["customer_entity_id"])
    op.create_index("ix_customers_canonical_company_name_clean", "customers", ["canonical_company_name_clean"])
    op.create_index("ix_customers_canonical_name_geo_key", "customers", ["canonical_name_geo_key"])
    op.create_index("ix_customers_match_key_domain", "customers", ["match_key_domain"])
    op.create_index("ix_customers_customer_email_primary", "customers", ["customer_email_primary"])
    op.create_index("ix_customers_email_domain_primary", "customers", ["email_domain_primary"])
    op.create_index("ix_customers_website_domain_candidate", "customers", ["website_domain_candidate"])
    op.create_index("ix_customers_city", "customers", ["city"])
    op.create_index("ix_customers_country_code", "customers", ["country_code"])
    op.create_index("ix_customers_already_client_flag", "customers", ["already_client_flag"])
    op.create_index("ix_customers_stripe_customer_id", "customers", ["stripe_customer_id"])

    op.create_table(
        "invoices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("invoice_id", sa.Text(), nullable=False),
        sa.Column("source_system", sa.Text(), nullable=False),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id")),
        sa.Column("customer_entity_id", sa.Text(), nullable=False),
        sa.Column("source_customer_id", sa.Text(), nullable=False),
        sa.Column("invoice_number", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("currency", sa.Text(), nullable=False),
        sa.Column("invoice_date_utc", sa.DateTime(timezone=True)),
        sa.Column("paid_at_utc", sa.DateTime(timezone=True)),
        sa.Column("finalized_at_utc", sa.DateTime(timezone=True)),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("billing_name", sa.Text(), nullable=False),
        sa.Column("customer_name_raw", sa.Text(), nullable=False),
        sa.Column("customer_name_clean", sa.Text(), nullable=False),
        sa.Column("customer_email", sa.Text(), nullable=False),
        sa.Column("email_domain", sa.Text(), nullable=False),
        sa.Column("website_domain_candidate", sa.Text(), nullable=False),
        sa.Column("city", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("country_code", sa.Text(), nullable=False),
        sa.Column("amount_paid", sa.Numeric(12, 2), nullable=False),
        sa.Column("total_invoiced", sa.Numeric(12, 2), nullable=False),
        sa.Column("subtotal", sa.Numeric(12, 2), nullable=False),
        sa.Column("tax", sa.Numeric(12, 2), nullable=False),
        sa.Column("discount_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("already_client_flag", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("invoice_id"),
    )
    op.create_index("ix_invoices_invoice_id", "invoices", ["invoice_id"])
    op.create_index("ix_invoices_customer_id", "invoices", ["customer_id"])
    op.create_index("ix_invoices_customer_entity_id", "invoices", ["customer_entity_id"])
    op.create_index("ix_invoices_source_customer_id", "invoices", ["source_customer_id"])
    op.create_index("ix_invoices_customer_name_clean", "invoices", ["customer_name_clean"])
    op.create_index("ix_invoices_email_domain", "invoices", ["email_domain"])
    op.create_index("ix_invoices_website_domain_candidate", "invoices", ["website_domain_candidate"])
    op.create_index("ix_invoices_country_code", "invoices", ["country_code"])
    op.create_index("ix_invoices_already_client_flag", "invoices", ["already_client_flag"])

    op.create_table(
        "prospects",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_reference", sa.Text(), nullable=False),
        sa.Column("company_name", sa.Text(), nullable=False),
        sa.Column("canonical_company_name_clean", sa.Text(), nullable=False),
        sa.Column("canonical_name_geo_key", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("email_domain", sa.Text(), nullable=False),
        sa.Column("website", sa.Text(), nullable=False),
        sa.Column("website_domain", sa.Text(), nullable=False),
        sa.Column("phone", sa.Text(), nullable=False),
        sa.Column("city", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("country_code", sa.Text(), nullable=False),
        sa.Column("address", sa.Text(), nullable=False),
        sa.Column("google_maps_url", sa.Text(), nullable=False),
        sa.Column("company_type", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("review_status", prospect_state, nullable=False),
        sa.Column("match_status", match_status, nullable=False),
        sa.Column("match_method", sa.Text(), nullable=False),
        sa.Column("match_score", sa.Integer(), nullable=False),
        sa.Column("match_reasons", sa.Text(), nullable=False),
        sa.Column("existing_customer_id", sa.Integer(), sa.ForeignKey("customers.id")),
        sa.Column("approved_for_outreach", sa.Boolean(), nullable=False),
        sa.Column("last_matched_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source", "source_reference", name="uq_prospect_source_reference"),
    )
    op.create_index("ix_prospects_company_name", "prospects", ["company_name"])
    op.create_index("ix_prospects_canonical_company_name_clean", "prospects", ["canonical_company_name_clean"])
    op.create_index("ix_prospects_canonical_name_geo_key", "prospects", ["canonical_name_geo_key"])
    op.create_index("ix_prospects_email", "prospects", ["email"])
    op.create_index("ix_prospects_email_domain", "prospects", ["email_domain"])
    op.create_index("ix_prospects_website_domain", "prospects", ["website_domain"])
    op.create_index("ix_prospects_city", "prospects", ["city"])
    op.create_index("ix_prospects_country_code", "prospects", ["country_code"])
    op.create_index("ix_prospects_review_status", "prospects", ["review_status"])
    op.create_index("ix_prospects_match_status", "prospects", ["match_status"])
    op.create_index("ix_prospects_existing_customer_id", "prospects", ["existing_customer_id"])
    op.create_index("ix_prospects_approved_for_outreach", "prospects", ["approved_for_outreach"])

    op.create_table(
        "outreach_queue_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("prospect_id", sa.Integer(), sa.ForeignKey("prospects.id"), nullable=False),
        sa.Column("queue_date", sa.Date(), nullable=False),
        sa.Column("state", queue_state, nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("reviewer_notes", sa.Text(), nullable=False),
        sa.Column("approved_by", sa.Text(), nullable=False),
        sa.Column("sent_to", sa.Text(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("prospect_id", "queue_date", name="uq_queue_per_prospect_per_day"),
    )
    op.create_index("ix_outreach_queue_items_prospect_id", "outreach_queue_items", ["prospect_id"])
    op.create_index("ix_outreach_queue_items_queue_date", "outreach_queue_items", ["queue_date"])
    op.create_index("ix_outreach_queue_items_state", "outreach_queue_items", ["state"])

    op.create_table(
        "suppression_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("domain", sa.Text(), nullable=False),
        sa.Column("company_name", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_suppression_entries_email", "suppression_entries", ["email"])
    op.create_index("ix_suppression_entries_domain", "suppression_entries", ["domain"])
    op.create_index("ix_suppression_entries_active", "suppression_entries", ["active"])

    op.create_table(
        "email_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("queue_item_id", sa.Integer(), sa.ForeignKey("outreach_queue_items.id")),
        sa.Column("prospect_id", sa.Integer(), sa.ForeignKey("prospects.id")),
        sa.Column("to_email", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("response_excerpt", sa.Text(), nullable=False),
        sa.Column("reply_to", sa.Text(), nullable=False),
        sa.Column("unsubscribe_token", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_email_logs_queue_item_id", "email_logs", ["queue_item_id"])
    op.create_index("ix_email_logs_prospect_id", "email_logs", ["prospect_id"])
    op.create_index("ix_email_logs_status", "email_logs", ["status"])
    op.create_index("ix_email_logs_unsubscribe_token", "email_logs", ["unsubscribe_token"])

    op.create_table(
        "webhook_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("event_id", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("payload_excerpt", sa.Text(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_webhook_logs_provider", "webhook_logs", ["provider"])
    op.create_index("ix_webhook_logs_event_id", "webhook_logs", ["event_id"])
    op.create_index("ix_webhook_logs_event_type", "webhook_logs", ["event_type"])


def downgrade() -> None:
    op.drop_index("ix_webhook_logs_event_type", table_name="webhook_logs")
    op.drop_index("ix_webhook_logs_event_id", table_name="webhook_logs")
    op.drop_index("ix_webhook_logs_provider", table_name="webhook_logs")
    op.drop_table("webhook_logs")

    op.drop_index("ix_email_logs_unsubscribe_token", table_name="email_logs")
    op.drop_index("ix_email_logs_status", table_name="email_logs")
    op.drop_index("ix_email_logs_prospect_id", table_name="email_logs")
    op.drop_index("ix_email_logs_queue_item_id", table_name="email_logs")
    op.drop_table("email_logs")

    op.drop_index("ix_suppression_entries_active", table_name="suppression_entries")
    op.drop_index("ix_suppression_entries_domain", table_name="suppression_entries")
    op.drop_index("ix_suppression_entries_email", table_name="suppression_entries")
    op.drop_table("suppression_entries")

    op.drop_index("ix_outreach_queue_items_state", table_name="outreach_queue_items")
    op.drop_index("ix_outreach_queue_items_queue_date", table_name="outreach_queue_items")
    op.drop_index("ix_outreach_queue_items_prospect_id", table_name="outreach_queue_items")
    op.drop_table("outreach_queue_items")

    op.drop_index("ix_prospects_approved_for_outreach", table_name="prospects")
    op.drop_index("ix_prospects_existing_customer_id", table_name="prospects")
    op.drop_index("ix_prospects_match_status", table_name="prospects")
    op.drop_index("ix_prospects_review_status", table_name="prospects")
    op.drop_index("ix_prospects_country_code", table_name="prospects")
    op.drop_index("ix_prospects_city", table_name="prospects")
    op.drop_index("ix_prospects_website_domain", table_name="prospects")
    op.drop_index("ix_prospects_email_domain", table_name="prospects")
    op.drop_index("ix_prospects_email", table_name="prospects")
    op.drop_index("ix_prospects_canonical_name_geo_key", table_name="prospects")
    op.drop_index("ix_prospects_canonical_company_name_clean", table_name="prospects")
    op.drop_index("ix_prospects_company_name", table_name="prospects")
    op.drop_table("prospects")

    op.drop_index("ix_invoices_already_client_flag", table_name="invoices")
    op.drop_index("ix_invoices_country_code", table_name="invoices")
    op.drop_index("ix_invoices_website_domain_candidate", table_name="invoices")
    op.drop_index("ix_invoices_email_domain", table_name="invoices")
    op.drop_index("ix_invoices_customer_name_clean", table_name="invoices")
    op.drop_index("ix_invoices_source_customer_id", table_name="invoices")
    op.drop_index("ix_invoices_customer_entity_id", table_name="invoices")
    op.drop_index("ix_invoices_customer_id", table_name="invoices")
    op.drop_index("ix_invoices_invoice_id", table_name="invoices")
    op.drop_table("invoices")

    op.drop_index("ix_customers_stripe_customer_id", table_name="customers")
    op.drop_index("ix_customers_already_client_flag", table_name="customers")
    op.drop_index("ix_customers_country_code", table_name="customers")
    op.drop_index("ix_customers_city", table_name="customers")
    op.drop_index("ix_customers_website_domain_candidate", table_name="customers")
    op.drop_index("ix_customers_email_domain_primary", table_name="customers")
    op.drop_index("ix_customers_customer_email_primary", table_name="customers")
    op.drop_index("ix_customers_match_key_domain", table_name="customers")
    op.drop_index("ix_customers_canonical_name_geo_key", table_name="customers")
    op.drop_index("ix_customers_canonical_company_name_clean", table_name="customers")
    op.drop_index("ix_customers_customer_entity_id", table_name="customers")
    op.drop_table("customers")

    sa.Enum(name="queuestate").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="matchstatus").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="prospectstate").drop(op.get_bind(), checkfirst=True)
