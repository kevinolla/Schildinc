"""Add WhatsApp / Instagram / LinkedIn columns to kvk_companies

Revision ID: 0007_kvk_social_contacts
Revises: 0006_kvk_prospect_fields
Create Date: 2026-05-15 00:00:00

These columns let the local browser agent (scripts/email_agent.py)
persist phone / WhatsApp / Instagram / LinkedIn alongside email when
scraping Google results for KVK companies. Schema mirrors the
equivalent fields on the prospects table so the rest of the app can
treat them uniformly.
"""
from alembic import op
import sqlalchemy as sa


revision = "0007_kvk_social_contacts"
down_revision = "0006_kvk_prospect_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # All Text with default "" so existing rows fill in cleanly without
    # backfill. No indexes — we filter/display, never join on these.
    op.add_column(
        "kvk_companies",
        sa.Column("whatsapp_number", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "kvk_companies",
        sa.Column("whatsapp_url", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "kvk_companies",
        sa.Column("instagram_url", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "kvk_companies",
        sa.Column("linkedin_url", sa.Text(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("kvk_companies", "linkedin_url")
    op.drop_column("kvk_companies", "instagram_url")
    op.drop_column("kvk_companies", "whatsapp_url")
    op.drop_column("kvk_companies", "whatsapp_number")
