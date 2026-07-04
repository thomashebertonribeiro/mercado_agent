"""
repositories/finance.py

Repository para o Finance Engine.
"""

from datetime import datetime, timedelta
from typing import Optional
from sqlalchemy import select, func, and_, desc
from sqlalchemy.ext.asyncio import AsyncSession

from models.finance import ProductCost, CostHistory, FinancialSnapshot


class FinanceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── ProductCost ──────────────────────────────────────────────────

    async def get_active_cost(self, product_id: str) -> Optional[ProductCost]:
        result = await self._session.execute(
            select(ProductCost).where(
                ProductCost.product_id == product_id,
                ProductCost.is_active == True,
            )
        )
        return result.scalars().first()

    async def get_all_costs_by_seller(self, seller_id: int) -> list[ProductCost]:
        result = await self._session.execute(
            select(ProductCost).where(
                ProductCost.seller_id == seller_id,
                ProductCost.is_active == True,
            ).order_by(ProductCost.product_id)
        )
        return list(result.scalars().all())

    async def upsert_cost(
        self,
        product_id: str,
        seller_id: int,
        product_cost: float,
        shipping_cost: float = 0,
        packaging_cost: float = 0,
        ads_cost: float = 0,
        other_variable_cost: float = 0,
        monthly_fixed_cost: float = 0,
        tax_rate: float = 0,
        ml_commission_rate: float = 13.0,
        notes: str | None = None,
        changed_by: str | None = None,
    ) -> ProductCost:
        """Cria ou atualiza o custo de um produto."""
        existing = await self.get_active_cost(product_id)

        old_values = None
        if existing:
            old_values = {
                "product_cost": float(existing.product_cost),
                "shipping_cost": float(existing.shipping_cost),
                "tax_rate": float(existing.tax_rate),
                "ml_commission_rate": float(existing.ml_commission_rate),
            }
            # Desativar registro anterior
            existing.is_active = False
            existing.updated_at = datetime.utcnow()

        # Criar novo registro
        cost = ProductCost(
            product_id=product_id,
            seller_id=seller_id,
            product_cost=product_cost,
            shipping_cost=shipping_cost,
            packaging_cost=packaging_cost,
            ads_cost=ads_cost,
            other_variable_cost=other_variable_cost,
            monthly_fixed_cost=monthly_fixed_cost,
            tax_rate=tax_rate,
            ml_commission_rate=ml_commission_rate,
            is_active=True,
            notes=notes,
            source="api",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        self._session.add(cost)
        await self._session.flush()

        # Registrar no histórico
        history = CostHistory(
            cost_id=cost.id,
            product_id=product_id,
            old_product_cost=old_values["product_cost"] if old_values else None,
            old_shipping_cost=old_values["shipping_cost"] if old_values else None,
            old_tax_rate=old_values["tax_rate"] if old_values else None,
            old_ml_commission_rate=old_values["ml_commission_rate"] if old_values else None,
            new_product_cost=product_cost,
            new_shipping_cost=shipping_cost,
            new_tax_rate=tax_rate,
            new_ml_commission_rate=ml_commission_rate,
            changed_by=changed_by,
            recorded_at=datetime.utcnow(),
        )
        self._session.add(history)
        await self._session.flush()

        return cost

    async def bulk_upsert_costs(
        self, seller_id: int, costs: list[dict]
    ) -> list[ProductCost]:
        """Atualização em lote de custos."""
        results = []
        for item in costs:
            cost = await self.upsert_cost(
                product_id=item["product_id"],
                seller_id=seller_id,
                product_cost=item.get("product_cost", 0),
                shipping_cost=item.get("shipping_cost", 0),
                packaging_cost=item.get("packaging_cost", 0),
                ads_cost=item.get("ads_cost", 0),
                other_variable_cost=item.get("other_variable_cost", 0),
                monthly_fixed_cost=item.get("monthly_fixed_cost", 0),
                tax_rate=item.get("tax_rate", 0),
                ml_commission_rate=item.get("ml_commission_rate", 13.0),
                notes=item.get("notes"),
                changed_by="bulk_import",
            )
            results.append(cost)
        return results

    async def get_cost_history(self, product_id: str, limit: int = 50) -> list[CostHistory]:
        result = await self._session.execute(
            select(CostHistory)
            .where(CostHistory.product_id == product_id)
            .order_by(desc(CostHistory.recorded_at))
            .limit(limit)
        )
        return list(result.scalars().all())

    # ── FinancialSnapshot ────────────────────────────────────────────

    async def save_snapshot(self, snapshot: FinancialSnapshot) -> FinancialSnapshot:
        self._session.add(snapshot)
        await self._session.flush()
        return snapshot

    async def get_latest_snapshots(
        self, seller_id: int, limit: int = 50
    ) -> list[FinancialSnapshot]:
        result = await self._session.execute(
            select(FinancialSnapshot)
            .where(FinancialSnapshot.seller_id == seller_id)
            .order_by(desc(FinancialSnapshot.snapshot_date))
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_product_snapshots(
        self, product_id: str, days: int = 30
    ) -> list[FinancialSnapshot]:
        since = datetime.utcnow() - timedelta(days=days)
        result = await self._session.execute(
            select(FinancialSnapshot)
            .where(
                FinancialSnapshot.product_id == product_id,
                FinancialSnapshot.snapshot_date >= since,
            )
            .order_by(FinancialSnapshot.snapshot_date)
        )
        return list(result.scalars().all())

    async def get_seller_summary(
        self, seller_id: int, days: int = 30
    ) -> dict:
        """Resumo financeiro consolidado do vendedor."""
        since = datetime.utcnow() - timedelta(days=days)

        result = await self._session.execute(
            select(
                func.sum(FinancialSnapshot.gross_revenue).label("total_gross_revenue"),
                func.sum(FinancialSnapshot.net_revenue).label("total_net_revenue"),
                func.sum(FinancialSnapshot.net_profit).label("total_net_profit"),
                func.sum(FinancialSnapshot.ml_commission).label("total_ml_commission"),
                func.sum(FinancialSnapshot.shipping_total).label("total_shipping"),
                func.sum(FinancialSnapshot.ads_total).label("total_ads"),
                func.sum(FinancialSnapshot.taxes_total).label("total_taxes"),
                func.sum(FinancialSnapshot.product_cost_total).label("total_product_cost"),
                func.sum(FinancialSnapshot.inventory_value).label("total_inventory_value"),
                func.count(FinancialSnapshot.product_id.distinct()).label("total_products"),
                func.sum(FinancialSnapshot.sold_quantity).label("total_units_sold"),
            ).where(
                FinancialSnapshot.seller_id == seller_id,
                FinancialSnapshot.snapshot_date >= since,
            )
        )
        row = result.one_or_none()
        if not row or row.total_gross_revenue is None:
            return {
                "total_gross_revenue": 0,
                "total_net_revenue": 0,
                "total_net_profit": 0,
                "total_ml_commission": 0,
                "total_shipping": 0,
                "total_ads": 0,
                "total_taxes": 0,
                "total_product_cost": 0,
                "total_inventory_value": 0,
                "total_products": 0,
                "total_units_sold": 0,
                "margin_pct": 0,
                "roi": 0,
                "roas": 0,
            }

        gross = float(row.total_gross_revenue)
        net_profit = float(row.total_net_profit)
        net_rev = float(row.total_net_revenue)

        margin_pct = (net_profit / gross * 100) if gross > 0 else 0
        total_cost = float(row.total_product_cost) + float(row.total_ml_commission)
        roi = (net_profit / total_cost * 100) if total_cost > 0 else 0
        ads = float(row.total_ads)
        roas = (gross / ads) if ads > 0 else 0

        return {
            "total_gross_revenue": gross,
            "total_net_revenue": net_rev,
            "total_net_profit": net_profit,
            "total_ml_commission": float(row.total_ml_commission),
            "total_shipping": float(row.total_shipping),
            "total_ads": ads,
            "total_taxes": float(row.total_taxes),
            "total_product_cost": float(row.total_product_cost),
            "total_inventory_value": float(row.total_inventory_value),
            "total_products": row.total_products,
            "total_units_sold": row.total_units_sold or 0,
            "margin_pct": round(margin_pct, 2),
            "roi": round(roi, 2),
            "roas": round(roas, 2),
        }
