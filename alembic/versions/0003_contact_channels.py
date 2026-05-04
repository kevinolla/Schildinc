"""Add explicit whatsapp contact fields

Revision ID: 0003_contact_channels
Revises: 0002_discovery_tier
Create Date: 2026-05-04 00:00:01
"""

from alembic import op
import sqlalchemy as sa


revision = "0003_contact_channels"
down_revision = "0002_discovery_tier"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("prospects", sa.Column("whatsapp_number", sa.Text(), nullable=False, server_default=""))
    op.add_column("prospects", sa.Column("whatsapp_url", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("prospects", "whatsapp_url")
    op.drop_column("prospects", "whatsapp_number")
