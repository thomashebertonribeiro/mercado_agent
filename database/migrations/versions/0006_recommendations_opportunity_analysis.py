"""Create recommendations, opportunity_scores, and analysis_reports tables.

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-03
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── recommendations ──────────────────────────────────────────────────
    op.create_table(
        "recommendations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("recommendation_type", sa.String(60), nullable=False, index=True),
        sa.Column("product_id", sa.String(50), nullable=True, index=True),
        sa.Column("category_id", sa.String(50), nullable=True, index=True),
        sa.Column("seller_id", sa.BigInteger(), nullable=True),
        sa.Column("priority_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("source", sa.String(50), nullable=False, server_default="recommendation_engine"),
        sa.Column("hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["category_id"], ["categories.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_recommendations_type_score", "recommendations", ["recommendation_type", "priority_score"])
    op.create_index("ix_recommendations_hash", "recommendations", ["hash"])

    # ── opportunity_scores ──────────────────────────────────────────────
    op.create_table(
        "opportunity_scores",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("product_id", sa.String(50), nullable=False, index=True),
        sa.Column("category_id", sa.String(50), nullable=True, index=True),
        sa.Column("total_score", sa.Integer(), nullable=False),
        sa.Column("raw_score", sa.Float(), nullable=False),
        sa.Column("factors", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("source", sa.String(50), nullable=False, server_default="opportunity_engine"),
        sa.Column("hash", sa.String(64), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["category_id"], ["categories.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_opp_score_product", "opportunity_scores", ["product_id", "computed_at"])
    op.create_index("ix_opp_score_category", "opportunity_scores", ["category_id", "computed_at"])

    # ── analysis_reports ────────────────────────────────────────────────
    op.create_table(
        "analysis_reports",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("product_id", sa.String(50), nullable=False, index=True),
        sa.Column("category_id", sa.String(50), nullable=True, index=True),
        sa.Column("report", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source", sa.String(50), nullable=False, server_default="ai_analyst"),
        sa.Column("hash", sa.String(64), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["category_id"], ["categories.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_analysis_reports_product", "analysis_reports", ["product_id", "generated_at"])


def downgrade() -> None:
    # analysis_reports
    op.drop_index("ix_analysis_reports_product", table_name="analysis_reports")
    op.drop_table("analysis_reports")

    # opportunity_scores
    op.drop_index("ix_opp_score_category", table_name="opportunity_scores")
    op.drop_index("ix_opp_score_product", table_name="opportunity_scores")
    op.drop_table("opportunity_scores")

    # recommendations
    op.drop_index("ix_recommendations_hash", table_name="recommendations")
    op.drop_index("ix_recommendations_type_score", table_name="recommendations")
    op.drop_table("recommendations")
