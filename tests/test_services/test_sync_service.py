"""
tests/test_services/test_sync_service.py

Testes unitarios do SyncService.

Coberturas:
  1. fetch_seller_items: busca IDs de anuncios do vendedor
  2. fetch_item_detail: busca detalhes de um item
  3. fetch_item_detail: retorna None para 404
  4. fetch_items_batch: busca multiplos itens (multiget)
  5. monitor_competitor: cria novo concorrente
  6. monitor_competitor: atualiza concorrente existente
  7. fetch_trends: busca e salva tendencias
  8. fetch_domain_discovery: busca categorias por termo
  9. fetch_categories: busca categorias raiz
  10. fetch_category_detail: busca detalhe de categoria
  11. fetch_seller_metrics: busca metricas do vendedor
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.sync_service import SyncService


def _make_service():
    session = AsyncMock()
    service = SyncService(session)
    service._token_svc = AsyncMock()
    service._competitor_repo = AsyncMock()
    service._trend_repo = AsyncMock()
    return service


def _mock_response(status_code=200, json_data=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    return resp


class TestFetchSellerItems:
    @pytest.mark.asyncio
    async def test_fetches_all_item_ids(self):
        service = _make_service()
        service._token_svc.get_valid_token.return_value = "test-token"

        page1 = {"results": ["MLB1", "MLB2"], "paging": {"total": 3}}
        page2 = {"results": ["MLB3"], "paging": {"total": 3}}

        with patch("services.sync_service.httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(side_effect=[
                _mock_response(200, page1),
                _mock_response(200, page2),
            ])
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = mock_instance

            ids = await service.fetch_seller_items(user_id=123, limit=2)

        assert ids == ["MLB1", "MLB2", "MLB3"]
        service._token_svc.get_valid_token.assert_called_once_with("mercadolivre", 123)


class TestFetchItemDetail:
    @pytest.mark.asyncio
    async def test_returns_item_data(self):
        service = _make_service()
        service._token_svc.get_valid_token.return_value = "test-token"

        item_data = {"id": "MLB123", "title": "Test Item", "price": 100}

        with patch("services.sync_service.httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=_mock_response(200, item_data))
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = mock_instance

            result = await service.fetch_item_detail("MLB123", user_id=123)

        assert result == item_data

    @pytest.mark.asyncio
    async def test_returns_none_for_404(self):
        service = _make_service()
        service._token_svc.get_valid_token.return_value = "test-token"

        with patch("services.sync_service.httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=_mock_response(404))
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = mock_instance

            result = await service.fetch_item_detail("MLB_NOT_FOUND", user_id=123)

        assert result is None


class TestFetchItemsBatch:
    @pytest.mark.asyncio
    async def test_fetches_multiple_items_in_batches(self):
        service = _make_service()
        service._token_svc.get_valid_token.return_value = "test-token"

        batch_response = [
            {"code": 200, "body": {"id": "MLB1", "title": "Item 1"}},
            {"code": 200, "body": {"id": "MLB2", "title": "Item 2"}},
        ]

        with patch("services.sync_service.httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=_mock_response(200, batch_response))
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = mock_instance

            items = await service.fetch_items_batch(["MLB1", "MLB2"], user_id=123)

        assert len(items) == 2
        assert items[0]["id"] == "MLB1"


class TestMonitorCompetitor:
    @pytest.mark.asyncio
    async def test_creates_new_competitor(self):
        service = _make_service()
        service._token_svc.get_valid_token.return_value = "test-token"
        service._competitor_repo.get_by_ml_item_id.return_value = None

        item_data = {
            "id": "MLB_COMP",
            "title": "Competitor Item",
            "seller_id": 999,
            "category_id": "MLB123",
            "price": 150.0,
            "original_price": 200.0,
            "available_quantity": 10,
            "status": "active",
            "listing_type_id": "gold_pro",
            "shipping": {"free_shipping": True},
            "permalink": "http://mercadolivre.com/Item",
            "thumbnail": "http://img.com/item.jpg",
        }
        mock_competitor = MagicMock()
        mock_competitor.id = 1
        service._competitor_repo.create.return_value = mock_competitor

        with patch("services.sync_service.httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=_mock_response(200, item_data))
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = mock_instance

            result = await service.monitor_competitor("MLB_COMP", user_id=123)

        assert result["action"] == "created"
        assert result["competitor_id"] == 1
        service._competitor_repo.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_updates_existing_competitor(self):
        service = _make_service()
        service._token_svc.get_valid_token.return_value = "test-token"

        mock_competitor = MagicMock()
        mock_competitor.id = 5
        service._competitor_repo.get_by_ml_item_id.return_value = mock_competitor

        change_mock = MagicMock()
        change_mock.change_type = "price_change"
        change_mock.change_detail = "150 -> 140"
        service._competitor_repo.update_from_api.return_value = [change_mock]

        item_data = {"id": "MLB_COMP", "price": 140.0}

        with patch("services.sync_service.httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=_mock_response(200, item_data))
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = mock_instance

            result = await service.monitor_competitor("MLB_COMP", user_id=123)

        assert result["action"] == "updated"
        assert result["changes"] == 1
        assert result["change_details"][0]["type"] == "price_change"


class TestFetchTrends:
    @pytest.mark.asyncio
    async def test_saves_trends(self):
        service = _make_service()

        trends = [
            {"keyword": "celular"},
            {"keyword": "notebook"},
        ]

        with patch("services.sync_service.httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=_mock_response(200, trends))
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = mock_instance

            result = await service.fetch_trends()

        assert len(result) == 2
        assert service._trend_repo.upsert.call_count == 2


class TestFetchDomainDiscovery:
    @pytest.mark.asyncio
    async def test_returns_categories(self):
        service = _make_service()

        categories = [{"category_id": "MLB123", "name": "Eletronicos"}]

        with patch("services.sync_service.httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=_mock_response(200, categories))
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = mock_instance

            result = await service.fetch_domain_discovery("celular")

        assert result == categories


class TestFetchCategories:
    @pytest.mark.asyncio
    async def test_returns_root_categories(self):
        service = _make_service()

        categories = [{"id": "MLB1", "name": "Eletronicos"}]

        with patch("services.sync_service.httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=_mock_response(200, categories))
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = mock_instance

            result = await service.fetch_categories()

        assert result == categories


class TestFetchCategoryDetail:
    @pytest.mark.asyncio
    async def test_returns_category_detail(self):
        service = _make_service()

        detail = {"id": "MLB123456", "name": "Celulares", "total_items_in_this_category": 1000}

        with patch("services.sync_service.httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=_mock_response(200, detail))
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = mock_instance

            result = await service.fetch_category_detail("MLB123456")

        assert result == detail

    @pytest.mark.asyncio
    async def test_returns_none_for_404(self):
        service = _make_service()

        with patch("services.sync_service.httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=_mock_response(404))
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = mock_instance

            result = await service.fetch_category_detail("MLB_NOT_FOUND")

        assert result is None


class TestFetchSellerMetrics:
    @pytest.mark.asyncio
    async def test_returns_formatted_metrics(self):
        service = _make_service()
        service._token_svc.get_valid_token.return_value = "test-token"

        user_data = {
            "nickname": "vendedor_teste",
            "seller_reputation": {
                "level_id": "5_green",
                "transactions": {"total": 100, "completed": 95, "canceled": 5},
                "power_seller_status": "platinum",
                "metrics": {"sales": {"completed": 95}},
            },
        }

        with patch("services.sync_service.httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=_mock_response(200, user_data))
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = mock_instance

            result = await service.fetch_seller_metrics(user_id=123)

        assert result["nickname"] == "vendedor_teste"
        assert result["level_id"] == "5_green"
        assert result["transactions_total"] == 100
        assert result["power_seller_status"] == "platinum"

    @pytest.mark.asyncio
    async def test_returns_none_for_error(self):
        service = _make_service()
        service._token_svc.get_valid_token.return_value = "test-token"

        with patch("services.sync_service.httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get = AsyncMock(return_value=_mock_response(500))
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = mock_instance

            result = await service.fetch_seller_metrics(user_id=123)

        assert result is None
