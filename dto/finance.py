"""
dto/finance.py

Data Transfer Objects para o Finance Engine.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class CostInput:
    """Entrada para criar/atualizar custo de produto."""
    product_id: str
    seller_id: int
    product_cost: float = 0.0
    shipping_cost: float = 0.0
    packaging_cost: float = 0.0
    ads_cost: float = 0.0
    other_variable_cost: float = 0.0
    monthly_fixed_cost: float = 0.0
    tax_rate: float = 0.0
    ml_commission_rate: float = 13.0
    notes: Optional[str] = None


@dataclass
class BulkCostInput:
    """Entrada para atualização em lote."""
    seller_id: int
    costs: list[CostInput] = field(default_factory=list)


@dataclass
class FinancialMetrics:
    """Métricas financeiras calculadas para um produto."""
    product_id: str
    price: float
    available_quantity: int
    sold_quantity: int

    # Receita
    gross_revenue: float = 0.0
    net_revenue: float = 0.0

    # Custos
    product_cost_total: float = 0.0
    ml_commission: float = 0.0
    shipping_total: float = 0.0
    ads_total: float = 0.0
    taxes_total: float = 0.0
    packaging_total: float = 0.0
    other_variable_total: float = 0.0
    fixed_cost_allocation: float = 0.0
    total_cost: float = 0.0

    # Lucro
    gross_profit: float = 0.0
    net_profit: float = 0.0
    margin_pct: float = 0.0

    # Indicadores
    roi: float = 0.0
    roas: float = 0.0
    break_even_units: int = 0
    inventory_value: float = 0.0
    inventory_days: Optional[int] = None
    turnover_ratio: float = 0.0

    def to_dict(self) -> dict:
        return {
            "product_id": self.product_id,
            "price": round(self.price, 2),
            "available_quantity": self.available_quantity,
            "sold_quantity": self.sold_quantity,
            "gross_revenue": round(self.gross_revenue, 2),
            "net_revenue": round(self.net_revenue, 2),
            "costs": {
                "product_cost": round(self.product_cost_total, 2),
                "ml_commission": round(self.ml_commission, 2),
                "shipping": round(self.shipping_total, 2),
                "ads": round(self.ads_total, 2),
                "taxes": round(self.taxes_total, 2),
                "packaging": round(self.packaging_total, 2),
                "other_variable": round(self.other_variable_total, 2),
                "fixed_allocation": round(self.fixed_cost_allocation, 2),
                "total": round(self.total_cost, 2),
            },
            "profit": {
                "gross": round(self.gross_profit, 2),
                "net": round(self.net_profit, 2),
                "margin_pct": round(self.margin_pct, 2),
            },
            "indicators": {
                "roi": round(self.roi, 2),
                "roas": round(self.roas, 2),
                "break_even_units": self.break_even_units,
                "inventory_value": round(self.inventory_value, 2),
                "inventory_days": self.inventory_days,
                "turnover_ratio": round(self.turnover_ratio, 2),
            },
        }


@dataclass
class SellerFinancialSummary:
    """Resumo financeiro consolidado do vendedor."""
    seller_id: int
    period_days: int
    total_products: int
    total_units_sold: int

    total_gross_revenue: float = 0.0
    total_net_revenue: float = 0.0
    total_net_profit: float = 0.0
    total_ml_commission: float = 0.0
    total_shipping: float = 0.0
    total_ads: float = 0.0
    total_taxes: float = 0.0
    total_product_cost: float = 0.0
    total_inventory_value: float = 0.0

    margin_pct: float = 0.0
    roi: float = 0.0
    roas: float = 0.0

    # Break-even
    break_even_revenue: float = 0.0

    # Top/bottom performers
    most_profitable: list[dict] = field(default_factory=list)
    least_profitable: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "seller_id": self.seller_id,
            "period_days": self.period_days,
            "summary": {
                "total_products": self.total_products,
                "total_units_sold": self.total_units_sold,
                "gross_revenue": round(self.total_gross_revenue, 2),
                "net_revenue": round(self.total_net_revenue, 2),
                "net_profit": round(self.total_net_profit, 2),
                "margin_pct": round(self.margin_pct, 2),
                "roi": round(self.roi, 2),
                "roas": round(self.roas, 2),
            },
            "costs": {
                "ml_commission": round(self.total_ml_commission, 2),
                "shipping": round(self.total_shipping, 2),
                "ads": round(self.total_ads, 2),
                "taxes": round(self.total_taxes, 2),
                "product_cost": round(self.total_product_cost, 2),
                "inventory_value": round(self.total_inventory_value, 2),
            },
            "break_even_revenue": round(self.break_even_revenue, 2),
            "most_profitable": self.most_profitable,
            "least_profitable": self.least_profitable,
        }
