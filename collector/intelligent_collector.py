"""
collector/intelligent_collector.py

Orquestrador central de coleta de dados do Mercado Livre.

Responsabilidades:
  1. Detectar mudanças via hash SHA-256 (sem snapshots completos)
  2. Controlar concorrência via RateLimiter + asyncio.Semaphore
  3. Gerir jobs e logs via CollectionJobRepository
  4. Delegar ingestão de eventos ao IngestionService
  5. Suportar 4 modos de coleta:
       by_product  — lista explícita de IDs
       by_category — paginação por categoria
       incremental — produtos não verificados recentemente
       full        — todos os produtos cadastrados

Feature: intelligent-collector
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, List, Optional

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from collector.cache_manager import CacheManager
from collector.metrics import MetricsCollector
from collector.modes import CollectionMode
from collector.rate_limiter import RateLimiter
from database.connection import AsyncSessionLocal
from models.product import Product
from repositories.collection_job import CollectionJobRepository
from services.ingestion import IngestionService
from utils.logger import logger
from observability.metrics import platform_metrics


class IntelligentCollector:
    """
    Coleta inteligente baseada em eventos — só grava o que mudou.

    Algoritmo por produto:
      1. Tenta obter payload do cache Redis
      2. Se miss → busca na API
      3. Computa SHA-256 do estado dinâmico
      4. Compara com hash armazenado no Redis
      5. Match   → skipped (sem escrita no banco)
      6. Mismatch → IngestionService → eventos inseridos → hash atualizado
    """

    _CATEGORY_PAGE_LIMIT = 50      # itens por página na busca por categoria
    _CATEGORY_MAX_ITEMS = 1000     # limite máximo de itens por categoria (R10.2)

    def __init__(
        self,
        api_client,                                   # MercadoLivreAPICollector
        cache_manager: Optional[CacheManager] = None,
        rate_limiter: RateLimiter = None,
        session_factory: Callable = AsyncSessionLocal,
        max_concurrency: int = 5,
        max_retries: int = 5,
        backoff_base: float = 2.0,
        max_wait: float = 60.0,
        api_rate_limit_delay: float = 0.5,
    ) -> None:
        self._api = api_client
        self._cache = cache_manager
        self._limiter = rate_limiter or RateLimiter(max_concurrency)
        self._session_factory = session_factory
        self._max_concurrency = max_concurrency
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._max_wait = max_wait
        self._api_rate_limit_delay = api_rate_limit_delay

    # ------------------------------------------------------------------ #
    # Ponto de entrada público                                            #
    # ------------------------------------------------------------------ #

    async def collect(
        self,
        mode: CollectionMode | str,
        product_ids: Optional[List[str]] = None,
        category_ids: Optional[List[str]] = None,
        incremental_threshold_hours: int = 6,
    ):
        """
        Executa um ciclo completo de coleta.

        Pre-flight validations (antes de criar o job):
          - by_product sem product_ids → ValueError, nenhum job criado (R2.7)

        Validações com job criado:
          - mode inválido → job status=failed, ValueError levantado (R2.6)

        Returns:
            CollectionJob com status final (done | partial | failed)
        """
        # Pre-flight: by_product sem IDs nunca cria job (R2.7)
        if mode == CollectionMode.BY_PRODUCT or mode == "by_product":
            if not product_ids:
                raise ValueError(
                    "mode=by_product requer product_ids não-vazio."
                )

        async with self._session_factory() as session:
            job_repo = CollectionJobRepository(session)
            metrics = MetricsCollector()

            # Valida mode antes de criar job — strings inválidas criam job failed (R2.6)
            valid_modes = {m.value for m in CollectionMode} | set(CollectionMode)
            if mode not in valid_modes:
                job = await job_repo.create_job(str(mode))
                await job_repo.set_running(job)
                metrics.start()
                metrics.finish()
                await job_repo.set_failed(job, metrics)
                raise ValueError(f"mode='{mode}' não reconhecido.")

            mode_enum = CollectionMode(mode) if isinstance(mode, str) else mode
            job = await job_repo.create_job(mode_enum.value)

            logger.info(
                "Coleta iniciada",
                job_id=job.id,
                collection_mode=mode_enum.value,
            )

            platform_metrics.collections_active.inc()
            platform_metrics.collections_total.inc()
            metrics.start()
            try:
                await job_repo.set_running(job)

                product_id_list = await self._resolve_items(
                    mode=mode_enum,
                    product_ids=product_ids,
                    category_ids=category_ids,
                    incremental_threshold_hours=incremental_threshold_hours,
                    job_id=job.id,
                    metrics=metrics,
                )

                job.total_items = len(product_id_list)
                await session.commit()

                await self._dispatch_workers(product_id_list, job, metrics)

                metrics.finish()
                platform_metrics.products_collected_total.inc(metrics.products)
                platform_metrics.events_generated_total.inc(metrics.events_generated)
                platform_metrics.requests_total.inc(metrics.requests)
                platform_metrics.requests_total.inc(1)  # includes count of skip API calls

                final_status = "partial" if metrics.errors else "done"
                if final_status == "done":
                    await job_repo.set_done(job, metrics)
                else:
                    await job_repo.set_partial(job, metrics)

            except Exception:
                metrics.finish()
                platform_metrics.collection_errors_total.inc()
                await job_repo.set_failed(job, metrics)
                raise
            finally:
                platform_metrics.collections_active.dec()

            logger.info(
                "Coleta finalizada",
                job_id=job.id,
                duration_seconds=metrics.duration_seconds,
                processed=metrics.products + metrics.errors,
                changes=metrics.changes,
                errors=metrics.errors,
                status=job.status,
            )
            return job

    # ------------------------------------------------------------------ #
    # Resolução de itens por modo                                         #
    # ------------------------------------------------------------------ #

    async def _resolve_items(
        self,
        mode: CollectionMode,
        product_ids: Optional[List[str]],
        category_ids: Optional[List[str]],
        incremental_threshold_hours: int,
        job_id: int,
        metrics: MetricsCollector,
    ) -> List[str]:
        if mode == CollectionMode.BY_PRODUCT:
            return list(product_ids or [])

        if mode == CollectionMode.BY_CATEGORY:
            return await self._resolve_by_category(category_ids or [], job_id, metrics)

        if mode == CollectionMode.INCREMENTAL:
            return await self._resolve_incremental(incremental_threshold_hours)

        if mode == CollectionMode.FULL:
            return await self._resolve_full()

        return []

    async def _resolve_by_category(
        self,
        category_ids: List[str],
        job_id: int,
        metrics: MetricsCollector,
    ) -> List[str]:
        """Pagina todos os produtos de cada categoria, deduplicando IDs."""
        seen: set[str] = set()
        result: List[str] = []

        for cat_id in category_ids:
            offset = 0
            cat_total = 0
            cap = self._CATEGORY_MAX_ITEMS

            while True:
                t0 = time.monotonic()
                page = await self._api.search_by_category(
                    category_id=cat_id,
                    offset=offset,
                    limit=self._CATEGORY_PAGE_LIMIT,
                )
                metrics.record_page_duration(time.monotonic() - t0)
                metrics.requests += 1

                if not page:
                    break

                paging = page.get("paging", {})
                api_total = paging.get("total", 0)
                effective_total = min(api_total, cap)

                items = page.get("results", [])
                if not items:
                    break

                for item in items:
                    pid = item.get("id") or item.get("item_id")
                    if pid and pid not in seen:
                        seen.add(pid)
                        result.append(pid)
                        cat_total += 1

                offset += len(items)

                if cat_total >= effective_total or offset >= effective_total:
                    if api_total > cap:
                        logger.info(
                            "Categoria truncada ao limite máximo",
                            category_id=cat_id,
                            api_total=api_total,
                            cap=cap,
                        )
                    break

        return result

    async def _resolve_incremental(self, threshold_hours: int) -> List[str]:
        """Retorna produtos nunca verificados ou verificados antes do threshold."""
        threshold = datetime.now(timezone.utc) - timedelta(hours=threshold_hours)
        async with self._session_factory() as session:
            query = select(Product.id).where(
                (Product.last_checked_at.is_(None))
                | (Product.last_checked_at < threshold)
            )
            result = await session.execute(query)
            return list(result.scalars().all())

    async def _resolve_full(self) -> List[str]:
        """Retorna todos os IDs de produtos em batches de 500 para evitar OOM."""
        ids: List[str] = []
        batch = 500
        offset = 0
        async with self._session_factory() as session:
            while True:
                query = select(Product.id).order_by(Product.id).offset(offset).limit(batch)
                result = await session.execute(query)
                chunk = list(result.scalars().all())
                if not chunk:
                    break
                ids.extend(chunk)
                offset += len(chunk)
        return ids

    # ------------------------------------------------------------------ #
    # Despacho concorrente                                                #
    # ------------------------------------------------------------------ #

    async def _dispatch_workers(
        self,
        product_ids: List[str],
        job,
        metrics: MetricsCollector,
    ) -> None:
        """
        Cria uma task por produto e aguarda todas com gather(return_exceptions=True).
        Cada task cria sua própria sessão de banco para evitar race conditions (R4.4).
        """
        tasks = [
            asyncio.create_task(
                self._process_one(pid, job, metrics)
            )
            for pid in product_ids
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

    # ------------------------------------------------------------------ #
    # Processamento individual                                            #
    # ------------------------------------------------------------------ #

    async def _process_one(
        self,
        product_id: str,
        job,
        metrics: MetricsCollector,
    ) -> None:
        """Pipeline completo para um único produto. Cria própria sessão de banco."""
        await self._limiter.acquire()
        try:
            await asyncio.sleep(self._api_rate_limit_delay)
            metrics.requests += 1

            # 1. Tenta cache (se disponível) → fallback para API
            payload = await self._cache_get_payload(product_id)
            if payload is None:
                payload = await self._api.fetch_product(
                    product_id,
                    rate_limiter=self._limiter,
                    max_retries=self._max_retries,
                    backoff_base=self._backoff_base,
                    max_wait=self._max_wait,
                )
                if payload is None:
                    metrics.errors += 1
                    async with self._session_factory() as session:
                        job_repo = CollectionJobRepository(session)
                        await job_repo.write_log(
                            job_id=job.id,
                            entity_type="product",
                            entity_id=product_id,
                            status="error",
                            error_message="API retornou None (produto removido ou indisponível)",
                        )
                    return
                await self._cache_set_payload(product_id, payload)

            # 2. Computa hash do estado dinâmico atual
            current_hash = self._compute_state_hash(payload)

            # 3. Compara com hash armazenado (cache ou None → sempre ingere)
            stored_hash = await self._cache_get_hash(product_id)

            if stored_hash is not None and stored_hash == current_hash:
                # Sem mudança — skip
                metrics.skipped += 1
                logger.debug(
                    "Produto sem mudança",
                    job_id=job.id,
                    product_id=product_id,
                )
                async with self._session_factory() as session:
                    await self._update_last_checked_at(product_id, session)
                    job_repo = CollectionJobRepository(session)
                    await job_repo.write_log(
                        job_id=job.id,
                        entity_type="product",
                        entity_id=product_id,
                        status="skipped",
                        events_generated=0,
                    )
                return

            # 4. Mudança detectada → ingere eventos
            async with self._session_factory() as session:
                ingestion = IngestionService(session)
                events_generated = await ingestion.ingest_product_payload(payload)

                if events_generated == 0:
                    # IngestionService decidiu não criar eventos (deduplicação interna)
                    metrics.skipped += 1
                    await self._update_last_checked_at(product_id, session)
                    job_repo = CollectionJobRepository(session)
                    await job_repo.write_log(
                        job_id=job.id,
                        entity_type="product",
                        entity_id=product_id,
                        status="skipped",
                        events_generated=0,
                    )
                    return

                # 5. Atualiza hash APÓS ingestão bem-sucedida
                await self._cache_set_hash(product_id, current_hash)

                metrics.products += 1
                metrics.changes += 1
                metrics.events_generated += events_generated

                logger.info(
                    "Mudança detectada e ingerida",
                    job_id=job.id,
                    product_id=product_id,
                    events_generated=events_generated,
                )
                await self._update_last_checked_at(product_id, session)
                job_repo = CollectionJobRepository(session)
                await job_repo.write_log(
                    job_id=job.id,
                    entity_type="product",
                    entity_id=product_id,
                    status="ok",
                    events_generated=events_generated,
                )

        except httpx.HTTPStatusError as exc:
            metrics.errors += 1
            logger.error(
                "Erro HTTP ao processar produto",
                job_id=job.id,
                product_id=product_id,
                http_status=exc.response.status_code,
            )
            async with self._session_factory() as session:
                job_repo = CollectionJobRepository(session)
                await job_repo.write_log(
                    job_id=job.id,
                    entity_type="product",
                    entity_id=product_id,
                    status="error",
                    error_message=f"HTTP {exc.response.status_code}: {exc}",
                )
        except Exception as exc:
            metrics.errors += 1
            logger.error(
                "Erro inesperado ao processar produto",
                job_id=job.id,
                product_id=product_id,
                error=str(exc),
            )
            async with self._session_factory() as session:
                job_repo = CollectionJobRepository(session)
                await job_repo.write_log(
                    job_id=job.id,
                    entity_type="product",
                    entity_id=product_id,
                    status="error",
                    error_message=str(exc),
                )
        finally:
            self._limiter.release()

    # ------------------------------------------------------------------ #
    # Helpers                                                             #
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # Cache helpers (None-safe)                                           #
    # ------------------------------------------------------------------ #

    async def _cache_get_payload(self, product_id: str) -> Optional[dict]:
        """Retorna payload do cache ou None se cache indisponível/miss."""
        if self._cache is None:
            return None
        try:
            return await self._cache.get_payload(product_id)
        except Exception:
            return None

    async def _cache_set_payload(self, product_id: str, payload: dict) -> None:
        """Persiste payload no cache, ignorando erros silenciosamente."""
        if self._cache is None:
            return
        try:
            await self._cache.set_payload(product_id, payload)
        except Exception:
            pass

    async def _cache_get_hash(self, product_id: str) -> Optional[str]:
        """Retorna hash do cache ou None se cache indisponível/miss."""
        if self._cache is None:
            return None
        try:
            return await self._cache.get_hash(product_id)
        except Exception:
            return None

    async def _cache_set_hash(self, product_id: str, state_hash: str) -> None:
        """Persiste hash no cache, ignorando erros silenciosamente."""
        if self._cache is None:
            return
        try:
            await self._cache.set_hash(product_id, state_hash)
        except Exception:
            pass

    async def _update_last_checked_at(
        self, product_id: str, session: AsyncSession
    ) -> None:
        """Atualiza last_checked_at para registro de controle incremental."""
        await session.execute(
            text("UPDATE products SET last_checked_at = :now WHERE id = :pid"),
            {"now": datetime.now(timezone.utc), "pid": product_id},
        )

    @staticmethod
    def _compute_state_hash(payload: dict) -> str:
        """
        SHA-256 dos campos dinâmicos que disparam criação de eventos.

        Campos: price, original_price, available_quantity, sold_quantity,
                status, title, listing_type_id

        json.dumps com sort_keys=True garante serialização determinística.
        """
        data = {
            "price": payload.get("price"),
            "original_price": payload.get("original_price"),
            "available_quantity": payload.get("available_quantity"),
            "sold_quantity": payload.get("sold_quantity"),
            "status": payload.get("status"),
            "title": payload.get("title"),
            "listing_type_id": payload.get("listing_type_id"),
        }
        serialised = json.dumps(data, sort_keys=True, default=str)
        return hashlib.sha256(serialised.encode()).hexdigest()
