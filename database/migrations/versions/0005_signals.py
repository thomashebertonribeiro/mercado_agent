"""Create signals table for Intelligence Engine.

Revision ID: 0005
Revises: 0004_intelligent_collector
Create Date: 2026-07-03
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004_intelligent_collector"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "signals",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("signal_type", sa.String(60), nullable=False, index=True),
        sa.Column("product_id", sa.String(50), nullable=True, index=True),
        sa.Column("category_id", sa.String(50), nullable=True, index=True),
        sa.Column("weight", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("value", sa.Numeric(15, 4), nullable=True),
        sa.Column("extra_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(50), nullable=False, server_default="intelligence_engine"),
        sa.Column("hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["category_id"], ["categories.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_signals_type_product", "signals", ["signal_type", "product_id"])
    op.create_index("ix_signals_type_category", "signals", ["signal_type", "category_id"])
    op.create_index("ix_signals_computed_at", "signals", ["computed_at"])
    op.create_index("ix_signals_hash", "signals", ["hash"])


def downgrade() -> None:
    op.drop_index("ix_signals_hash", table_name="signals")
    op.drop_index("ix_signals_computed_at", table_name="signals")
    op.drop_index("ix_signals_type_category", table_name="signals")
    op.drop_index("ix_signals_type_product", table_name="signals")
    op.drop_table("signals")
