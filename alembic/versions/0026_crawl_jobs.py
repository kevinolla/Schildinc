"""Directory crawler: crawl_jobs table + prospect/recipient provenance columns.

- `crawl_jobs`: one sector x country crawl task with live counters. Up to
  CRAWLER_MAX_CONCURRENT_JOBS run concurrently; jobs resume from queries_done.
- `prospects.crawl_job_id` + `prospects.main_sector`: which job produced the
  row and the canonical sector of the search term that surfaced it.
- `email_campaign_recipients.prospect_id`: crawled prospects become a campaign
  audience (source_type='prospect').

Revision ID: 0026_crawl_jobs
Revises: 0025_sequences
"""

from alembic import op
import sqlalchemy as sa

revision = "0026_crawl_jobs"
down_revision = "0025_sequences"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "crawl_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False, server_default=""),
        sa.Column("sectors", sa.Text(), nullable=False, server_default=""),
        sa.Column("country_code", sa.Text(), nullable=False, server_default="NL"),
        sa.Column("status", sa.Text(), nullable=False, server_default="running"),
        sa.Column("max_results", sa.Integer(), nullable=False, server_default="500"),
        sa.Column("extract_emails", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("queries_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("queries_done", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("found_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("new_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("dup_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("email_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("client_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("current_activity", sa.Text(), nullable=False, server_default=""),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_crawl_jobs_status", "crawl_jobs", ["status"])
    op.create_index("ix_crawl_jobs_country_code", "crawl_jobs", ["country_code"])

    # batch_alter_table: plain ALTERs on Postgres, copy-and-move on SQLite
    # (SQLite cannot ALTER-add a column that carries an FK constraint).
    with op.batch_alter_table("prospects") as batch:
        batch.add_column(sa.Column("crawl_job_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("main_sector", sa.Text(), nullable=False, server_default=""))
        batch.create_foreign_key("fk_prospects_crawl_job_id", "crawl_jobs", ["crawl_job_id"], ["id"])
        batch.create_index("ix_prospects_crawl_job_id", ["crawl_job_id"])
        batch.create_index("ix_prospects_main_sector", ["main_sector"])

    with op.batch_alter_table("email_campaign_recipients") as batch:
        batch.add_column(sa.Column("prospect_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_email_campaign_recipients_prospect_id", "prospects", ["prospect_id"], ["id"])
        batch.create_index("ix_email_campaign_recipients_prospect_id", ["prospect_id"])


def downgrade() -> None:
    with op.batch_alter_table("email_campaign_recipients") as batch:
        batch.drop_index("ix_email_campaign_recipients_prospect_id")
        batch.drop_constraint("fk_email_campaign_recipients_prospect_id", type_="foreignkey")
        batch.drop_column("prospect_id")
    with op.batch_alter_table("prospects") as batch:
        batch.drop_index("ix_prospects_main_sector")
        batch.drop_index("ix_prospects_crawl_job_id")
        batch.drop_constraint("fk_prospects_crawl_job_id", type_="foreignkey")
        batch.drop_column("main_sector")
        batch.drop_column("crawl_job_id")
    op.drop_index("ix_crawl_jobs_country_code", table_name="crawl_jobs")
    op.drop_index("ix_crawl_jobs_status", table_name="crawl_jobs")
    op.drop_table("crawl_jobs")
