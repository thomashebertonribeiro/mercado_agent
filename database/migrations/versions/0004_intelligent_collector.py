"""Add last_checked_at to products and extra_metrics to collection_jobs.

Revision ID: 0004
Revises: 0003_event_sourced_schema
Create Date: 2026-07-02
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003_event_sourced_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # products.last_checked_at
    op.add_column(
        "products",
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_products_last_checked_at", "products", ["last_checked_at"]
    )

    # collection_jobs.extra_metrics
    op.add_column(
        "collection_jobs",
        sa.Column(
            "extra_metrics",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_index("ix_products_last_checked_at", table_name="products")
    op.drop_column("products", "last_checked_at")
    op.drop_column("collection_jobs", "extra_metrics")
