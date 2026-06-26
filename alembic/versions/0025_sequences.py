"""DESIGN_V2 Phase 3B: 3-step cold email sequence engine

Revision ID: 0025_sequences
Revises: 0024_personalizations
Create Date: 2026-06-26 03:00:00

Additive + rollback-safe. Four new tables (email_sequences, sequence_steps,
sequence_enrollments, sequence_emails). Downgrade drops them in FK-safe order.
Nothing writes here unless settings.sequence_engine_enabled is on.
"""
from alembic import op
import sqlalchemy as sa


revision = "0025_sequences"
down_revision = "0024_personalizations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "email_sequences",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False, server_default=""),
        sa.Column("sector", sa.Text(), nullable=False, server_default="bike"),
        sa.Column("lead_type", sa.Text(), nullable=False, server_default=""),
        sa.Column("timezone_strategy", sa.Text(), nullable=False, server_default="lead_local"),
        sa.Column("cadence_rule", sa.Text(), nullable=False, server_default="weekly_wed_0700"),
        sa.Column("step_count", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("seed_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_email_sequences_name", "email_sequences", ["name"])
    op.create_index("ix_email_sequences_is_active", "email_sequences", ["is_active"])

    op.create_table(
        "sequence_steps",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sequence_id", sa.Integer(), sa.ForeignKey("email_sequences.id"), nullable=False),
        sa.Column("step_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("template_id", sa.Integer(), sa.ForeignKey("email_templates.id"), nullable=True),
        sa.Column("subject_override", sa.Text(), nullable=False, server_default=""),
        sa.Column("send_weekday", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("send_hour_local", sa.Integer(), nullable=False, server_default="7"),
        sa.Column("gap_days", sa.Integer(), nullable=False, server_default="7"),
        sa.Column("personalization_level", sa.Text(), nullable=False, server_default="light"),
        sa.UniqueConstraint("sequence_id", "step_number", name="uq_sequence_step_number"),
    )
    op.create_index("ix_sequence_steps_sequence_id", "sequence_steps", ["sequence_id"])

    op.create_table(
        "sequence_enrollments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("sequence_id", sa.Integer(), sa.ForeignKey("email_sequences.id"), nullable=False),
        sa.Column("subject_type", sa.Text(), nullable=False, server_default="kvk"),
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.Column("to_email", sa.Text(), nullable=False, server_default=""),
        sa.Column("company_name", sa.Text(), nullable=False, server_default=""),
        sa.Column("merge_context", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("current_step", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sequence_status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("stop_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("next_followup_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("timezone", sa.Text(), nullable=False, server_default="Europe/Amsterdam"),
        sa.Column("created_by", sa.Text(), nullable=False, server_default=""),
        sa.Column("enrolled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("sequence_id", "subject_type", "subject_id", name="uq_enrollment_subject"),
    )
    op.create_index("ix_sequence_enrollments_sequence_id", "sequence_enrollments", ["sequence_id"])
    op.create_index("ix_sequence_enrollments_subject_type", "sequence_enrollments", ["subject_type"])
    op.create_index("ix_sequence_enrollments_subject_id", "sequence_enrollments", ["subject_id"])
    op.create_index("ix_sequence_enrollments_to_email", "sequence_enrollments", ["to_email"])
    op.create_index("ix_sequence_enrollments_status", "sequence_enrollments", ["sequence_status"])
    op.create_index("ix_sequence_enrollments_next_followup_at", "sequence_enrollments", ["next_followup_at"])

    op.create_table(
        "sequence_emails",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("enrollment_id", sa.Integer(), sa.ForeignKey("sequence_enrollments.id"), nullable=False),
        sa.Column("step_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("campaign_id", sa.Integer(), sa.ForeignKey("email_campaigns.id"), nullable=True),
        sa.Column("recipient_id", sa.Integer(), sa.ForeignKey("email_campaign_recipients.id"), nullable=True),
        sa.Column("scheduled_send_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="scheduled"),
        sa.Column("baseline_template_version", sa.Text(), nullable=False, server_default=""),
        sa.Column("personalization_version", sa.Text(), nullable=False, server_default=""),
        sa.Column("rendered_subject", sa.Text(), nullable=False, server_default=""),
        sa.Column("rendered_body_html", sa.Text(), nullable=False, server_default=""),
        sa.Column("rendered_body_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("personalization_fields_used", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("confidence_summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_sequence_emails_enrollment_id", "sequence_emails", ["enrollment_id"])
    op.create_index("ix_sequence_emails_campaign_id", "sequence_emails", ["campaign_id"])
    op.create_index("ix_sequence_emails_status", "sequence_emails", ["status"])


def downgrade() -> None:
    op.drop_table("sequence_emails")
    op.drop_table("sequence_enrollments")
    op.drop_table("sequence_steps")
    op.drop_table("email_sequences")
