"""
tests/test_finance/test_finance_engine.py

Testes do Finance Engine — calculos financeiros, custos, metricas.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from decimal import Decimal

from services.finance_service import FinanceService
from dto.finance import CostInput, FinancialMetrics, SellerFinancialSummary


def _make_product(price=100.0):
    product = MagicMock()
    product.id = "MLB123"
    product.title = "Test Product"
    product.initial_price = Decimal(str(price))
    product.category_id = "MLB1055"
    product.seller_id = 123456789
    return product


def _make_cost(product_cost=30.0, shipping=5.0, tax=0.0, commission=13.0):
    cost = MagicMock()
    cost.product_cost = Decimal(str(product_cost))
    cost.shipping_cost = Decimal(str(shipping))
    cost.packaging_cost = Decimal("1.50")
    cost.ads_cost = Decimal("2.00")
    cost.other_variable_cost = Decimal("0")
    cost.monthly_fixed_cost = Decimal("100.00")
    cost.tax_rate = Decimal(str(tax))
    cost.ml_commission_rate = Decimal(str(commission))
    return cost


def _make_price_event(price=100.0, qty=10):
    event = MagicMock()
    event.price = Decimal(str(price))
    event.available_qty = qty
    return event


def _make_service():
    session = AsyncMock()
    service = FinanceService(session)
    service._finance_repo = AsyncMock()
    service._product_repo = AsyncMock()
    service._event_repo = AsyncMock()
    return service


class TestProductMetrics:
    @pytest.mark.asyncio
    async def test_basic_calculation(self):
        service = _make_service()
        service._product_repo.get_by_id.return_value = _make_product(price=100.0)
        service._finance_repo.get_active_cost.return_value = _make_cost(
            product_cost=30.0, shipping=5.0, tax=0.0, commission=13.0
        )
        service._event_repo.get_latest_price_event.return_value = _make_price_event(100.0, 10)

        metrics = await service.calculate_product_metrics("MLB123", sold_quantity=5)

        assert metrics.product_id == "MLB123"
        assert metrics.price == 100.0
        assert metrics.gross_revenue == 500.0
        assert metrics.ml_commission == 65.0  # 13% of 500
        assert metrics.product_cost_total == 150.0  # 30 * 5
        assert metrics.shipping_total == 25.0  # 5 * 5
        assert metrics.taxes_total == 0.0  # 0% tax

    @pytest.mark.asyncio
    async def test_margin_calculation(self):
        service = _make_service()
        service._product_repo.get_by_id.return_value = _make_product(price=100.0)
        service._finance_repo.get_active_cost.return_value = _make_cost(
            product_cost=30.0, shipping=5.0, tax=0.0, commission=13.0
        )
        service._event_repo.get_latest_price_event.return_value = _make_price_event(100.0, 10)

        metrics = await service.calculate_product_metrics("MLB123", sold_quantity=10)

        # gross_revenue = 1000
        # total_cost = 130 (commission) + 50 (shipping) + 15 (packaging) + 20 (ads) + 0 (other) + 0 (tax) + 300 (product) + 100 (fixed) = 615
        # net_profit = 1000 - 615 = 385
        # margin = 385/1000 * 100 = 38.5%
        assert metrics.margin_pct == pytest.approx(38.5, rel=1e-1)

    @pytest.mark.asyncio
    async def test_roi_calculation(self):
        service = _make_service()
        service._product_repo.get_by_id.return_value = _make_product(price=100.0)
        service._finance_repo.get_active_cost.return_value = _make_cost(
            product_cost=30.0, shipping=5.0, tax=0.0, commission=13.0
        )
        service._event_repo.get_latest_price_event.return_value = _make_price_event(100.0, 10)

        metrics = await service.calculate_product_metrics("MLB123", sold_quantity=10)

        # investment = 300 (product) + 20 (ads) + 100 (fixed) = 420
        # roi = 385 / 420 * 100 = ~91.67%
        assert metrics.roi > 0

    @pytest.mark.asyncio
    async def test_roas_with_ads(self):
        service = _make_service()
        service._product_repo.get_by_id.return_value = _make_product(price=100.0)
        service._finance_repo.get_active_cost.return_value = _make_cost(
            product_cost=30.0, shipping=5.0, tax=0.0, commission=13.0
        )
        service._event_repo.get_latest_price_event.return_value = _make_price_event(100.0, 10)

        metrics = await service.calculate_product_metrics("MLB123", sold_quantity=10)

        # roas = gross_revenue / ads_total = 1000 / 20 = 50
        assert metrics.roas == 50.0

    @pytest.mark.asyncio
    async def test_break_even_units(self):
        service = _make_service()
        service._product_repo.get_by_id.return_value = _make_product(price=100.0)
        service._finance_repo.get_active_cost.return_value = _make_cost(
            product_cost=30.0, shipping=5.0, tax=0.0, commission=13.0
        )
        service._event_repo.get_latest_price_event.return_value = _make_price_event(100.0, 10)

        metrics = await service.calculate_product_metrics("MLB123", sold_quantity=0)

        # contribution_margin = 100 - 30 - 5 - (100*0.13) - (100*0) = 100 - 30 - 5 - 13 = 52
        # break_even = ceil(100 / 52) = 2
        assert metrics.break_even_units >= 1

    @pytest.mark.asyncio
    async def test_inventory_value(self):
        service = _make_service()
        service._product_repo.get_by_id.return_value = _make_product(price=100.0)
        service._finance_repo.get_active_cost.return_value = _make_cost(product_cost=30.0)
        service._event_repo.get_latest_price_event.return_value = _make_price_event(100.0, 20)

        metrics = await service.calculate_product_metrics("MLB123", sold_quantity=5)

        assert metrics.inventory_value == 600.0  # 30 * 20

    @pytest.mark.asyncio
    async def test_no_cost_configured(self):
        service = _make_service()
        service._product_repo.get_by_id.return_value = _make_product(price=100.0)
        service._finance_repo.get_active_cost.return_value = None
        service._event_repo.get_latest_price_event.return_value = _make_price_event(100.0, 5)

        metrics = await service.calculate_product_metrics("MLB123", sold_quantity=3)

        # With default 13% commission, no other costs
        assert metrics.ml_commission == 39.0  # 13% of 300
        assert metrics.product_cost_total == 0.0

    @pytest.mark.asyncio
    async def test_product_not_found(self):
        service = _make_service()
        service._product_repo.get_by_id.return_value = None

        with pytest.raises(LookupError):
            await service.calculate_product_metrics("MLB_NOT_FOUND")


class TestCostManagement:
    @pytest.mark.asyncio
    async def test_set_product_cost(self):
        service = _make_service()
        mock_cost = MagicMock()
        mock_cost.id = 1
        service._finance_repo.upsert_cost.return_value = mock_cost

        input_data = CostInput(
            product_id="MLB123",
            seller_id=123456789,
            product_cost=25.0,
            shipping_cost=5.0,
        )
        result = await service.set_product_cost(input_data)

        assert result["message"] == "Custo salvo com sucesso"
        assert result["cost_id"] == 1

    @pytest.mark.asyncio
    async def test_bulk_set_costs(self):
        service = _make_service()
        service._finance_repo.bulk_upsert_costs.return_value = [MagicMock(), MagicMock()]

        costs = [
            {"product_id": "MLB1", "product_cost": 10.0},
            {"product_id": "MLB2", "product_cost": 20.0},
        ]
        result = await service.bulk_set_costs(123456789, costs)

        assert "2 custos atualizados" in result["message"]

    @pytest.mark.asyncio
    async def test_get_cost_history(self):
        service = _make_service()
        history_item = MagicMock()
        history_item.id = 1
        history_item.old_product_cost = Decimal("30.00")
        history_item.new_product_cost = Decimal("25.00")
        history_item.old_tax_rate = Decimal("5.00")
        history_item.new_tax_rate = Decimal("0.00")
        history_item.old_ml_commission_rate = Decimal("13.00")
        history_item.new_ml_commission_rate = Decimal("13.00")
        history_item.changed_by = "api"
        history_item.recorded_at = MagicMock()
        history_item.recorded_at.isoformat.return_value = "2026-07-04T20:00:00"

        service._finance_repo.get_cost_history.return_value = [history_item]

        result = await service.get_cost_history("MLB123")

        assert len(result) == 1
        assert result[0]["new_product_cost"] == 25.0


class TestSellerSummary:
    @pytest.mark.asyncio
    async def test_summary_with_data(self):
        service = _make_service()
        service._finance_repo.get_seller_summary.return_value = {
            "total_gross_revenue": 10000,
            "total_net_revenue": 8500,
            "total_net_profit": 3000,
            "total_ml_commission": 1300,
            "total_shipping": 500,
            "total_ads": 200,
            "total_taxes": 0,
            "total_product_cost": 3000,
            "total_inventory_value": 5000,
            "total_products": 12,
            "total_units_sold": 100,
            "margin_pct": 30.0,
            "roi": 75.0,
            "roas": 50.0,
        }
        service._finance_repo.get_all_costs_by_seller.return_value = []

        summary = await service.calculate_seller_summary(123456789, days=30)

        assert summary.total_gross_revenue == 10000
        assert summary.total_products == 12
        assert summary.margin_pct == 30.0

    @pytest.mark.asyncio
    async def test_summary_no_data(self):
        service = _make_service()
        service._finance_repo.get_seller_summary.return_value = {
            "total_gross_revenue": None,
            "total_net_revenue": None,
            "total_net_profit": None,
            "total_ml_commission": None,
            "total_shipping": None,
            "total_ads": None,
            "total_taxes": None,
            "total_product_cost": None,
            "total_inventory_value": None,
            "total_products": 0,
            "total_units_sold": None,
            "margin_pct": 0,
            "roi": 0,
            "roas": 0,
        }
        service._finance_repo.get_all_costs_by_seller.return_value = []

        summary = await service.calculate_seller_summary(123456789, days=30)

        assert summary.total_products == 0
        assert summary.margin_pct == 0
        assert summary.roi == 0


class TestFinancialMetricsDTO:
    def test_to_dict_structure(self):
        metrics = FinancialMetrics(
            product_id="MLB123",
            price=100.0,
            available_quantity=10,
            sold_quantity=5,
            gross_revenue=500.0,
            net_revenue=435.0,
            product_cost_total=150.0,
            ml_commission=65.0,
            shipping_total=25.0,
            ads_total=10.0,
            taxes_total=0.0,
            total_cost=250.0,
            gross_profit=350.0,
            net_profit=250.0,
            margin_pct=50.0,
            roi=100.0,
            roas=50.0,
            break_even_units=2,
            inventory_value=300.0,
        )
        d = metrics.to_dict()

        assert d["product_id"] == "MLB123"
        assert d["price"] == 100.0
        assert d["gross_revenue"] == 500.0
        assert d["costs"]["product_cost"] == 150.0
        assert d["costs"]["ml_commission"] == 65.0
        assert d["profit"]["margin_pct"] == 50.0
        assert d["indicators"]["roi"] == 100.0
        assert d["indicators"]["roas"] == 50.0
        assert d["indicators"]["break_even_units"] == 2
