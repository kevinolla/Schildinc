"""Add KVK companies, establishments, and import log tables

Revision ID: 0005_add_kvk_tables
Revises: 0004_discovery_lists
Create Date: 2026-05-05 00:00:00
"""

from alembic import op
import sqlalchemy as sa

revision = "0005_add_kvk_tables"
down_revision = "0004_discovery_lists"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "kvk_companies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_system", sa.Text(), nullable=False, server_default="kvk_bike_list"),
        sa.Column("source_file", sa.Text(), nullable=False, server_default=""),
        sa.Column("company_entity_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("record_type", sa.Text(), nullable=False, server_default="company"),
        sa.Column("kvk_number", sa.Text(), nullable=False),
        sa.Column("company_name", sa.Text(), nullable=False),
        sa.Column("canonical_company_name_clean", sa.Text(), nullable=False, server_default=""),
        sa.Column("search_company_name", sa.Text(), nullable=False, server_default=""),
        sa.Column("main_activity_code", sa.Integer(), nullable=True),
        sa.Column("main_activity_description", sa.Text(), nullable=False, server_default=""),
        sa.Column("date_of_establishment", sa.Date(), nullable=True),
        sa.Column("country_code", sa.Text(), nullable=False, server_default="NL"),
        sa.Column("province_code", sa.Text(), nullable=False, server_default=""),
        sa.Column("establishments_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("primary_establishment_number", sa.Text(), nullable=False, server_default=""),
        sa.Column("primary_city", sa.Text(), nullable=False, server_default=""),
        sa.Column("primary_postal_code", sa.Text(), nullable=False, server_default=""),
        sa.Column("primary_address", sa.Text(), nullable=False, server_default=""),
        sa.Column("website", sa.Text(), nullable=False, server_default=""),
        sa.Column("website_domain", sa.Text(), nullable=False, server_default=""),
        sa.Column("email_public", sa.Text(), nullable=False, server_default=""),
        sa.Column("phone_public", sa.Text(), nullable=False, server_default=""),
        sa.Column("email_source_url", sa.Text(), nullable=False, server_default=""),
        sa.Column("phone_source_url", sa.Text(), nullable=False, server_default=""),
        sa.Column("email_confidence", sa.Text(), nullable=False, server_default=""),
        sa.Column("phone_confidence", sa.Text(), nullable=False, server_default=""),
        sa.Column("enrichment_status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("google_maps_query", sa.Text(), nullable=False, server_default=""),
        sa.Column("contact_search_query", sa.Text(), nullable=False, server_default=""),
        sa.Column("last_enrichment_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("already_client_flag", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("client_match_status", sa.Text(), nullable=False, server_default="unknown"),
        sa.Column("matched_customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=True),
        sa.Column("match_confidence", sa.Text(), nullable=False, server_default=""),
        sa.Column("best_match_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("bike_shop_tier", sa.Text(), nullable=False, server_default="Unclassified"),
        sa.Column("bike_shop_segment", sa.Text(), nullable=False, server_default=""),
        sa.Column("outreach_priority", sa.Text(), nullable=False, server_default=""),
        sa.Column("tier_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("headquarters_required", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("franchise_or_buying_group", sa.Text(), nullable=False, server_default=""),
        sa.Column("recommended_sales_angle", sa.Text(), nullable=False, server_default=""),
        sa.Column("recommended_contact_type", sa.Text(), nullable=False, server_default=""),
        sa.Column("approved_for_outreach", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("kvk_number", name="uq_kvk_company_number"),
        sa.UniqueConstraint("company_entity_id", name="uq_kvk_company_entity_id"),
    )
    op.create_index("ix_kvk_companies_kvk_number", "kvk_companies", ["kvk_number"])
    op.create_index("ix_kvk_companies_company_entity_id", "kvk_companies", ["company_entity_id"])
    op.create_index("ix_kvk_companies_canonical_company_name_clean", "kvk_companies", ["canonical_company_name_clean"])
    op.create_index("ix_kvk_companies_primary_city", "kvk_companies", ["primary_city"])
    op.create_index("ix_kvk_companies_country_code", "kvk_companies", ["country_code"])
    op.create_index("ix_kvk_companies_enrichment_status", "kvk_companies", ["enrichment_status"])
    op.create_index("ix_kvk_companies_already_client_flag", "kvk_companies", ["already_client_flag"])
    op.create_index("ix_kvk_companies_client_match_status", "kvk_companies", ["client_match_status"])
    op.create_index("ix_kvk_companies_bike_shop_tier", "kvk_companies", ["bike_shop_tier"])
    op.create_index("ix_kvk_companies_approved_for_outreach", "kvk_companies", ["approved_for_outreach"])
    op.create_index("ix_kvk_companies_email_public", "kvk_companies", ["email_public"])
    op.create_index("ix_kvk_companies_website_domain", "kvk_companies", ["website_domain"])

    op.create_table(
        "kvk_establishments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_system", sa.Text(), nullable=False, server_default="kvk_bike_list"),
        sa.Column("source_file", sa.Text(), nullable=False, server_default=""),
        sa.Column("record_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("record_type", sa.Text(), nullable=False, server_default="establishment"),
        sa.Column("kvk_number", sa.Text(), nullable=False),
        sa.Column("establishment_number", sa.Text(), nullable=False, server_default=""),
        sa.Column("company_id", sa.Integer(), sa.ForeignKey("kvk_companies.id"), nullable=True),
        sa.Column("company_name_raw", sa.Text(), nullable=False, server_default=""),
        sa.Column("company_name", sa.Text(), nullable=False),
        sa.Column("canonical_company_name_clean", sa.Text(), nullable=False, server_default=""),
        sa.Column("search_company_name", sa.Text(), nullable=False, server_default=""),
        sa.Column("main_activity_code", sa.Integer(), nullable=True),
        sa.Column("main_activity_description", sa.Text(), nullable=False, server_default=""),
        sa.Column("date_of_establishment", sa.Date(), nullable=True),
        sa.Column("country_code", sa.Text(), nullable=False, server_default="NL"),
        sa.Column("province_code", sa.Text(), nullable=False, server_default=""),
        sa.Column("non_mailing_indicator", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("visiting_street", sa.Text(), nullable=False, server_default=""),
        sa.Column("visiting_house_number", sa.Text(), nullable=False, server_default=""),
        sa.Column("visiting_house_letter", sa.Text(), nullable=False, server_default=""),
        sa.Column("visiting_house_number_addition", sa.Text(), nullable=False, server_default=""),
        sa.Column("visiting_location_addition", sa.Text(), nullable=False, server_default=""),
        sa.Column("visiting_postal_code", sa.Text(), nullable=False, server_default=""),
        sa.Column("visiting_city", sa.Text(), nullable=False, server_default=""),
        sa.Column("visiting_municipality_code", sa.Text(), nullable=False, server_default=""),
        sa.Column("visiting_municipality_name", sa.Text(), nullable=False, server_default=""),
        sa.Column("postal_street", sa.Text(), nullable=False, server_default=""),
        sa.Column("postal_house_number", sa.Text(), nullable=False, server_default=""),
        sa.Column("postal_house_letter", sa.Text(), nullable=False, server_default=""),
        sa.Column("postal_house_number_addition", sa.Text(), nullable=False, server_default=""),
        sa.Column("postal_location_addition", sa.Text(), nullable=False, server_default=""),
        sa.Column("postal_postal_code", sa.Text(), nullable=False, server_default=""),
        sa.Column("postal_city", sa.Text(), nullable=False, server_default=""),
        sa.Column("postal_municipality_code", sa.Text(), nullable=False, server_default=""),
        sa.Column("postal_municipality_name", sa.Text(), nullable=False, server_default=""),
        sa.Column("full_visiting_address", sa.Text(), nullable=False, server_default=""),
        sa.Column("full_postal_address", sa.Text(), nullable=False, server_default=""),
        sa.Column("website", sa.Text(), nullable=False, server_default=""),
        sa.Column("website_domain", sa.Text(), nullable=False, server_default=""),
        sa.Column("email_public", sa.Text(), nullable=False, server_default=""),
        sa.Column("phone_public", sa.Text(), nullable=False, server_default=""),
        sa.Column("email_source_url", sa.Text(), nullable=False, server_default=""),
        sa.Column("phone_source_url", sa.Text(), nullable=False, server_default=""),
        sa.Column("email_confidence", sa.Text(), nullable=False, server_default=""),
        sa.Column("phone_confidence", sa.Text(), nullable=False, server_default=""),
        sa.Column("enrichment_status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("google_maps_query", sa.Text(), nullable=False, server_default=""),
        sa.Column("contact_search_query", sa.Text(), nullable=False, server_default=""),
        sa.Column("has_multiple_establishments", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("establishments_per_kvk", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("already_client_flag", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("client_match_status", sa.Text(), nullable=False, server_default="unknown"),
        sa.Column("matched_customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=True),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("record_id", name="uq_kvk_establishment_record_id"),
        sa.UniqueConstraint("kvk_number", "establishment_number", name="uq_kvk_establishment"),
    )
    op.create_index("ix_kvk_establishments_record_id", "kvk_establishments", ["record_id"])
    op.create_index("ix_kvk_establishments_kvk_number", "kvk_establishments", ["kvk_number"])
    op.create_index("ix_kvk_establishments_establishment_number", "kvk_establishments", ["establishment_number"])
    op.create_index("ix_kvk_establishments_company_id", "kvk_establishments", ["company_id"])
    op.create_index("ix_kvk_establishments_visiting_city", "kvk_establishments", ["visiting_city"])
    op.create_index("ix_kvk_establishments_country_code", "kvk_establishments", ["country_code"])
    op.create_index("ix_kvk_establishments_enrichment_status", "kvk_establishments", ["enrichment_status"])
    op.create_index("ix_kvk_establishments_canonical_company_name_clean", "kvk_establishments", ["canonical_company_name_clean"])

    op.create_table(
        "kvk_import_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("import_batch_id", sa.Text(), nullable=False),
        sa.Column("file_name", sa.Text(), nullable=False, server_default=""),
        sa.Column("record_type", sa.Text(), nullable=False, server_default=""),
        sa.Column("row_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("successful_upserts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.Text(), nullable=False, server_default="in_progress"),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_kvk_import_logs_import_batch_id", "kvk_import_logs", ["import_batch_id"])


def downgrade() -> None:
    op.drop_table("kvk_import_logs")
    op.drop_table("kvk_establishments")
    op.drop_table("kvk_companies")
