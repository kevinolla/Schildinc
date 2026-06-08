"""Email attachments + @mention notifications

Revision ID: 0020_attachments_notifications
Revises: 0019_helpdesk_nav
Create Date: 2026-06-08 01:00:00

`message_attachments` holds inbound email attachment metadata (bytes fetched
on-demand from Gmail). `notifications` powers @mentions in internal notes.
"""
from alembic import op
import sqlalchemy as sa


revision = "0020_attachments_notifications"
down_revision = "0019_helpdesk_nav"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "message_attachments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("message_id", sa.Integer(), sa.ForeignKey("messages.id"), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False, server_default=""),
        sa.Column("mime_type", sa.Text(), nullable=False, server_default=""),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("gmail_message_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("gmail_attachment_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_message_attachments_message_id", "message_attachments", ["message_id"])

    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("agent_id", sa.Integer(), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False, server_default="mention"),
        sa.Column("conversation_id", sa.Integer(), sa.ForeignKey("conversations.id"), nullable=True),
        sa.Column("message_id", sa.Integer(), sa.ForeignKey("messages.id"), nullable=True),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("is_read", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_notifications_agent_id", "notifications", ["agent_id"])
    op.create_index("ix_notifications_kind", "notifications", ["kind"])
    op.create_index("ix_notifications_conversation_id", "notifications", ["conversation_id"])
    op.create_index("ix_notifications_is_read", "notifications", ["is_read"])
    op.create_index("ix_notifications_created_at", "notifications", ["created_at"])


def downgrade() -> None:
    op.drop_table("notifications")
    op.drop_table("message_attachments")
