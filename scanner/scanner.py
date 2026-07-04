"""
scanner/scanner.py

Market Scanner — varredura contínua de categorias e descoberta de produtos.

Fluxo:
  1. Carrega árvore de categorias do provider
  2. Para cada categoria folha, calcula frequência de varredura
  3. Varre produtos com paginação
  4. Descobre novos produtos, identifica removidos
  5. Enfileira produtos novos/alterados para coleta
  6. Atualiza estado do scan

O scanner NÃO coleta dados — apenas descobre e enfileira.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from providers.base import MarketplaceProvider
from scanner.models import (
    CategoryScanFrequency,
    CategoryScanState,
    Discovery,
    ScanResult,
)
from utils.logger import logger
from observability.metrics import platform_metrics


# ─────────────────────────────────────────────────────────────────────────────
# Limiares para frequência de varredura
# ─────────────────────────────────────────────────────────────────────────────

HIGH_TRAFFIC_ITEMS = 10000     # >= 10k anúncios → alta frequência
MEDIUM_TRAFFIC_ITEMS = 1000    # >= 1k anúncios → média frequência

# Intervalos entre varreduras (horas)
SCAN_INTERVALS: dict[CategoryScanFrequency, int] = {
    CategoryScanFrequency.HIGH: 6,
    CategoryScanFrequency.MEDIUM: 24,
    CategoryScanFrequency.LOW: 72,
    CategoryScanFrequency.ONCE: 0,  # não repetir
}

MAX_PRODUCTS_PER_CATEGORY = 1000  # limite de produtos a descobrir por categoria


class MarketScanner:
    """Scanner contínuo de marketplace.

    Uso:
        scanner = MarketScanner(provider, session_factory)
        result = await scanner.scan_all_categories()
    """

    def __init__(
        self,
        provider: MarketplaceProvider,
        session_factory,
        max_concurrency: int = 5,
    ) -> None:
        self._provider = provider
        self._session_factory = session_factory
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._categories: list[CategoryScanState] = []
        self._known_products: set[str] = set()

    # ── API pública ────────────────────────────────────────────────────

    async def initialize(self) -> None:
        """Carrega a árvore completa de categorias do provider."""
        logger.info(
            "Inicializando scanner",
            provider=self._provider.name,
        )
        roots = await self._provider.get_root_categories()
        self._categories = []
        for root in roots:
            tree = await self._build_category_tree(root)
            self._categories.append(tree)

        total = self._count_leaves(self._categories)
        logger.info(
            "Arvore de categorias carregada",
            root_categories=len(roots),
            leaf_categories=total,
        )

    async def scan_all_categories(self) -> ScanResult:
        """Varre todas as categorias que precisam de scan."""
        platform_metrics.scans_total.inc()
        result = ScanResult()
        result.started_at = datetime.now(timezone.utc)

        if not self._categories:
            await self.initialize()

        leaves = self._get_due_leaves(self._categories)
        logger.info("Iniciando varredura", categories_due=len(leaves))

        # Carrega produtos conhecidos do banco para detectar novidades
        await self._load_known_products()

        tasks = []
        for leaf in leaves:
            tasks.append(self._scan_category(leaf, result))

        await asyncio.gather(*tasks)

        result.finished_at = datetime.now(timezone.utc)
        result.duration_seconds = (result.finished_at - result.started_at).total_seconds()

        platform_metrics.products_collected_total.inc(result.new_products)
        platform_metrics.requests_total.inc(result.categories_scanned)

        logger.info(
            "Varredura concluida",
            categories=result.categories_scanned,
            new_products=result.new_products,
            removed=result.removed_products,
            duration=round(result.duration_seconds, 1),
        )

        return result

    async def scan_category(self, category_id: str) -> ScanResult:
        """Varre uma categoria específica."""
        result = ScanResult()
        result.started_at = datetime.now(timezone.utc)
        await self._load_known_products()

        leaf = self._find_category(category_id, self._categories)
        if not leaf:
            logger.warning("Categoria nao encontrada na arvore", category_id=category_id)
            return result

        await self._scan_category(leaf, result)
        result.finished_at = datetime.now(timezone.utc)
        result.duration_seconds = (result.finished_at - result.started_at).total_seconds()
        return result

    def get_category_tree(self) -> list[CategoryScanState]:
        """Retorna a árvore de categorias com estados."""
        return self._categories

    def get_due_categories(self) -> list[CategoryScanState]:
        """Retorna categorias que precisam ser varridas agora."""
        return self._get_due_leaves(self._categories)

    # ── Varredura ──────────────────────────────────────────────────────

    async def _scan_category(self, cat: CategoryScanState, result: ScanResult) -> None:
        async with self._semaphore:
            t0 = time.monotonic()
            try:
                logger.debug("Varrendo categoria", category=cat.category_id, name=cat.category_name)
                cat.scan_count += 1

                discovered = await self._discover_products(cat.category_id)

                # Produtos encontrados
                for prod in discovered:
                    result.products_found += 1
                    if prod.product_id not in self._known_products:
                        result.new_products += 1
                        self._known_products.add(prod.product_id)
                        await self._enqueue_product(prod.product_id)

                    if prod.seller_id:
                        result.new_sellers.add(prod.seller_id)

                result.categories_scanned += 1

                platform_metrics.record_category_duration(cat.category_id, time.monotonic() - t0)

                # Atualiza estado
                cat.last_scanned_at = datetime.now(timezone.utc)
                cat.next_scan_at = self._compute_next_scan(cat)

            except Exception as exc:
                platform_metrics.collection_errors_total.inc()
                platform_metrics.record_category_duration(cat.category_id, time.monotonic() - t0)
                logger.error(
                    "Erro ao varrer categoria",
                    category=cat.category_id,
                    error=str(exc),
                )
                result.errors += 1

    async def _discover_products(self, category_id: str) -> list[Discovery]:
        """Descobre produtos em uma categoria via paginação."""
        discoveries: list[Discovery] = []
        offset = 0
        limit = 50

        while offset < MAX_PRODUCTS_PER_CATEGORY:
            data = await self._provider.search_by_category(
                category_id, offset=offset, limit=limit,
            )
            if not data:
                break

            results = data.get("results", [])
            if not results:
                break

            for item in results:
                seller = item.get("seller") or {}
                discoveries.append(Discovery(
                    product_id=item["id"],
                    title=item.get("title", ""),
                    category_id=category_id,
                    seller_id=seller.get("id"),
                    price=float(item.get("price", 0)),
                    is_new=item["id"] not in self._known_products,
                ))

            total = data.get("paging", {}).get("total", 0)
            offset += limit
            if offset >= total:
                break

        return discoveries

    async def _enqueue_product(self, product_id: str) -> None:
        """Registra produto para coleta futura (Redis Stream ou banco)."""
        async with self._session_factory() as session:
            from models.product import Product as ProductModel
            exists = await session.get(ProductModel, product_id)
            if not exists:
                session.add(ProductModel(
                    id=product_id,
                    title="",  # será preenchido na coleta
                    seller_id=0,
                    source=self._provider.name,
                    hash=product_id,
                ))
                await session.commit()

    # ── Construção da árvore ───────────────────────────────────────────

    async def _build_category_tree(
        self,
        cat,
        parent_id: Optional[str] = None,
    ) -> CategoryScanState:
        children = await self._provider.get_subcategories(cat.id)
        path = await self._provider.get_category_path(cat.id)
        total_items = cat.total_items or 0
        if total_items == 0:
            try:
                total_items = await self._provider.get_category_total_items(cat.id)
            except Exception:
                pass

        child_states: list[CategoryScanState] = []
        for child in children:
            child_state = await self._build_category_tree(child, parent_id=cat.id)
            child_states.append(child_state)

        # Se tem filhos, total_items é a soma dos filhos
        if child_states:
            total_items = sum(c.total_items for c in child_states)

        frequency = self._compute_frequency(total_items)

        return CategoryScanState(
            category_id=cat.id,
            category_name=cat.name,
            parent_id=parent_id,
            path=path,
            total_items=total_items,
            frequency=frequency,
            is_leaf=len(child_states) == 0,
            children=child_states,
            next_scan_at=datetime.now(timezone.utc),  # pronto para scan inicial
        )

    # ── Helpers ────────────────────────────────────────────────────────

    def _compute_frequency(self, total_items: int) -> CategoryScanFrequency:
        if total_items >= HIGH_TRAFFIC_ITEMS:
            return CategoryScanFrequency.HIGH
        elif total_items >= MEDIUM_TRAFFIC_ITEMS:
            return CategoryScanFrequency.MEDIUM
        elif total_items > 0:
            return CategoryScanFrequency.LOW
        return CategoryScanFrequency.ONCE

    def _compute_next_scan(self, cat: CategoryScanState) -> datetime:
        interval_hours = SCAN_INTERVALS.get(cat.frequency, 24)
        return datetime.now(timezone.utc) + timedelta(hours=interval_hours)

    def _count_leaves(self, categories: list[CategoryScanState]) -> int:
        count = 0
        for cat in categories:
            if cat.is_leaf:
                count += 1
            else:
                count += self._count_leaves(cat.children)
        return count

    def _get_due_leaves(
        self,
        categories: list[CategoryScanState],
    ) -> list[CategoryScanState]:
        due: list[CategoryScanState] = []
        now = datetime.now(timezone.utc)
        for cat in categories:
            if cat.is_leaf:
                if cat.frequency != CategoryScanFrequency.ONCE:
                    if cat.next_scan_at is None or now >= cat.next_scan_at:
                        due.append(cat)
            else:
                due.extend(self._get_due_leaves(cat.children))
        return due

    def _find_category(
        self,
        category_id: str,
        categories: list[CategoryScanState],
    ) -> Optional[CategoryScanState]:
        for cat in categories:
            if cat.category_id == category_id:
                return cat
            if cat.children:
                found = self._find_category(category_id, cat.children)
                if found:
                    return found
        return None

    async def _load_known_products(self) -> None:
        """Carrega IDs de produtos já conhecidos do banco."""
        async with self._session_factory() as session:
            from models.product import Product as ProductModel
            result = await session.execute(select(ProductModel.id))
            self._known_products = {row[0] for row in result.all()}
