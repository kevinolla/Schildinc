"""Add Schild Inc-specific columns to customers (sector, contact, segment)

Revision ID: 0010_customer_rich_fields
Revises: 0009_fb_leads_annotations
Create Date: 2026-05-18 00:00:00

The Schild Inc historical customer CSV (order-lines aggregated by
customer) carries sales-side annotations that don't map cleanly to
the original Customer model — main sector, sub sector, B2B/B2C
segment, contact person, phone, website. We add lean Text columns
for each so they can be displayed/filtered without parsing notes.
"""
from alembic import op
import sqlalchemy as sa


revision = "0010_customer_rich_fields"
down_revision = "0009_fb_leads_annotations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for col in [
        ("main_sector",      sa.Text()),  # 'Bike', 'Liquor', etc.
        ("sub_sector",       sa.Text()),  # 'Motorcycle', 'Wine', etc.
        ("customer_segment", sa.Text()),  # 'B2B' / 'B2C'
        ("contact_person",   sa.Text()),
        ("phone_primary",    sa.Text()),
        ("website",          sa.Text()),  # full URL — website_domain_candidate is just the domain
    ]:
        name, type_ = col
        op.add_column("customers", sa.Column(name, type_, nullable=False, server_default=""))
    # Useful index for analytics-page filtering
    op.create_index("ix_customers_main_sector", "customers", ["main_sector"])
    op.create_index("ix_customers_customer_segment", "customers", ["customer_segment"])


def downgrade() -> None:
    op.drop_index("ix_customers_customer_segment", table_name="customers")
    op.drop_index("ix_customers_main_sector", table_name="customers")
    for name in ["website", "phone_primary", "contact_person",
                 "customer_segment", "sub_sector", "main_sector"]:
        op.drop_column("customers", name)
