"""Email engine — templates, campaigns, recipients, events, Gmail account

Revision ID: 0013_email_engine
Revises: 0012_fb_leads_sector
Create Date: 2026-06-02 00:00:00

Adds the Gmail-backed email engine: reusable templates, campaigns with a
selected audience (KVK / leads / customers), per-recipient open/click/
unsubscribe tracking, a raw event log, and a singleton table holding the
connected Gmail OAuth credentials (Railway's filesystem is ephemeral).
"""
from alembic import op
import sqlalchemy as sa


revision = "0013_email_engine"
down_revision = "0012_fb_leads_sector"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "email_templates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False, server_default="custom"),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("subject", sa.Text(), nullable=False, server_default=""),
        sa.Column("body_html", sa.Text(), nullable=False, server_default=""),
        sa.Column("body_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("merge_fields", sa.Text(), nullable=False, server_default=""),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_starter", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("seed_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_email_templates_name", "email_templates", ["name"])
    op.create_index("ix_email_templates_category", "email_templates", ["category"])
    op.create_index("ix_email_templates_is_active", "email_templates", ["is_active"])

    op.create_table(
        "email_campaigns",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("template_id", sa.Integer(), sa.ForeignKey("email_templates.id"), nullable=True),
        sa.Column("subject", sa.Text(), nullable=False, server_default=""),
        sa.Column("body_html", sa.Text(), nullable=False, server_default=""),
        sa.Column("body_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("audience_type", sa.Text(), nullable=False, server_default="kvk"),
        sa.Column("lead_temperature", sa.Text(), nullable=False, server_default="cold"),
        sa.Column("status", sa.Text(), nullable=False, server_default="draft"),
        sa.Column("sender_alias", sa.Text(), nullable=False, server_default=""),
        sa.Column("sender_name", sa.Text(), nullable=False, server_default=""),
        sa.Column("reply_to", sa.Text(), nullable=False, server_default=""),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_recipients", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sent_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("open_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("click_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unsubscribe_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bounce_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by", sa.Text(), nullable=False, server_default=""),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_email_campaigns_name", "email_campaigns", ["name"])
    op.create_index("ix_email_campaigns_template_id", "email_campaigns", ["template_id"])
    op.create_index("ix_email_campaigns_audience_type", "email_campaigns", ["audience_type"])
    op.create_index("ix_email_campaigns_lead_temperature", "email_campaigns", ["lead_temperature"])
    op.create_index("ix_email_campaigns_status", "email_campaigns", ["status"])
    op.create_index("ix_email_campaigns_scheduled_at", "email_campaigns", ["scheduled_at"])

    op.create_table(
        "email_campaign_recipients",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("campaign_id", sa.Integer(), sa.ForeignKey("email_campaigns.id"), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False, server_default="kvk"),
        sa.Column("kvk_company_id", sa.Integer(), sa.ForeignKey("kvk_companies.id"), nullable=True),
        sa.Column("facebook_lead_id", sa.Integer(), sa.ForeignKey("facebook_leads.id"), nullable=True),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id"), nullable=True),
        sa.Column("to_email", sa.Text(), nullable=False, server_default=""),
        sa.Column("company_name", sa.Text(), nullable=False, server_default=""),
        sa.Column("contact_name", sa.Text(), nullable=False, server_default=""),
        sa.Column("merge_data", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("tracking_token", sa.Text(), nullable=False, server_default=""),
        sa.Column("gmail_message_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("open_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("click_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("first_opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_clicked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("unsubscribed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("campaign_id", "to_email", name="uq_campaign_recipient_email"),
    )
    op.create_index("ix_ecr_campaign_id", "email_campaign_recipients", ["campaign_id"])
    op.create_index("ix_ecr_source_type", "email_campaign_recipients", ["source_type"])
    op.create_index("ix_ecr_kvk_company_id", "email_campaign_recipients", ["kvk_company_id"])
    op.create_index("ix_ecr_facebook_lead_id", "email_campaign_recipients", ["facebook_lead_id"])
    op.create_index("ix_ecr_customer_id", "email_campaign_recipients", ["customer_id"])
    op.create_index("ix_ecr_to_email", "email_campaign_recipients", ["to_email"])
    op.create_index("ix_ecr_status", "email_campaign_recipients", ["status"])
    op.create_index("ix_ecr_tracking_token", "email_campaign_recipients", ["tracking_token"], unique=True)

    op.create_table(
        "email_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("recipient_id", sa.Integer(), sa.ForeignKey("email_campaign_recipients.id"), nullable=True),
        sa.Column("campaign_id", sa.Integer(), sa.ForeignKey("email_campaigns.id"), nullable=True),
        sa.Column("event_type", sa.Text(), nullable=False, server_default=""),
        sa.Column("url", sa.Text(), nullable=False, server_default=""),
        sa.Column("user_agent", sa.Text(), nullable=False, server_default=""),
        sa.Column("ip_address", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_email_events_recipient_id", "email_events", ["recipient_id"])
    op.create_index("ix_email_events_campaign_id", "email_events", ["campaign_id"])
    op.create_index("ix_email_events_event_type", "email_events", ["event_type"])
    op.create_index("ix_email_events_created_at", "email_events", ["created_at"])

    op.create_table(
        "gmail_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_email", sa.Text(), nullable=False, server_default=""),
        sa.Column("token_json", sa.Text(), nullable=False, server_default=""),
        sa.Column("scopes", sa.Text(), nullable=False, server_default=""),
        sa.Column("send_as_aliases", sa.Text(), nullable=False, server_default=""),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
    )
    op.create_index("ix_gmail_accounts_account_email", "gmail_accounts", ["account_email"])
    op.create_index("ix_gmail_accounts_is_active", "gmail_accounts", ["is_active"])


def downgrade() -> None:
    op.drop_table("gmail_accounts")
    op.drop_table("email_events")
    op.drop_table("email_campaign_recipients")
    op.drop_table("email_campaigns")
    op.drop_table("email_templates")
