"""Crawl jobs: optional city-level targeting.

`crawl_jobs.cities` — comma-separated city names. Empty = whole country.

Revision ID: 0027_crawl_job_cities
Revises: 0026_crawl_jobs
"""

from alembic import op
import sqlalchemy as sa

revision = "0027_crawl_job_cities"
down_revision = "0026_crawl_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("crawl_jobs", sa.Column("cities", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("crawl_jobs", "cities")
