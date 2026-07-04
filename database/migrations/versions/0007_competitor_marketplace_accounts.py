"""0007_competitor_marketplace_accounts

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-04
"""

from alembic import op
import sqlalchemy as sa


revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # marketplace_accounts
    op.create_table(
        "marketplace_accounts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("marketplace", sa.String(50), nullable=False, server_default="mercadolivre"),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("nickname", sa.String(200), nullable=True),
        sa.Column("country", sa.String(10), nullable=True),
        sa.Column("access_token", sa.Text(), nullable=False),
        sa.Column("refresh_token", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scope", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_marketplace_accounts_marketplace_user", "marketplace_accounts", ["marketplace", "user_id"], unique=True)

    # competitors
    op.create_table(
        "competitors",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("ml_item_id", sa.String(50), unique=True, nullable=False),
        sa.Column("seller_id", sa.BigInteger(), nullable=True),
        sa.Column("title", sa.String(500), nullable=True, server_default=""),
        sa.Column("category_id", sa.String(50), nullable=True),
        sa.Column("current_price", sa.Float(), nullable=True),
        sa.Column("original_price", sa.Float(), nullable=True),
        sa.Column("available_quantity", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(30), nullable=True),
        sa.Column("listing_type", sa.String(50), nullable=True),
        sa.Column("shipping_free", sa.Boolean(), server_default=sa.text("false")),
        sa.Column("permalink", sa.String(1000), nullable=True),
        sa.Column("thumbnail", sa.String(1000), nullable=True),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true")),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # competitor_history
    op.create_table(
        "competitor_history",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("competitor_id", sa.Integer(), sa.ForeignKey("competitors.id", ondelete="CASCADE"), nullable=False),
        sa.Column("price", sa.Float(), nullable=True),
        sa.Column("original_price", sa.Float(), nullable=True),
        sa.Column("available_quantity", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(30), nullable=True),
        sa.Column("shipping_free", sa.Boolean(), nullable=True),
        sa.Column("listing_type", sa.String(50), nullable=True),
        sa.Column("change_type", sa.String(50), nullable=False),
        sa.Column("change_detail", sa.Text(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_competitor_history_competitor_id", "competitor_history", ["competitor_id"])

    # market_trends
    op.create_table(
        "market_trends",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("keyword", sa.String(200), nullable=False),
        sa.Column("category_id", sa.String(50), nullable=True),
        sa.Column("category_name", sa.String(200), nullable=True),
        sa.Column("trend_type", sa.String(30), nullable=False, server_default="keyword"),
        sa.Column("total_items", sa.Integer(), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("collected_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_market_trends_keyword_type", "market_trends", ["keyword", "trend_type"])


def downgrade() -> None:
    op.drop_table("market_trends")
    op.drop_table("competitor_history")
    op.drop_table("competitors")
    op.drop_table("marketplace_accounts")
