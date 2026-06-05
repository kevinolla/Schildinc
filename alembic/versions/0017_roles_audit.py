"""In-house CRM Phase 6 — agent login (roles) + audit log

Revision ID: 0017_roles_audit
Revises: 0016_whatsapp
Create Date: 2026-06-05 02:00:00

Adds per-agent login (agents.password_hash + last_login_at) for real
role-based permissions, and an `audit_logs` table recording sensitive actions.
"""
from alembic import op
import sqlalchemy as sa


revision = "0017_roles_audit"
down_revision = "0016_whatsapp"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agents", sa.Column("password_hash", sa.Text(), nullable=False, server_default=""))
    op.add_column("agents", sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("agent_id", sa.Integer(), nullable=True),
        sa.Column("actor", sa.Text(), nullable=False, server_default=""),
        sa.Column("action", sa.Text(), nullable=False, server_default=""),
        sa.Column("target_type", sa.Text(), nullable=False, server_default=""),
        sa.Column("target_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("detail", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_audit_logs_agent_id", "audit_logs", ["agent_id"])
    op.create_index("ix_audit_logs_actor", "audit_logs", ["actor"])
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_column("agents", "last_login_at")
    op.drop_column("agents", "password_hash")
