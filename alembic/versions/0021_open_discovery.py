"""Open (non-Google) discovery provenance + email provider audit

Revision ID: 0021_open_discovery
Revises: 0020_attachments_notifications
Create Date: 2026-06-23 00:00:00

Additive + rollback-safe. Every column is nullable / server-default, so the
downgrade is a clean drop and no required data is lost. With the new env vars
unset, nothing reads these columns and behaviour is unchanged.

Adds:
- kvk_companies / prospects: open-discovery confidence + provenance
  (website_confidence, discovery_query_used, discovery_input_type,
   discovery_backend) + prospects review fields (last_discovery_attempt_at,
   match_confidence, best_match_reason)
- email_campaign_recipients: provider + provider_message_id (the Gmail path
  keeps writing gmail_message_id; other providers write the new pair)
"""
from alembic import op
import sqlalchemy as sa


revision = "0021_open_discovery"
down_revision = "0020_attachments_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # kvk_companies — open-discovery provenance trio + backend
    op.add_column("kvk_companies", sa.Column("website_confidence", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("kvk_companies", sa.Column("discovery_query_used", sa.Text(), nullable=False, server_default=""))
    op.add_column("kvk_companies", sa.Column("discovery_input_type", sa.Text(), nullable=False, server_default=""))
    op.add_column("kvk_companies", sa.Column("discovery_backend", sa.Text(), nullable=False, server_default=""))
    op.create_index("ix_kvk_companies_website_confidence", "kvk_companies", ["website_confidence"])

    # prospects — same provenance + review fields (mirror KvkCompany)
    op.add_column("prospects", sa.Column("website_confidence", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("prospects", sa.Column("discovery_query_used", sa.Text(), nullable=False, server_default=""))
    op.add_column("prospects", sa.Column("discovery_input_type", sa.Text(), nullable=False, server_default=""))
    op.add_column("prospects", sa.Column("discovery_backend", sa.Text(), nullable=False, server_default=""))
    op.add_column("prospects", sa.Column("last_discovery_attempt_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("prospects", sa.Column("match_confidence", sa.Text(), nullable=False, server_default=""))
    op.add_column("prospects", sa.Column("best_match_reason", sa.Text(), nullable=False, server_default=""))
    op.create_index("ix_prospects_website_confidence", "prospects", ["website_confidence"])

    # email_campaign_recipients — provider audit
    op.add_column("email_campaign_recipients", sa.Column("provider", sa.Text(), nullable=False, server_default=""))
    op.add_column("email_campaign_recipients", sa.Column("provider_message_id", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("email_campaign_recipients", "provider_message_id")
    op.drop_column("email_campaign_recipients", "provider")
    op.drop_index("ix_prospects_website_confidence", table_name="prospects")
    for col in ["best_match_reason", "match_confidence", "last_discovery_attempt_at",
                "discovery_backend", "discovery_input_type", "discovery_query_used", "website_confidence"]:
        op.drop_column("prospects", col)
    op.drop_index("ix_kvk_companies_website_confidence", table_name="kvk_companies")
    for col in ["discovery_backend", "discovery_input_type", "discovery_query_used", "website_confidence"]:
        op.drop_column("kvk_companies", col)
