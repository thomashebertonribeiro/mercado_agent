"""Finance Engine — product_costs, cost_history, financial_snapshots

Revision ID: 0008_finance_engine
Revises: 0007_competitor_marketplace_accounts
Create Date: 2026-07-04

"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '0008'
down_revision: Union[str, None] = '0007'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── product_costs ────────────────────────────────────────────────
    op.create_table(
        'product_costs',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('product_id', sa.String(50), nullable=False),
        sa.Column('seller_id', sa.BigInteger(), nullable=False),
        sa.Column('product_cost', sa.Numeric(12, 2), nullable=False, server_default='0'),
        sa.Column('shipping_cost', sa.Numeric(12, 2), nullable=False, server_default='0'),
        sa.Column('packaging_cost', sa.Numeric(12, 2), nullable=False, server_default='0'),
        sa.Column('ads_cost', sa.Numeric(12, 2), nullable=False, server_default='0'),
        sa.Column('other_variable_cost', sa.Numeric(12, 2), nullable=False, server_default='0'),
        sa.Column('monthly_fixed_cost', sa.Numeric(12, 2), nullable=False, server_default='0'),
        sa.Column('tax_rate', sa.Numeric(5, 2), nullable=False, server_default='0'),
        sa.Column('ml_commission_rate', sa.Numeric(5, 2), nullable=False, server_default='13'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('source', sa.String(50), nullable=False, server_default='manual'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['product_id'], ['products.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['seller_id'], ['sellers.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_product_costs_product_active', 'product_costs', ['product_id', 'is_active'])
    op.create_index('ix_product_costs_seller', 'product_costs', ['seller_id'])

    # ── cost_history ─────────────────────────────────────────────────
    op.create_table(
        'cost_history',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('cost_id', sa.BigInteger(), nullable=False),
        sa.Column('product_id', sa.String(50), nullable=False),
        sa.Column('old_product_cost', sa.Numeric(12, 2), nullable=True),
        sa.Column('old_shipping_cost', sa.Numeric(12, 2), nullable=True),
        sa.Column('old_tax_rate', sa.Numeric(5, 2), nullable=True),
        sa.Column('old_ml_commission_rate', sa.Numeric(5, 2), nullable=True),
        sa.Column('new_product_cost', sa.Numeric(12, 2), nullable=False),
        sa.Column('new_shipping_cost', sa.Numeric(12, 2), nullable=False),
        sa.Column('new_tax_rate', sa.Numeric(5, 2), nullable=False),
        sa.Column('new_ml_commission_rate', sa.Numeric(5, 2), nullable=False),
        sa.Column('changed_by', sa.String(100), nullable=True),
        sa.Column('change_reason', sa.Text(), nullable=True),
        sa.Column('recorded_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['cost_id'], ['product_costs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_cost_history_product', 'cost_history', ['product_id'])
    op.create_index('ix_cost_history_cost', 'cost_history', ['cost_id'])
    op.create_index('ix_cost_history_recorded', 'cost_history', ['recorded_at'])

    # ── financial_snapshots ──────────────────────────────────────────
    op.create_table(
        'financial_snapshots',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('product_id', sa.String(50), nullable=False),
        sa.Column('seller_id', sa.BigInteger(), nullable=False),
        sa.Column('snapshot_date', sa.DateTime(), nullable=False),
        sa.Column('price', sa.Numeric(12, 2), nullable=False),
        sa.Column('available_quantity', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('sold_quantity', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('gross_revenue', sa.Numeric(12, 2), nullable=False, server_default='0'),
        sa.Column('net_revenue', sa.Numeric(12, 2), nullable=False, server_default='0'),
        sa.Column('total_cost', sa.Numeric(12, 2), nullable=False, server_default='0'),
        sa.Column('gross_profit', sa.Numeric(12, 2), nullable=False, server_default='0'),
        sa.Column('net_profit', sa.Numeric(12, 2), nullable=False, server_default='0'),
        sa.Column('margin_pct', sa.Numeric(5, 2), nullable=False, server_default='0'),
        sa.Column('ml_commission', sa.Numeric(12, 2), nullable=False, server_default='0'),
        sa.Column('shipping_total', sa.Numeric(12, 2), nullable=False, server_default='0'),
        sa.Column('ads_total', sa.Numeric(12, 2), nullable=False, server_default='0'),
        sa.Column('taxes_total', sa.Numeric(12, 2), nullable=False, server_default='0'),
        sa.Column('product_cost_total', sa.Numeric(12, 2), nullable=False, server_default='0'),
        sa.Column('roi', sa.Numeric(8, 2), nullable=False, server_default='0'),
        sa.Column('roas', sa.Numeric(8, 2), nullable=False, server_default='0'),
        sa.Column('inventory_value', sa.Numeric(12, 2), nullable=False, server_default='0'),
        sa.Column('inventory_days', sa.Integer(), nullable=True),
        sa.Column('source', sa.String(50), nullable=False, server_default='scheduler'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['product_id'], ['products.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_financial_snapshots_product_date', 'financial_snapshots', ['product_id', 'snapshot_date'])
    op.create_index('ix_financial_snapshots_seller_date', 'financial_snapshots', ['seller_id', 'snapshot_date'])
    op.create_index('ix_financial_snapshots_date', 'financial_snapshots', ['snapshot_date'])


def downgrade() -> None:
    op.drop_table('financial_snapshots')
    op.drop_table('cost_history')
    op.drop_table('product_costs')
