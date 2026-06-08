"""In-house CRM Phase 1 — unified Contact Hub

Revision ID: 0014_contact_hub
Revises: 0013_email_engine
Create Date: 2026-06-05 00:00:00

Adds the master `contacts` identity table (merging Customer + KVK + Lead +
Prospect), the `contact_channels` table (email/phone/whatsapp/social values),
and the `activities` unified timeline table.
"""
from alembic import op
import sqlalchemy as sa


revision = "0014_contact_hub"
down_revision = "0013_email_engine"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "contacts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("display_name", sa.Text(), nullable=False, server_default=""),
        sa.Column("company_name", sa.Text(), nullable=False, server_default=""),
        sa.Column("contact_person", sa.Text(), nullable=False, server_default=""),
        sa.Column("primary_email", sa.Text(), nullable=False, server_default=""),
        sa.Column("primary_phone", sa.Text(), nullable=False, server_default=""),
        sa.Column("city", sa.Text(), nullable=False, server_default=""),
        sa.Column("country_code", sa.Text(), nullable=False, server_default=""),
        sa.Column("sector", sa.Text(), nullable=False, server_default=""),
        sa.Column("tier", sa.Text(), nullable=False, server_default=""),
        sa.Column("website", sa.Text(), nullable=False, server_default=""),
        sa.Column("lifetime_value", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("is_customer", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("do_not_contact", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=True),
        sa.Column("kvk_company_id", sa.Integer(), sa.ForeignKey("kvk_companies.id"), nullable=True),
        sa.Column("facebook_lead_id", sa.Integer(), sa.ForeignKey("facebook_leads.id"), nullable=True),
        sa.Column("prospect_id", sa.Integer(), sa.ForeignKey("prospects.id"), nullable=True),
        sa.Column("source_summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("owner_agent_id", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    for col in ["display_name", "company_name", "primary_email", "primary_phone",
                "city", "country_code", "sector", "tier", "is_customer",
                "do_not_contact", "customer_id", "kvk_company_id",
                "facebook_lead_id", "prospect_id", "source_summary",
                "owner_agent_id", "last_activity_at"]:
        op.create_index(f"ix_contacts_{col}", "contacts", [col])

    op.create_table(
        "contact_channels",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("contact_id", sa.Integer(), sa.ForeignKey("contacts.id"), nullable=False),
        sa.Column("channel_type", sa.Text(), nullable=False, server_default="email"),
        sa.Column("value", sa.Text(), nullable=False, server_default=""),
        sa.Column("value_normalized", sa.Text(), nullable=False, server_default=""),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("source", sa.Text(), nullable=False, server_default=""),
        sa.Column("label", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("contact_id", "channel_type", "value_normalized", name="uq_contact_channel_value"),
    )
    op.create_index("ix_contact_channels_contact_id", "contact_channels", ["contact_id"])
    op.create_index("ix_contact_channels_channel_type", "contact_channels", ["channel_type"])
    op.create_index("ix_contact_channels_value_normalized", "contact_channels", ["value_normalized"])

    op.create_table(
        "activities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("contact_id", sa.Integer(), sa.ForeignKey("contacts.id"), nullable=False),
        sa.Column("activity_type", sa.Text(), nullable=False, server_default=""),
        sa.Column("channel", sa.Text(), nullable=False, server_default="system"),
        sa.Column("direction", sa.Text(), nullable=False, server_default="none"),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("agent_id", sa.Integer(), nullable=True),
        sa.Column("ref_type", sa.Text(), nullable=False, server_default=""),
        sa.Column("ref_id", sa.Integer(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_activities_contact_id", "activities", ["contact_id"])
    op.create_index("ix_activities_activity_type", "activities", ["activity_type"])
    op.create_index("ix_activities_occurred_at", "activities", ["occurred_at"])


def downgrade() -> None:
    op.drop_table("activities")
    op.drop_table("contact_channels")
    op.drop_table("contacts")
