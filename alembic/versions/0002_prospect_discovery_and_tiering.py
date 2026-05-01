"""Add prospect discovery, tiering, and richer outreach fields

Revision ID: 0002_discovery_tier
Revises: 0001_initial_schema
Create Date: 2026-05-01 00:00:01
"""

from alembic import op
import sqlalchemy as sa


revision = "0002_discovery_tier"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("prospects", sa.Column("email_discovery_status", sa.Text(), nullable=False, server_default="not_started"))
    op.add_column("prospects", sa.Column("email_source_page", sa.Text(), nullable=False, server_default=""))
    op.add_column("prospects", sa.Column("email_confidence", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("prospects", sa.Column("email_discovered_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("prospects", sa.Column("linkedin_url", sa.Text(), nullable=False, server_default=""))
    op.add_column("prospects", sa.Column("instagram_url", sa.Text(), nullable=False, server_default=""))
    op.add_column("prospects", sa.Column("social_discovered_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("prospects", sa.Column("website_summary", sa.Text(), nullable=False, server_default=""))
    op.add_column("prospects", sa.Column("discovery_highlights", sa.Text(), nullable=False, server_default=""))
    op.add_column("prospects", sa.Column("discovery_error", sa.Text(), nullable=False, server_default=""))
    op.add_column("prospects", sa.Column("bike_shop_tier", sa.Text(), nullable=False, server_default="Unclassified"))
    op.add_column("prospects", sa.Column("bike_shop_segment", sa.Text(), nullable=False, server_default=""))
    op.add_column("prospects", sa.Column("outreach_priority", sa.Text(), nullable=False, server_default="Manual Review"))
    op.add_column("prospects", sa.Column("headquarters_required", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("prospects", sa.Column("franchise_or_buying_group", sa.Text(), nullable=False, server_default=""))
    op.add_column("prospects", sa.Column("tier_reason", sa.Text(), nullable=False, server_default=""))
    op.add_column("prospects", sa.Column("recommended_sales_angle", sa.Text(), nullable=False, server_default=""))
    op.add_column("prospects", sa.Column("recommended_contact_type", sa.Text(), nullable=False, server_default=""))
    op.add_column("prospects", sa.Column("custom_use_case", sa.Text(), nullable=False, server_default=""))
    op.add_column("prospects", sa.Column("proof_line", sa.Text(), nullable=False, server_default=""))
    op.add_column("prospects", sa.Column("manual_tier_override", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("prospects", sa.Column("last_contacted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("prospects", sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=True))

    op.create_index("ix_prospects_email_discovery_status", "prospects", ["email_discovery_status"])
    op.create_index("ix_prospects_email_confidence", "prospects", ["email_confidence"])
    op.create_index("ix_prospects_bike_shop_tier", "prospects", ["bike_shop_tier"])
    op.create_index("ix_prospects_outreach_priority", "prospects", ["outreach_priority"])
    op.create_index("ix_prospects_headquarters_required", "prospects", ["headquarters_required"])
    op.create_index("ix_prospects_manual_tier_override", "prospects", ["manual_tier_override"])
    op.create_index("ix_prospects_cooldown_until", "prospects", ["cooldown_until"])

    op.add_column("outreach_queue_items", sa.Column("channel", sa.Text(), nullable=False, server_default="email"))
    op.add_column("outreach_queue_items", sa.Column("campaign_name", sa.Text(), nullable=False, server_default="default"))
    op.add_column("outreach_queue_items", sa.Column("body_html", sa.Text(), nullable=False, server_default=""))
    op.add_column("outreach_queue_items", sa.Column("dry_run", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_index("ix_outreach_queue_items_channel", "outreach_queue_items", ["channel"])

    op.add_column("email_logs", sa.Column("channel", sa.Text(), nullable=False, server_default="email"))
    op.add_column("email_logs", sa.Column("html_excerpt", sa.Text(), nullable=False, server_default=""))
    op.create_index("ix_email_logs_channel", "email_logs", ["channel"])

    op.create_table(
        "prospect_activity_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("prospect_id", sa.Integer(), sa.ForeignKey("prospects.id"), nullable=True),
        sa.Column("action_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_prospect_activity_logs_prospect_id", "prospect_activity_logs", ["prospect_id"])
    op.create_index("ix_prospect_activity_logs_action_type", "prospect_activity_logs", ["action_type"])
    op.create_index("ix_prospect_activity_logs_status", "prospect_activity_logs", ["status"])

    op.execute("UPDATE prospects SET email_discovery_status = 'imported' WHERE email <> ''")


def downgrade() -> None:
    op.drop_index("ix_prospect_activity_logs_status", table_name="prospect_activity_logs")
    op.drop_index("ix_prospect_activity_logs_action_type", table_name="prospect_activity_logs")
    op.drop_index("ix_prospect_activity_logs_prospect_id", table_name="prospect_activity_logs")
    op.drop_table("prospect_activity_logs")

    op.drop_index("ix_email_logs_channel", table_name="email_logs")
    op.drop_column("email_logs", "html_excerpt")
    op.drop_column("email_logs", "channel")

    op.drop_index("ix_outreach_queue_items_channel", table_name="outreach_queue_items")
    op.drop_column("outreach_queue_items", "dry_run")
    op.drop_column("outreach_queue_items", "body_html")
    op.drop_column("outreach_queue_items", "campaign_name")
    op.drop_column("outreach_queue_items", "channel")

    op.drop_index("ix_prospects_cooldown_until", table_name="prospects")
    op.drop_index("ix_prospects_manual_tier_override", table_name="prospects")
    op.drop_index("ix_prospects_headquarters_required", table_name="prospects")
    op.drop_index("ix_prospects_outreach_priority", table_name="prospects")
    op.drop_index("ix_prospects_bike_shop_tier", table_name="prospects")
    op.drop_index("ix_prospects_email_confidence", table_name="prospects")
    op.drop_index("ix_prospects_email_discovery_status", table_name="prospects")
    op.drop_column("prospects", "cooldown_until")
    op.drop_column("prospects", "last_contacted_at")
    op.drop_column("prospects", "manual_tier_override")
    op.drop_column("prospects", "proof_line")
    op.drop_column("prospects", "custom_use_case")
    op.drop_column("prospects", "recommended_contact_type")
    op.drop_column("prospects", "recommended_sales_angle")
    op.drop_column("prospects", "tier_reason")
    op.drop_column("prospects", "franchise_or_buying_group")
    op.drop_column("prospects", "headquarters_required")
    op.drop_column("prospects", "outreach_priority")
    op.drop_column("prospects", "bike_shop_segment")
    op.drop_column("prospects", "bike_shop_tier")
    op.drop_column("prospects", "discovery_error")
    op.drop_column("prospects", "discovery_highlights")
    op.drop_column("prospects", "website_summary")
    op.drop_column("prospects", "social_discovered_at")
    op.drop_column("prospects", "instagram_url")
    op.drop_column("prospects", "linkedin_url")
    op.drop_column("prospects", "email_discovered_at")
    op.drop_column("prospects", "email_confidence")
    op.drop_column("prospects", "email_source_page")
    op.drop_column("prospects", "email_discovery_status")
