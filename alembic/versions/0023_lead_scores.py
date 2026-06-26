"""DESIGN_V2 Phase 2: lead_scores

Revision ID: 0023_lead_scores
Revises: 0022_dry_run_and_facts
Create Date: 2026-06-26 01:00:00

Additive + rollback-safe. A single new table; downgrade is a clean drop.
Nothing writes here unless settings.lead_scoring_enabled is on, so with the
flag unset this migration changes no behaviour.
"""
from alembic import op
import sqlalchemy as sa


revision = "0023_lead_scores"
down_revision = "0022_dry_run_and_facts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "lead_scores",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("subject_type", sa.Text(), nullable=False, server_default="kvk"),
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.Column("store_quality_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("commercial_potential", sa.Text(), nullable=False, server_default=""),
        sa.Column("outreach_priority", sa.Text(), nullable=False, server_default=""),
        sa.Column("sample_pack_eligibility", sa.Text(), nullable=False, server_default=""),
        sa.Column("call_followup_eligibility", sa.Text(), nullable=False, server_default=""),
        sa.Column("bike_tier", sa.Text(), nullable=False, server_default=""),
        sa.Column("reasons", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("engine_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("input_fingerprint", sa.Text(), nullable=False, server_default=""),
        sa.Column("manual_override", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reviewed_by", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("subject_type", "subject_id", name="uq_lead_score_subject"),
    )
    op.create_index("ix_lead_scores_subject_type", "lead_scores", ["subject_type"])
    op.create_index("ix_lead_scores_subject_id", "lead_scores", ["subject_id"])
    op.create_index("ix_lead_scores_outreach_priority", "lead_scores", ["outreach_priority"])


def downgrade() -> None:
    op.drop_index("ix_lead_scores_outreach_priority", table_name="lead_scores")
    op.drop_index("ix_lead_scores_subject_id", table_name="lead_scores")
    op.drop_index("ix_lead_scores_subject_type", table_name="lead_scores")
    op.drop_table("lead_scores")
