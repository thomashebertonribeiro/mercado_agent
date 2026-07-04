"""
services/finance_service.py

Finance Engine — calculo financeiro para produtos e vendedores.

Calcula automaticamente:
- Receita Bruta / Liquida
- Comissao ML, Frete, Ads, Embalagens
- Custos variaveis e fixos rateados
- Impostos
- Custo do produto (COGS)
- Lucro Bruto / Liquido
- Margem %
- ROI, ROAS
- Break-even
- Ponto de reposicao
- Giro de estoque
"""

import math
from datetime import datetime
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from repositories.finance import FinanceRepository
from repositories.product import ProductRepository
from repositories.event import EventRepository
from dto.finance import CostInput, FinancialMetrics, SellerFinancialSummary
from utils.logger import logger

# ML fee defaults (Brazil)
DEFAULT_ML_COMMISSION_RATE = 13.0  # %
DEFAULT_TAX_RATE = 0.0  # %
DEFAULT_SHIPPING_COST = 0.0


class FinanceService:
    """
    Servico de calculo financeiro.

    Uso:
        async with AsyncSessionLocal() as session:
            finance = FinanceService(session)
            metrics = await finance.calculate_product_metrics("MLB123")
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._finance_repo = FinanceRepository(session)
        self._product_repo = ProductRepository(session)
        self._event_repo = EventRepository(session)

    # ── Product Metrics ──────────────────────────────────────────────

    async def calculate_product_metrics(
        self,
        product_id: str,
        sold_quantity: int = 0,
    ) -> FinancialMetrics:
        """
        Calcula metricas financeiras completas para um produto.
        """
        product = await self._product_repo.get_by_id(product_id)
        if not product:
            raise LookupError(f"Produto {product_id} nao encontrado")

        cost = await self._finance_repo.get_active_cost(product_id)

        # Preco atual do produto
        price = float(product.initial_price or 0)

        # Buscar quantidade disponivel do ultimo price event
        available_qty = 0
        latest_event = await self._event_repo.get_latest_price_event(product_id)
        if latest_event:
            available_qty = latest_event.available_qty or 0
            if latest_event.price:
                price = float(latest_event.price)

        # Custos configurados
        product_cost = float(cost.product_cost) if cost else 0.0
        shipping_cost = float(cost.shipping_cost) if cost else 0.0
        packaging_cost = float(cost.packaging_cost) if cost else 0.0
        ads_cost = float(cost.ads_cost) if cost else 0.0
        other_var = float(cost.other_variable_cost) if cost else 0.0
        fixed_cost = float(cost.monthly_fixed_cost) if cost else 0.0
        tax_rate = float(cost.tax_rate) if cost else DEFAULT_TAX_RATE
        commission_rate = float(cost.ml_commission_rate) if cost else DEFAULT_ML_COMMISSION_RATE

        # ── Calculos ────────────────────────────────────────────────

        # Receita Bruta
        gross_revenue = price * sold_quantity

        # Comissao ML
        ml_commission = gross_revenue * (commission_rate / 100)

        # Frete total
        shipping_total = shipping_cost * sold_quantity

        # Embalagens
        packaging_total = packaging_cost * sold_quantity

        # Ads
        ads_total = ads_cost * sold_quantity

        # Outros variaveis
        other_variable_total = other_var * sold_quantity

        # Impostos
        taxes_total = gross_revenue * (tax_rate / 100)

        # Custo do produto
        product_cost_total = product_cost * sold_quantity

        # Custo fixo rateado (assume 30 dias)
        fixed_allocation = fixed_cost  # Ja e mensal

        # Custo Total
        total_cost = (
            ml_commission
            + shipping_total
            + packaging_total
            + ads_total
            + other_variable_total
            + taxes_total
            + product_cost_total
            + fixed_allocation
        )

        # Receita Liquida
        net_revenue = gross_revenue - ml_commission - taxes_total

        # Lucro Bruto (receita - custo do produto)
        gross_profit = gross_revenue - product_cost_total

        # Lucro Liquido
        net_profit = gross_revenue - total_cost

        # Margem %
        margin_pct = (net_profit / gross_revenue * 100) if gross_revenue > 0 else 0

        # ROI (Return on Investment)
        investment = product_cost_total + ads_total + fixed_allocation
        roi = (net_profit / investment * 100) if investment > 0 else 0

        # ROAS (Return on Ad Spend)
        roas = (gross_revenue / ads_total) if ads_total > 0 else 0

        # Break-even (unidades para cobrir custos fixos)
        contribution_margin = price - product_cost - shipping_cost - (price * commission_rate / 100) - (price * tax_rate / 100)
        break_even_units = (
            math.ceil(fixed_cost / contribution_margin) if contribution_margin > 0 else 0
        )

        # Capital em estoque
        inventory_value = product_cost * available_qty

        # Giro de estoque (unidades vendidas / estoque medio)
        avg_inventory = max(available_qty, 1)
        turnover_ratio = sold_quantity / avg_inventory if avg_inventory > 0 else 0

        # Estoque em dias
        daily_sales = sold_quantity / 30 if sold_quantity > 0 else 0
        inventory_days = int(available_qty / daily_sales) if daily_sales > 0 else None

        return FinancialMetrics(
            product_id=product_id,
            price=price,
            available_quantity=available_qty,
            sold_quantity=sold_quantity,
            gross_revenue=gross_revenue,
            net_revenue=net_revenue,
            product_cost_total=product_cost_total,
            ml_commission=ml_commission,
            shipping_total=shipping_total,
            ads_total=ads_total,
            taxes_total=taxes_total,
            packaging_total=packaging_total,
            other_variable_total=other_variable_total,
            fixed_cost_allocation=fixed_allocation,
            total_cost=total_cost,
            gross_profit=gross_profit,
            net_profit=net_profit,
            margin_pct=margin_pct,
            roi=roi,
            roas=roas,
            break_even_units=break_even_units,
            inventory_value=inventory_value,
            inventory_days=inventory_days,
            turnover_ratio=turnover_ratio,
        )

    # ── Seller Summary ───────────────────────────────────────────────

    async def calculate_seller_summary(
        self, seller_id: int, days: int = 30
    ) -> SellerFinancialSummary:
        """Resumo financeiro consolidado do vendedor."""
        summary_data = await self._finance_repo.get_seller_summary(seller_id, days)

        # Buscar todos os produtos com custo configurado
        costs = await self._finance_repo.get_all_costs_by_seller(seller_id)

        most_profitable = []
        least_profitable = []

        for cost in costs:
            try:
                metrics = await self.calculate_product_metrics(
                    cost.product_id, sold_quantity=0
                )
                entry = {
                    "product_id": cost.product_id,
                    "margin_pct": round(metrics.margin_pct, 2),
                    "net_profit": round(metrics.net_profit, 2),
                    "inventory_value": round(metrics.inventory_value, 2),
                }
                if metrics.margin_pct >= 0:
                    most_profitable.append(entry)
                else:
                    least_profitable.append(entry)
            except Exception:
                continue

        # Ordenar
        most_profitable.sort(key=lambda x: x["margin_pct"], reverse=True)
        least_profitable.sort(key=lambda x: x["margin_pct"])

        return SellerFinancialSummary(
            seller_id=seller_id,
            period_days=days,
            total_products=summary_data["total_products"],
            total_units_sold=summary_data["total_units_sold"],
            total_gross_revenue=summary_data["total_gross_revenue"],
            total_net_revenue=summary_data["total_net_revenue"],
            total_net_profit=summary_data["total_net_profit"],
            total_ml_commission=summary_data["total_ml_commission"],
            total_shipping=summary_data["total_shipping"],
            total_ads=summary_data["total_ads"],
            total_taxes=summary_data["total_taxes"],
            total_product_cost=summary_data["total_product_cost"],
            total_inventory_value=summary_data["total_inventory_value"],
            margin_pct=summary_data["margin_pct"],
            roi=summary_data["roi"],
            roas=summary_data["roas"],
            most_profitable=most_profitable[:10],
            least_profitable=least_profitable[:10],
        )

    # ── Cost Management ──────────────────────────────────────────────

    async def set_product_cost(self, input_data: CostInput) -> dict:
        """Define ou atualiza o custo de um produto."""
        cost = await self._finance_repo.upsert_cost(
            product_id=input_data.product_id,
            seller_id=input_data.seller_id,
            product_cost=input_data.product_cost,
            shipping_cost=input_data.shipping_cost,
            packaging_cost=input_data.packaging_cost,
            ads_cost=input_data.ads_cost,
            other_variable_cost=input_data.other_variable_cost,
            monthly_fixed_cost=input_data.monthly_fixed_cost,
            tax_rate=input_data.tax_rate,
            ml_commission_rate=input_data.ml_commission_rate,
            notes=input_data.notes,
            changed_by="api",
        )
        await self._session.commit()
        logger.info(f"Custo atualizado para {input_data.product_id}")
        return {"message": "Custo salvo com sucesso", "cost_id": cost.id}

    async def bulk_set_costs(self, seller_id: int, costs: list[dict]) -> dict:
        """Atualização em lote de custos."""
        results = await self._finance_repo.bulk_upsert_costs(seller_id, costs)
        await self._session.commit()
        logger.info(f"Custos atualizados em lote: {len(results)} produtos")
        return {"message": f"{len(results)} custos atualizados"}

    async def get_cost_history(self, product_id: str) -> list[dict]:
        """Historico de alteracoes de custo."""
        history = await self._finance_repo.get_cost_history(product_id)
        return [
            {
                "id": h.id,
                "old_product_cost": float(h.old_product_cost) if h.old_product_cost else None,
                "new_product_cost": float(h.new_product_cost),
                "old_tax_rate": float(h.old_tax_rate) if h.old_tax_rate else None,
                "new_tax_rate": float(h.new_tax_rate),
                "changed_by": h.changed_by,
                "recorded_at": h.recorded_at.isoformat(),
            }
            for h in history
        ]

    # ── Batch Analysis ───────────────────────────────────────────────

    async def analyze_all_products(self, seller_id: int) -> list[dict]:
        """Analise financeira de todos os produtos do vendedor."""
        costs = await self._finance_repo.get_all_costs_by_seller(seller_id)
        results = []

        for cost in costs:
            try:
                metrics = await self.calculate_product_metrics(
                    cost.product_id, sold_quantity=0
                )
                results.append(metrics.to_dict())
            except Exception as e:
                logger.warning(f"Erro ao analisar {cost.product_id}: {e}")
                continue

        # Ordenar por margem (maior primeiro)
        results.sort(key=lambda x: x["profit"]["margin_pct"], reverse=True)
        return results
