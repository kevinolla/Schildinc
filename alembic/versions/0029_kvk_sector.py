"""KVK sector classification: kvk_companies.main_sector / sub_sector / classifier_version.

Lets the KVK list be sliced by sector in the cold database + audience exports
(mirrors facebook_leads/prospects). Backfilled by scripts/backfill_kvk_sector.py.

Revision ID: 0029_kvk_sector
Revises: 0028_template_builder_json
"""

from alembic import op
import sqlalchemy as sa

revision = "0029_kvk_sector"
down_revision = "0028_template_builder_json"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("kvk_companies", sa.Column("main_sector", sa.Text(), nullable=False, server_default=""))
    op.add_column("kvk_companies", sa.Column("sub_sector", sa.Text(), nullable=False, server_default=""))
    op.add_column("kvk_companies", sa.Column("classifier_version", sa.Integer(), nullable=False, server_default="0"))
    op.create_index("ix_kvk_companies_main_sector", "kvk_companies", ["main_sector"])


def downgrade() -> None:
    op.drop_index("ix_kvk_companies_main_sector", table_name="kvk_companies")
    op.drop_column("kvk_companies", "classifier_version")
    op.drop_column("kvk_companies", "sub_sector")
    op.drop_column("kvk_companies", "main_sector")
