"""Helpdesk nav — conversation favorite + agent team

Revision ID: 0019_helpdesk_nav
Revises: 0018_kvk_owner
Create Date: 2026-06-08 00:00:00

Adds conversations.is_favorite (Personal > Favorites view) and agents.team
(Teams view + per-team inbox routing). The 'spam' status reuses the existing
conversations.status text column — no schema change needed for it.
"""
from alembic import op
import sqlalchemy as sa


revision = "0019_helpdesk_nav"
down_revision = "0018_kvk_owner"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("is_favorite", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_index("ix_conversations_is_favorite", "conversations", ["is_favorite"])
    op.add_column("agents", sa.Column("team", sa.Text(), nullable=False, server_default=""))
    op.create_index("ix_agents_team", "agents", ["team"])


def downgrade() -> None:
    op.drop_index("ix_agents_team", table_name="agents")
    op.drop_column("agents", "team")
    op.drop_index("ix_conversations_is_favorite", table_name="conversations")
    op.drop_column("conversations", "is_favorite")
