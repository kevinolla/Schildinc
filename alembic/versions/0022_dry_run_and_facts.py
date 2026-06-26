"""DESIGN_V2 foundation: dry-run keystone + enrichment_facts

Revision ID: 0022_dry_run_and_facts
Revises: 0021_open_discovery
Create Date: 2026-06-26 00:00:00

Additive + rollback-safe. Every change is a new column / new table; the
downgrade is a clean drop. With the new env flags unset, production sending
behaves exactly as before EXCEPT that newly-created campaigns start dry-run.

SAFETY — the dry-run backfill (critic fix #2): the new ``email_campaigns.dry_run``
column is added with ``server_default=false()``, so ALL existing rows
(including any campaign currently 'sending') are backfilled to FALSE and keep
sending exactly as today. Only NEW campaigns created via the app pick up the
TRUE default (and only when CAMPAIGN_DRY_RUN_DEFAULT is left at its default).

Adds:
- email_campaigns.dry_run (bool, default FALSE for existing rows)
- email_campaign_recipients.dry_run_preview_html (text)
- enrichment_facts (provenance-first discovered-fact store)
"""
from alembic import op
import sqlalchemy as sa


revision = "0022_dry_run_and_facts"
down_revision = "0021_open_discovery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- dry-run keystone ---------------------------------------------------
    # server_default=false() backfills EXISTING rows to FALSE (live campaigns
    # keep sending). The ORM stamps TRUE on new campaigns via its Python default.
    op.add_column(
        "email_campaigns",
        sa.Column("dry_run", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "email_campaign_recipients",
        sa.Column("dry_run_preview_html", sa.Text(), nullable=False, server_default=""),
    )

    # --- enrichment_facts ---------------------------------------------------
    op.create_table(
        "enrichment_facts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("subject_type", sa.Text(), nullable=False, server_default="kvk"),
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.Column("field_name", sa.Text(), nullable=False, server_default=""),
        sa.Column("extracted_value", sa.Text(), nullable=False, server_default=""),
        sa.Column("source_url", sa.Text(), nullable=False, server_default=""),
        sa.Column("extraction_method", sa.Text(), nullable=False, server_default=""),
        sa.Column("confidence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("review_required", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("reviewed_by", sa.Text(), nullable=False, server_default=""),
        sa.Column("extracted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "subject_type", "subject_id", "field_name", "source_url",
            name="uq_enrichment_fact_subject_field_source",
        ),
    )
    op.create_index("ix_enrichment_facts_subject_type", "enrichment_facts", ["subject_type"])
    op.create_index("ix_enrichment_facts_subject_id", "enrichment_facts", ["subject_id"])
    op.create_index("ix_enrichment_facts_field_name", "enrichment_facts", ["field_name"])


def downgrade() -> None:
    op.drop_index("ix_enrichment_facts_field_name", table_name="enrichment_facts")
    op.drop_index("ix_enrichment_facts_subject_id", table_name="enrichment_facts")
    op.drop_index("ix_enrichment_facts_subject_type", table_name="enrichment_facts")
    op.drop_table("enrichment_facts")
    op.drop_column("email_campaign_recipients", "dry_run_preview_html")
    op.drop_column("email_campaigns", "dry_run")
