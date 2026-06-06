"""Owner / decision-maker enrichment fields on kvk_companies

Revision ID: 0018_kvk_owner
Revises: 0017_roles_audit
Create Date: 2026-06-06 00:00:00

Lets the local Google-snippet enrichment agent record the owner/decision-maker
name (+ role + socials source) so cold campaigns personalize the greeting.
"""
from alembic import op
import sqlalchemy as sa


revision = "0018_kvk_owner"
down_revision = "0017_roles_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("kvk_companies", sa.Column("owner_name", sa.Text(), nullable=False, server_default=""))
    op.add_column("kvk_companies", sa.Column("owner_role", sa.Text(), nullable=False, server_default=""))
    op.add_column("kvk_companies", sa.Column("owner_source", sa.Text(), nullable=False, server_default=""))
    op.add_column("kvk_companies", sa.Column("owner_status", sa.Text(), nullable=False, server_default="pending"))
    op.add_column("kvk_companies", sa.Column("owner_search_attempts", sa.Integer(), nullable=False, server_default="0"))
    op.create_index("ix_kvk_companies_owner_name", "kvk_companies", ["owner_name"])
    op.create_index("ix_kvk_companies_owner_status", "kvk_companies", ["owner_status"])
    op.create_index("ix_kvk_companies_owner_search_attempts", "kvk_companies", ["owner_search_attempts"])


def downgrade() -> None:
    op.drop_index("ix_kvk_companies_owner_search_attempts", table_name="kvk_companies")
    op.drop_index("ix_kvk_companies_owner_status", table_name="kvk_companies")
    op.drop_index("ix_kvk_companies_owner_name", table_name="kvk_companies")
    op.drop_column("kvk_companies", "owner_search_attempts")
    op.drop_column("kvk_companies", "owner_status")
    op.drop_column("kvk_companies", "owner_source")
    op.drop_column("kvk_companies", "owner_role")
    op.drop_column("kvk_companies", "owner_name")
