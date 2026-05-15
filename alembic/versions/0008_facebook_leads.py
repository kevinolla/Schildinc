"""Add facebook_leads table

Revision ID: 0008_facebook_leads
Revises: 0007_kvk_social_contacts
Create Date: 2026-05-15 10:00:00

Stores leads imported from the Facebook Lead Ads spreadsheet:
  https://docs.google.com/spreadsheets/d/10k2UB3qefKvskF1YemikhVCPk0JI8xmScH2dj_I7h5g
"""
from alembic import op
import sqlalchemy as sa


revision = "0008_facebook_leads"
down_revision = "0007_kvk_social_contacts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "facebook_leads",
        sa.Column("id", sa.Integer(), primary_key=True),
        # Facebook's lead id e.g. "l:2929211493944045" — unique across all forms
        sa.Column("fb_lead_id", sa.Text(), nullable=False, unique=True, index=True),
        sa.Column("created_time_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ad_name", sa.Text(), default=""),
        sa.Column("adset_name", sa.Text(), default=""),
        sa.Column("campaign_name", sa.Text(), default=""),
        sa.Column("form_name", sa.Text(), default=""),
        sa.Column("platform", sa.Text(), default=""),
        sa.Column("is_organic", sa.Boolean(), default=False),
        # Customer-identifying fields (the only data we actually outreach on)
        sa.Column("full_name", sa.Text(), default="", index=True),
        sa.Column("email", sa.Text(), default="", index=True),
        sa.Column("phone_number", sa.Text(), default=""),
        sa.Column("company_name", sa.Text(), default="", index=True),
        # Form survey answers — most useful for sectoring + LTV estimation
        sa.Column("industry", sa.Text(), default="", index=True),
        sa.Column("estimated_order_size", sa.Text(), default=""),
        # Lead lifecycle
        sa.Column("lead_status", sa.Text(), default="", index=True),
        # Cross-references populated when we run dedup
        sa.Column("matched_customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=True, index=True),
        sa.Column("matched_kvk_company_id", sa.Integer(), sa.ForeignKey("kvk_companies.id"), nullable=True, index=True),
        sa.Column("match_status", sa.Text(), default="new", index=True),  # new | existing_customer | known_prospect
        # Audit
        sa.Column("source_url", sa.Text(), default=""),
        sa.Column("raw_row", sa.Text(), default=""),  # original CSV row for debugging
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("facebook_leads")
