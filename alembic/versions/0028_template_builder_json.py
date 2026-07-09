"""Visual template editor: email_templates.builder_json block model.

Stores the drag-and-drop editor's block list (JSON) so a template can be
re-opened and edited visually. body_html/body_text remain the compiled output.

Revision ID: 0028_template_builder_json
Revises: 0027_crawl_job_cities
"""

from alembic import op
import sqlalchemy as sa

revision = "0028_template_builder_json"
down_revision = "0027_crawl_job_cities"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("email_templates", sa.Column("builder_json", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("email_templates", "builder_json")
