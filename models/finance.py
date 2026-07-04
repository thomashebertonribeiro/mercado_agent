"""
models/finance.py

Finance Engine — custos, margens e metricas financeiras por produto.

Cada produto pode ter um custo proprio (COGS) configuravel.
Historico de custos mantido para auditoria.
Snapshots financeiros gerados periodicamente.
"""

from datetime import datetime
from sqlalchemy import (
    Column, String, BigInteger, Numeric, Integer, DateTime,
    ForeignKey, Text, Boolean, Index,
)
from sqlalchemy.orm import relationship
from models.base import Base


class ProductCost(Base):
    """
    Custo configurado para um produto específico.
    Um produto pode ter múltiplos registros (histórico),
    mas apenas o 'active' é usado para cálculos.
    """
    __tablename__ = "product_costs"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    product_id = Column(
        String(50), ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    seller_id = Column(BigInteger, ForeignKey("sellers.id", ondelete="CASCADE"), nullable=False)

    # Custo do produto (COGS)
    product_cost = Column(Numeric(12, 2), nullable=False, default=0)

    # Custos variáveis por unidade
    shipping_cost = Column(Numeric(12, 2), nullable=False, default=0)
    packaging_cost = Column(Numeric(12, 2), nullable=False, default=0)
    ads_cost = Column(Numeric(12, 2), nullable=False, default=0)
    other_variable_cost = Column(Numeric(12, 2), nullable=False, default=0)

    # Custos fixos rateados (mensal)
    monthly_fixed_cost = Column(Numeric(12, 2), nullable=False, default=0)

    # Impostos (%)
    tax_rate = Column(Numeric(5, 2), nullable=False, default=0)
    ml_commission_rate = Column(Numeric(5, 2), nullable=False, default=0)

    # Controle
    is_active = Column(Boolean, nullable=False, default=True)
    notes = Column(Text, nullable=True)
    source = Column(String(50), nullable=False, default="manual")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    product = relationship("Product", backref="costs")
    history = relationship("CostHistory", back_populates="cost", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_product_costs_product_active", "product_id", "is_active"),
        Index("ix_product_costs_seller", "seller_id"),
    )

    def __repr__(self) -> str:
        return f"<ProductCost product={self.product_id} cost={self.product_cost}>"


class CostHistory(Base):
    """
    Histórico de alterações de custo.
    Cada alteração gera um novo registro (append-only).
    """
    __tablename__ = "cost_history"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    cost_id = Column(
        BigInteger, ForeignKey("product_costs.id", ondelete="CASCADE"), nullable=False
    )
    product_id = Column(String(50), nullable=False)

    # Snapshot dos valores anteriores
    old_product_cost = Column(Numeric(12, 2), nullable=True)
    old_shipping_cost = Column(Numeric(12, 2), nullable=True)
    old_tax_rate = Column(Numeric(5, 2), nullable=True)
    old_ml_commission_rate = Column(Numeric(5, 2), nullable=True)

    # Snapshot dos novos valores
    new_product_cost = Column(Numeric(12, 2), nullable=False)
    new_shipping_cost = Column(Numeric(12, 2), nullable=False)
    new_tax_rate = Column(Numeric(5, 2), nullable=False)
    new_ml_commission_rate = Column(Numeric(5, 2), nullable=False)

    changed_by = Column(String(100), nullable=True)
    change_reason = Column(Text, nullable=True)
    recorded_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    cost = relationship("ProductCost", back_populates="history")

    __table_args__ = (
        Index("ix_cost_history_product", "product_id"),
        Index("ix_cost_history_cost", "cost_id"),
        Index("ix_cost_history_recorded", "recorded_at"),
    )


class FinancialSnapshot(Base):
    """
    Snapshot financeiro diário por produto.
    Gerado pelo scheduler (jobs/finance_jobs.py).
    Append-only — nunca sobrescreve.
    """
    __tablename__ = "financial_snapshots"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    product_id = Column(
        String(50), ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    seller_id = Column(BigInteger, nullable=False)
    snapshot_date = Column(DateTime, nullable=False)

    # Preço e estoque no momento do snapshot
    price = Column(Numeric(12, 2), nullable=False)
    available_quantity = Column(Integer, nullable=False, default=0)
    sold_quantity = Column(Integer, nullable=False, default=0)

    # Cálculos financeiros
    gross_revenue = Column(Numeric(12, 2), nullable=False, default=0)
    net_revenue = Column(Numeric(12, 2), nullable=False, default=0)
    total_cost = Column(Numeric(12, 2), nullable=False, default=0)
    gross_profit = Column(Numeric(12, 2), nullable=False, default=0)
    net_profit = Column(Numeric(12, 2), nullable=False, default=0)
    margin_pct = Column(Numeric(5, 2), nullable=False, default=0)

    # Custos detalhados
    ml_commission = Column(Numeric(12, 2), nullable=False, default=0)
    shipping_total = Column(Numeric(12, 2), nullable=False, default=0)
    ads_total = Column(Numeric(12, 2), nullable=False, default=0)
    taxes_total = Column(Numeric(12, 2), nullable=False, default=0)
    product_cost_total = Column(Numeric(12, 2), nullable=False, default=0)

    # ROI e ROAS
    roi = Column(Numeric(8, 2), nullable=False, default=0)
    roas = Column(Numeric(8, 2), nullable=False, default=0)

    # Capital em estoque
    inventory_value = Column(Numeric(12, 2), nullable=False, default=0)
    inventory_days = Column(Integer, nullable=True)

    source = Column(String(50), nullable=False, default="scheduler")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_financial_snapshots_product_date", "product_id", "snapshot_date"),
        Index("ix_financial_snapshots_seller_date", "seller_id", "snapshot_date"),
        Index("ix_financial_snapshots_date", "snapshot_date"),
    )

    def __repr__(self) -> str:
        return f"<FinancialSnapshot product={self.product_id} date={self.snapshot_date}>"
