"""In-house CRM Phase 2 — shared inbox: agents, conversations, messages, canned replies

Revision ID: 0015_inbox
Revises: 0014_contact_hub
Create Date: 2026-06-05 00:30:00

Adds the Trengo-style shared inbox: `agents`, `conversations`, `messages`,
`canned_replies`, plus `gmail_accounts.last_poll_at` for incremental inbound
email polling (two-way email).
"""
from alembic import op
import sqlalchemy as sa


revision = "0015_inbox"
down_revision = "0014_contact_hub"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False, server_default=""),
        sa.Column("email", sa.Text(), nullable=False, server_default=""),
        sa.Column("role", sa.Text(), nullable=False, server_default="agent"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_agents_name", "agents", ["name"])
    op.create_index("ix_agents_email", "agents", ["email"], unique=True)
    op.create_index("ix_agents_role", "agents", ["role"])
    op.create_index("ix_agents_is_active", "agents", ["is_active"])

    op.create_table(
        "conversations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("contact_id", sa.Integer(), sa.ForeignKey("contacts.id"), nullable=True),
        sa.Column("channel", sa.Text(), nullable=False, server_default="email"),
        sa.Column("subject", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.Text(), nullable=False, server_default="open"),
        sa.Column("assignee_agent_id", sa.Integer(), sa.ForeignKey("agents.id"), nullable=True),
        sa.Column("labels", sa.Text(), nullable=False, server_default=""),
        sa.Column("unread", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_message_preview", sa.Text(), nullable=False, server_default=""),
        sa.Column("last_direction", sa.Text(), nullable=False, server_default=""),
        sa.Column("snoozed_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("external_thread_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("contact_email", sa.Text(), nullable=False, server_default=""),
        sa.Column("contact_phone", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    for col in ["contact_id", "channel", "status", "assignee_agent_id", "unread",
                "last_message_at", "external_thread_id", "contact_email"]:
        op.create_index(f"ix_conversations_{col}", "conversations", [col])

    op.create_table(
        "messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("conversation_id", sa.Integer(), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("contact_id", sa.Integer(), sa.ForeignKey("contacts.id"), nullable=True),
        sa.Column("direction", sa.Text(), nullable=False, server_default="in"),
        sa.Column("channel", sa.Text(), nullable=False, server_default="email"),
        sa.Column("is_internal_note", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("from_addr", sa.Text(), nullable=False, server_default=""),
        sa.Column("to_addr", sa.Text(), nullable=False, server_default=""),
        sa.Column("subject", sa.Text(), nullable=False, server_default=""),
        sa.Column("body_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("body_html", sa.Text(), nullable=False, server_default=""),
        sa.Column("agent_id", sa.Integer(), sa.ForeignKey("agents.id"), nullable=True),
        sa.Column("agent_name", sa.Text(), nullable=False, server_default=""),
        sa.Column("external_message_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("external_thread_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.Text(), nullable=False, server_default=""),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])
    op.create_index("ix_messages_contact_id", "messages", ["contact_id"])
    op.create_index("ix_messages_direction", "messages", ["direction"])
    op.create_index("ix_messages_is_internal_note", "messages", ["is_internal_note"])
    op.create_index("ix_messages_external_message_id", "messages", ["external_message_id"])
    op.create_index("ix_messages_occurred_at", "messages", ["occurred_at"])

    op.create_table(
        "canned_replies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column("category", sa.Text(), nullable=False, server_default="general"),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_starter", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("seed_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_canned_replies_title", "canned_replies", ["title"])
    op.create_index("ix_canned_replies_is_active", "canned_replies", ["is_active"])

    op.add_column("gmail_accounts", sa.Column("last_poll_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("gmail_accounts", "last_poll_at")
    op.drop_table("canned_replies")
    op.drop_table("messages")
    op.drop_table("conversations")
    op.drop_table("agents")
