"""Track per-record search attempts on kvk_companies

Revision ID: 0011_kvk_search_attempts
Revises: 0010_customer_rich_fields
Create Date: 2026-05-20 00:00:00

Lets the agent pending endpoint sort by lowest-attempts-first so
records that have never been searched get tried before retrying
records that already failed once or twice.
"""
from alembic import op
import sqlalchemy as sa


revision = "0011_kvk_search_attempts"
down_revision = "0010_customer_rich_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "kvk_companies",
        sa.Column("search_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    # Index for ORDER BY search_attempts ASC in /agent/pending
    op.create_index(
        "ix_kvk_companies_search_attempts",
        "kvk_companies",
        ["search_attempts"],
    )


def downgrade() -> None:
    op.drop_index("ix_kvk_companies_search_attempts", table_name="kvk_companies")
    op.drop_column("kvk_companies", "search_attempts")
