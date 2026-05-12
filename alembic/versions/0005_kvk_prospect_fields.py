"""Add KVK import fields to prospects

Revision ID: 0005_kvk_prospect_fields
Revises: 0004_discovery_lists
Create Date: 2026-05-05 00:00:00
"""

from alembic import op
import sqlalchemy as sa


revision = "0005_kvk_prospect_fields"
down_revision = "0004_discovery_lists"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("prospects", sa.Column("kvk_number", sa.Text(), nullable=False, server_default=""))
    op.add_column("prospects", sa.Column("kvk_establishment_number", sa.Text(), nullable=False, server_default=""))
    op.add_column("prospects", sa.Column("kvk_company_entity_id", sa.Text(), nullable=False, server_default=""))
    op.add_column("prospects", sa.Column("website_search_query", sa.Text(), nullable=False, server_default=""))
    op.add_column("prospects", sa.Column("contact_search_query", sa.Text(), nullable=False, server_default=""))
    op.create_index("ix_prospects_kvk_number", "prospects", ["kvk_number"])
    op.create_index("ix_prospects_kvk_establishment_number", "prospects", ["kvk_establishment_number"])
    op.create_index("ix_prospects_kvk_company_entity_id", "prospects", ["kvk_company_entity_id"])


def downgrade() -> None:
    op.drop_index("ix_prospects_kvk_company_entity_id", table_name="prospects")
    op.drop_index("ix_prospects_kvk_establishment_number", table_name="prospects")
    op.drop_index("ix_prospects_kvk_number", table_name="prospects")
    op.drop_column("prospects", "contact_search_query")
    op.drop_column("prospects", "website_search_query")
    op.drop_column("prospects", "kvk_company_entity_id")
    op.drop_column("prospects", "kvk_establishment_number")
    op.drop_column("prospects", "kvk_number")
