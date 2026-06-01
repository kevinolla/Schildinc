"""Add main_sector + sub_sector to facebook_leads + classifier flag

Revision ID: 0012_fb_leads_sector
Revises: 0011_kvk_search_attempts
Create Date: 2026-06-01 00:00:00

The lead classifier (app/lead_classifier.py) needs a column to write
into. Plus a `classifier_version` so we can re-run when the keyword
dictionary changes without touching already-classified rows.
"""
from alembic import op
import sqlalchemy as sa


revision = "0012_fb_leads_sector"
down_revision = "0011_kvk_search_attempts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "facebook_leads",
        sa.Column("main_sector", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "facebook_leads",
        sa.Column("sub_sector", sa.Text(), nullable=False, server_default=""),
    )
    # 0 = never classified, 1 = first classifier version etc.
    op.add_column(
        "facebook_leads",
        sa.Column("classifier_version", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index(
        "ix_facebook_leads_main_sector", "facebook_leads", ["main_sector"]
    )
    op.create_index(
        "ix_facebook_leads_classifier_version",
        "facebook_leads",
        ["classifier_version"],
    )


def downgrade() -> None:
    op.drop_index("ix_facebook_leads_classifier_version", table_name="facebook_leads")
    op.drop_index("ix_facebook_leads_main_sector", table_name="facebook_leads")
    op.drop_column("facebook_leads", "classifier_version")
    op.drop_column("facebook_leads", "sub_sector")
    op.drop_column("facebook_leads", "main_sector")
