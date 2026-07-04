"""
services/sync_service.py

Servico de sincronizacao de dados do Mercado Livre.

Responsabilidades:
  - Sincronizar anuncios do vendedor autenticado
  - Buscar detalhes de itens especificos
  - Monitorar concorrentes cadastrados
  - Coletar tendencias de mercado

Todas as chamadas usam endpoints autenticados, sem depender de /sites/MLB/search.
"""

import json
from datetime import datetime, timezone
from typing import Optional
import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from services.token_service import TokenService
from repositories.competitor import CompetitorRepository
from repositories.market_trend import MarketTrendRepository
from utils.logger import logger


class SyncService:
    """
    Servico de sincronizacao de dados do ML.

    Uso:
        async with AsyncSessionLocal() as session:
            sync = SyncService(session)
            items = await sync.fetch_seller_items(user_id=123)
    """

    BASE_URL = "https://api.mercadolibre.com"

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._token_svc = TokenService(session)
        self._competitor_repo = CompetitorRepository(session)
        self._trend_repo = MarketTrendRepository(session)

    # ── Anuncios do vendedor ──────────────────────────────────────────

    async def fetch_seller_items(
        self, user_id: int, status: str = "active", limit: int = 50
    ) -> list[str]:
        """
        Busca IDs de anuncios do vendedor via /users/{id}/items/search.
        Retorna lista de item_ids.
        """
        token = await self._token_svc.get_valid_token("mercadolivre", user_id)
        headers = {"Authorization": f"Bearer {token}"}

        all_ids: list[str] = []
        offset = 0

        while True:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{self.BASE_URL}/users/{user_id}/items/search",
                    headers=headers,
                    params={"status": status, "limit": limit, "offset": offset},
                )
                resp.raise_for_status()
                data = resp.json()

            results = data.get("results", [])
            all_ids.extend(results)

            total = data.get("paging", {}).get("total", 0)
            offset += limit
            if offset >= total or not results:
                break

        logger.info(f"Seller {user_id}: {len(all_ids)} items ({status})")
        return all_ids

    async def fetch_item_detail(self, item_id: str, user_id: int) -> Optional[dict]:
        """
        Busca detalhes de um item via /items/{id}.
        """
        token = await self._token_svc.get_valid_token("mercadolivre", user_id)
        headers = {"Authorization": f"Bearer {token}"}

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{self.BASE_URL}/items/{item_id}", headers=headers
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()

    async def fetch_items_batch(
        self, item_ids: list[str], user_id: int
    ) -> list[dict]:
        """
        Busca multiplos itens via /items?ids=... (multiget, max 20 por chamada).
        """
        token = await self._token_svc.get_valid_token("mercadolivre", user_id)
        headers = {"Authorization": f"Bearer {token}"}
        all_items: list[dict] = []

        for i in range(0, len(item_ids), 20):
            batch = item_ids[i : i + 20]
            ids_param = ",".join(batch)
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{self.BASE_URL}/items", headers=headers, params={"ids": ids_param}
                )
                resp.raise_for_status()
                for item_resp in resp.json():
                    if item_resp.get("code") == 200:
                        all_items.append(item_resp["body"])

        return all_items

    # ── Concorrentes ──────────────────────────────────────────────────

    async def monitor_competitor(self, ml_item_id: str, user_id: int) -> dict:
        """
        Monitora um concorrente: busca dados atuais e registra alteracoes.
        """
        token = await self._token_svc.get_valid_token("mercadolivre", user_id)
        headers = {"Authorization": f"Bearer {token}"}

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{self.BASE_URL}/items/{ml_item_id}", headers=headers
            )
            if resp.status_code == 404:
                return {"error": "Item nao encontrado"}
            resp.raise_for_status()
            item_data = resp.json()

        competitor = await self._competitor_repo.get_by_ml_item_id(ml_item_id)

        if not competitor:
            competitor = await self._competitor_repo.create(
                ml_item_id=ml_item_id,
                title=item_data.get("title", ""),
                seller_id=item_data.get("seller_id"),
                category_id=item_data.get("category_id"),
                current_price=float(item_data.get("price", 0)),
                original_price=float(item_data["original_price"]) if item_data.get("original_price") else None,
                available_quantity=item_data.get("available_quantity"),
                status=item_data.get("status"),
                listing_type=item_data.get("listing_type_id"),
                shipping_free=item_data.get("shipping", {}).get("free_shipping", False),
                permalink=item_data.get("permalink"),
                thumbnail=item_data.get("thumbnail"),
            )
            return {"action": "created", "competitor_id": competitor.id}

        changes = await self._competitor_repo.update_from_api(competitor, item_data)
        return {
            "action": "updated",
            "competitor_id": competitor.id,
            "changes": len(changes),
            "change_details": [{"type": c.change_type, "detail": c.change_detail} for c in changes],
        }

    # ── Tendencias ────────────────────────────────────────────────────

    async def fetch_trends(self) -> list[dict]:
        """
        Busca tendencias via /trends/MLB.
        Nota: este endpoint pode retornar 403 para apps nao certificados.
        """
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(f"{self.BASE_URL}/trends/MLB")
                if resp.status_code == 403:
                    logger.warning("Trends endpoint returned 403 - app not certified")
                    return []
                resp.raise_for_status()
                trends = resp.json()

            saved = []
            for t in trends:
                keyword = t.get("keyword", "")
                if keyword:
                    await self._trend_repo.upsert(
                        keyword=keyword,
                        trend_type="keyword",
                    )
                    saved.append(keyword)

            logger.info(f"Tendencias coletadas: {len(saved)}")
            return trends
        except httpx.HTTPStatusError as e:
            logger.warning(f"Erro ao buscar tendencias: {e.response.status_code}")
            return []

    async def fetch_domain_discovery(self, query: str) -> list[dict]:
        """
        Busca categorias relacionadas a um termo via domain_discovery.
        """
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{self.BASE_URL}/sites/MLB/domain_discovery/search",
                params={"q": query, "limit": 5},
            )
            resp.raise_for_status()
            return resp.json()

    # ── Categorias ────────────────────────────────────────────────────

    async def fetch_categories(self) -> list[dict]:
        """
        Busca categorias via domain_discovery com query ampla.
        Nota: /sites/MLB/categories retorna 403, entao usamos domain_discovery.
        """
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"{self.BASE_URL}/sites/MLB/domain_discovery/search",
                    params={"q": "produtos", "limit": 50},
                )
                if resp.status_code == 403:
                    logger.warning("Categories endpoint returned 403")
                    return []
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as e:
            logger.warning(f"Erro ao buscar categorias: {e.response.status_code}")
            return []

    async def fetch_category_detail(self, category_id: str) -> Optional[dict]:
        """
        Busca detalhes de uma categoria (endpoint publico).
        """
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"{self.BASE_URL}/categories/{category_id}")
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()

    # ── Metricas do vendedor ──────────────────────────────────────────

    async def fetch_seller_metrics(self, user_id: int) -> Optional[dict]:
        """
        Busca metricas do vendedor (se disponivel via API).
        """
        token = await self._token_svc.get_valid_token("mercadolivre", user_id)
        headers = {"Authorization": f"Bearer {token}"}

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{self.BASE_URL}/users/{user_id}", headers=headers
            )
            if resp.status_code != 200:
                return None
            data = resp.json()

        reputation = data.get("seller_reputation", {})
        return {
            "user_id": user_id,
            "nickname": data.get("nickname"),
            "level_id": reputation.get("level_id"),
            "transactions_total": reputation.get("transactions", {}).get("total"),
            "transactions_completed": reputation.get("transactions", {}).get("completed"),
            "transactions_canceled": reputation.get("transactions", {}).get("canceled"),
            "power_seller_status": reputation.get("power_seller_status"),
            "metrics": reputation.get("metrics", {}),
        }
