"""DESIGN_V2 Phase 3A: personalizations

Revision ID: 0024_personalizations
Revises: 0023_lead_scores
Create Date: 2026-06-26 02:00:00

Additive + rollback-safe. One new table; downgrade is a clean drop. Nothing
writes here unless settings.personalization_enabled is on, so with the flag
unset this migration changes no behaviour.
"""
from alembic import op
import sqlalchemy as sa


revision = "0024_personalizations"
down_revision = "0023_lead_scores"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "personalizations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("subject_type", sa.Text(), nullable=False, server_default="kvk"),
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.Column("sequence_step", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("first_line_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("primary_angle", sa.Text(), nullable=False, server_default=""),
        sa.Column("supporting_fact", sa.Text(), nullable=False, server_default=""),
        sa.Column("cta_variant", sa.Text(), nullable=False, server_default=""),
        sa.Column("internal_sales_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("personalization_confidence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source_summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("facts_used", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("model_used", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.Text(), nullable=False, server_default="draft"),
        sa.Column("input_fingerprint", sa.Text(), nullable=False, server_default=""),
        sa.Column("reviewed_by", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("subject_type", "subject_id", "sequence_step", name="uq_personalization_subject_step"),
    )
    op.create_index("ix_personalizations_subject_type", "personalizations", ["subject_type"])
    op.create_index("ix_personalizations_subject_id", "personalizations", ["subject_id"])
    op.create_index("ix_personalizations_status", "personalizations", ["status"])


def downgrade() -> None:
    op.drop_index("ix_personalizations_status", table_name="personalizations")
    op.drop_index("ix_personalizations_subject_id", table_name="personalizations")
    op.drop_index("ix_personalizations_subject_type", table_name="personalizations")
    op.drop_table("personalizations")
