"""In-house CRM Phase 3 — WhatsApp templates registry

Revision ID: 0016_whatsapp
Revises: 0015_inbox
Create Date: 2026-06-05 01:00:00

WhatsApp send/receive reuses the existing `conversations` + `messages` tables
(channel='whatsapp'). This migration only adds `whatsapp_templates`, a small
registry of Meta-approved template names operators can pick when messaging a
contact outside the 24-hour service window.
"""
from alembic import op
import sqlalchemy as sa


revision = "0016_whatsapp"
down_revision = "0015_inbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "whatsapp_templates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False, server_default=""),
        sa.Column("language", sa.Text(), nullable=False, server_default="en"),
        sa.Column("category", sa.Text(), nullable=False, server_default=""),
        sa.Column("body_preview", sa.Text(), nullable=False, server_default=""),
        sa.Column("param_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_whatsapp_templates_name", "whatsapp_templates", ["name"])
    op.create_index("ix_whatsapp_templates_is_active", "whatsapp_templates", ["is_active"])


def downgrade() -> None:
    op.drop_table("whatsapp_templates")
