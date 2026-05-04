"""Store full discovery email and page lists

Revision ID: 0004_discovery_lists
Revises: 0003_contact_channels
Create Date: 2026-05-04 00:00:02
"""

from alembic import op
import sqlalchemy as sa


revision = "0004_discovery_lists"
down_revision = "0003_contact_channels"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("prospects", sa.Column("emails_found", sa.Text(), nullable=False, server_default=""))
    op.add_column("prospects", sa.Column("pages_scanned", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("prospects", "pages_scanned")
    op.drop_column("prospects", "emails_found")
