"""Add sales-annotation columns to facebook_leads

Revision ID: 0009_fb_leads_annotations
Revises: 0008_facebook_leads
Create Date: 2026-05-15 11:00:00

The historical Marketing Lead CSV adds sales-side annotations to each
lead (Quality Score, Progress, PIC, Customer Segmentation, etc.) plus
explicit Country / Email Quality fields. This migration adds the
columns so both the current Lead Ads sheet AND the historical export
can flow into the same table.
"""
from alembic import op
import sqlalchemy as sa


revision = "0009_fb_leads_annotations"
down_revision = "0008_facebook_leads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for col in [
        ("country",                 sa.Text(),    ""),
        ("email_quality",           sa.Text(),    ""),
        ("quality_score",           sa.Text(),    ""),  # text — sometimes blank
        ("leads_quality",           sa.Text(),    ""),
        ("progress",                sa.Text(),    ""),
        ("pic",                     sa.Text(),    ""),
        ("customer_segmentation",   sa.Text(),    ""),
        ("total_order_amount",      sa.Text(),    ""),
        ("detailed_information",    sa.Text(),    ""),
        ("email_marketing_consent", sa.Text(),    ""),
    ]:
        name, type_, default = col
        op.add_column(
            "facebook_leads",
            sa.Column(name, type_, nullable=False, server_default=default),
        )


def downgrade() -> None:
    for name in [
        "email_marketing_consent", "detailed_information", "total_order_amount",
        "customer_segmentation", "pic", "progress", "leads_quality",
        "quality_score", "email_quality", "country",
    ]:
        op.drop_column("facebook_leads", name)
