"""
providers/mercadolivre.py

Implementação do MarketplaceProvider para o Mercado Livre (MLB).

Utiliza a API oficial pública + Playwright como fallback
para dados não disponíveis via API.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import httpx

from config.settings import settings
from providers.base import (
    CategoryTree,
    Currency,
    ListingType,
    MarketplaceProvider,
    ProductCondition,
    ProductListing,
    ProductPage,
    ProviderConfig,
    SearchResultPage,
    SellerPage,
)
from utils.logger import logger


class MercadoLivreProvider(MarketplaceProvider):
    """Provider oficial do Mercado Livre (site MLB - Brasil).

    API Base: https://api.mercadolibre.com
    """

    BASE_URL = "https://api.mercadolibre.com"
    SITE_ID = "MLB"

    def __init__(self, config: Optional[ProviderConfig] = None) -> None:
        self.config = config or ProviderConfig()
        self._base_headers = {
            "User-Agent": settings.CRAWLER_USER_AGENT,
            "Accept": "application/json",
        }

    # ── Identificação ───────────────────────────────────────────────────

    @property
    def name(self) -> str:
        return "mercadolivre"

    @property
    def supported_currencies(self) -> list[Currency]:
        return [Currency.BRL, Currency.ARS, Currency.MXN, Currency.COP, Currency.CLP, Currency.PEN, Currency.UYU]

    # ── 1. Categorias ──────────────────────────────────────────────────

    async def get_root_categories(self) -> list[CategoryTree]:
        data = await self._get(f"/sites/{self.SITE_ID}/categories")
        return [
            CategoryTree(id=cat["id"], name=cat["name"])
            for cat in data
        ]

    async def get_subcategories(self, category_id: str) -> list[CategoryTree]:
        data = await self._get(f"/categories/{category_id}")
        children = data.get("children", [])
        return [
            CategoryTree(
                id=c["id"],
                name=c["name"],
                parent_id=category_id,
                total_items=c.get("total_items_in_this_category"),
            )
            for c in children
        ]

    async def get_category_path(self, category_id: str) -> list[dict]:
        data = await self._get(f"/categories/{category_id}")
        return data.get("path_from_root", [])

    async def get_category_detail(self, category_id: str) -> Optional[dict]:
        """Retorna dados brutos da categoria (incluindo settings, atributos)."""
        try:
            return await self._get(f"/categories/{category_id}")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise

    # ── 2. Produtos ────────────────────────────────────────────────────

    async def get_products_by_category(
        self,
        category_id: str,
        offset: int = 0,
        limit: int = 50,
    ) -> list[ProductListing]:
        data = await self._get(
            f"/sites/{self.SITE_ID}/search?"
            f"category={category_id}&offset={offset}&limit={limit}"
        )
        return self._parse_search_results(data)

    async def get_product_detail(self, product_id: str) -> Optional[ProductPage]:
        try:
            data = await self._get(f"/items/{product_id}")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise

        return self._parse_product(data)

    async def get_product_description(self, product_id: str) -> Optional[str]:
        try:
            data = await self._get(f"/items/{product_id}/description")
            return data.get("plain_text", "")
        except httpx.HTTPStatusError:
            return None

    # ── 3. Vendedores ──────────────────────────────────────────────────

    async def get_seller_detail(self, seller_id: int) -> Optional[SellerPage]:
        try:
            data = await self._get(f"/users/{seller_id}")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise

        return SellerPage(
            id=data["id"],
            nickname=data.get("nickname", ""),
            registration_date=self._parse_date(data.get("registration_date")),
            seller_reputation=data.get("seller_reputation", {}).get("level_id"),
            transactions_total=data.get("seller_reputation", {}).get("transactions", {}).get("total"),
            transactions_completed=data.get("seller_reputation", {}).get("transactions", {}).get("completed"),
            rating_average=data.get("seller_reputation", {}).get("power_seller_status") == "gold",  # proxy
            country=data.get("country", {}).get("id"),
            tags=data.get("tags", []),
            crawled_at=datetime.now(timezone.utc),
        )

    # ── 4. Busca ───────────────────────────────────────────────────────

    async def search(
        self,
        query: str,
        category_id: Optional[str] = None,
        offset: int = 0,
        limit: int = 50,
    ) -> SearchResultPage:
        path = f"/sites/{self.SITE_ID}/search?q={query}&offset={offset}&limit={limit}"
        if category_id:
            path += f"&category={category_id}"
        data = await self._get(path)

        results = self._parse_search_results(data)
        return SearchResultPage(
            query=query,
            results=results,
            total=data.get("paging", {}).get("total", 0),
            page=offset // limit + 1 if limit else 1,
            page_size=limit,
            filters=data.get("available_filters"),
        )

    async def search_by_category(
        self,
        category_id: str,
        offset: int = 0,
        limit: int = 50,
    ) -> Optional[dict]:
        """Retorna JSON bruto da busca por categoria (usado pelo scanner)."""
        try:
            return await self._get(
                f"/sites/{self.SITE_ID}/search?"
                f"category={category_id}&offset={offset}&limit={limit}"
            )
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise

    # ── 5. Métodos auxiliares ──────────────────────────────────────────

    async def get_category_total_items(self, category_id: str) -> int:
        """Retorna o total de anúncios em uma categoria (sem carregar resultados)."""
        data = await self._get(
            f"/sites/{self.SITE_ID}/search?"
            f"category={category_id}&offset=0&limit=1"
        )
        return data.get("paging", {}).get("total", 0)

    # ── Internos ───────────────────────────────────────────────────────

    async def _get(self, path: str) -> dict:
        async with httpx.AsyncClient(
            headers=self._base_headers,
            timeout=httpx.Timeout(15.0),
            follow_redirects=True,
        ) as client:
            response = await client.get(f"{self.BASE_URL}{path}")
            response.raise_for_status()
            return response.json()

    def _parse_search_results(self, data: dict) -> list[ProductListing]:
        results: list[ProductListing] = []
        for item in data.get("results", []):
            listing = self._parse_listing(item)
            if listing:
                results.append(listing)
        return results

    def _parse_listing(self, item: dict) -> Optional[ProductListing]:
        try:
            return ProductListing(
                id=item["id"],
                title=item.get("title", ""),
                price=float(item.get("price", 0)),
                currency=Currency(item.get("currency_id", "BRL")),
                available_quantity=item.get("available_quantity", 0),
                seller_id=item.get("seller", {}).get("id"),
                seller_nickname=item.get("seller", {}).get("nickname"),
                category_id=item.get("category_id"),
                condition=ProductCondition(item.get("condition", "new")),
                listing_type=ListingType(item.get("listing_type_id", "free")),
                thumbnail=item.get("thumbnail"),
                permalink=item.get("permalink"),
                installments=item.get("installments"),
                shipping_free=item.get("shipping", {}).get("free_shipping", False),
                original_price=(
                    float(item["original_price"])
                    if item.get("original_price") else None
                ),
                review_count=item.get("reviews", {}).get("total", 0),
                review_average=(
                    float(item["reviews"]["average"])
                    if item.get("reviews") and item["reviews"].get("average")
                    else None
                ),
                crawled_at=datetime.now(timezone.utc),
            )
        except (KeyError, ValueError, TypeError) as exc:
            logger.warning("Erro ao parsear listing", item_id=item.get("id"), error=str(exc))
            return None

    def _parse_product(self, data: dict) -> ProductPage:
        return ProductPage(
            id=data["id"],
            title=data.get("title", ""),
            price=float(data.get("price", 0)),
            currency=Currency(data.get("currency_id", "BRL")),
            available_quantity=data.get("available_quantity", 0),
            condition=ProductCondition(data.get("condition", "new")),
            listing_type=ListingType(data.get("listing_type_id", "free")),
            seller_id=data.get("seller_id"),
            seller_nickname=data.get("seller", {}).get("nickname") if data.get("seller") else None,
            category_id=data.get("category_id"),
            category_path=data.get("path_from_root"),
            description=None,
            thumbnail=data.get("thumbnail"),
            pictures=[p["url"] for p in data.get("pictures", [])],
            permalink=data.get("permalink"),
            original_price=(
                float(data["original_price"])
                if data.get("original_price") else None
            ),
            shipping_free=data.get("shipping", {}).get("free_shipping", False),
            shipping_cost=(
                float(data["shipping"]["cost"])
                if data.get("shipping") and data["shipping"].get("cost")
                else None
            ),
            weight=(
                float(data["weight"])
                if data.get("weight") else None
            ),
            review_count=data.get("reviews", {}).get("total", 0),
            review_average=(
                float(data["reviews"]["average"])
                if data.get("reviews") and data["reviews"].get("average")
                else None
            ),
            questions_count=data.get("question_count", 0),
            tags=data.get("tags", []),
            attributes={
                attr["id"]: attr.get("value_name") or attr.get("values", [{}])[0].get("name")
                for attr in data.get("attributes", [])
                if attr.get("id")
            },
            crawled_at=datetime.now(timezone.utc),
        )

    def _parse_date(self, value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        try:
            if value.endswith("Z"):
                value = value[:-1] + "+00:00"
            return datetime.fromisoformat(value)
        except (ValueError, TypeError):
            return None
