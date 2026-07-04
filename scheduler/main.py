"""
scheduler/main.py

Scheduler de coleta do Data Lake — integrado ao IntelligentCollector.

Jobs registrados:
  - run_full_collection()        → CollectionMode.FULL (padrão: 24h)
  - run_incremental_collection() → CollectionMode.INCREMENTAL (padrão: 6h)

Proteção contra duplicatas:
  Antes de iniciar qualquer job, verifica se já existe um job com
  status='running' para o mesmo job_type. Se sim, loga WARNING e retorna
  sem criar um novo job (R9.3).

Feature: intelligent-collector
"""

from __future__ import annotations

import asyncio

import redis.asyncio as aioredis
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from collector.api_client import MercadoLivreAPICollector
from collector.cache_manager import CacheManager
from collector.intelligent_collector import IntelligentCollector
from collector.modes import CollectionMode
from collector.rate_limiter import RateLimiter
from config.settings import settings
from database.connection import AsyncSessionLocal
from intelligence.engine import IntelligenceEngine
from repositories.collection_job import CollectionJobRepository
from repositories.product import ProductRepository
from utils.logger import logger


# ─────────────────────────────────────────────────────────────────────────────
# Factory
# ─────────────────────────────────────────────────────────────────────────────

def _build_collector() -> IntelligentCollector:
    """
    Constrói o IntelligentCollector com todas as suas dependências
    a partir das configurações do ambiente.

    Chamado uma vez na inicialização do scheduler e reutilizado entre jobs.
    Se o Redis estiver indisponível, o cache é desativado (graceful degradation).
    """
    api_client = MercadoLivreAPICollector()

    try:
        redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=False)
        cache_manager = CacheManager(
            redis_client=redis_client,
            hash_ttl_seconds=settings.CACHE_HASH_TTL_SECONDS,
            payload_ttl_seconds=settings.CACHE_PAYLOAD_TTL_SECONDS,
        )
    except Exception as exc:
        logger.warning("Redis indisponível — cache desativado", error=str(exc))
        cache_manager = None

    rate_limiter = RateLimiter(max_concurrency=settings.COLLECTOR_MAX_CONCURRENCY)

    return IntelligentCollector(
        api_client=api_client,
        cache_manager=cache_manager,
        rate_limiter=rate_limiter,
        session_factory=AsyncSessionLocal,
        max_concurrency=settings.COLLECTOR_MAX_CONCURRENCY,
        max_retries=settings.COLLECTOR_MAX_RETRIES,
        backoff_base=settings.COLLECTOR_BACKOFF_BASE,
        max_wait=settings.COLLECTOR_MAX_WAIT,
        api_rate_limit_delay=settings.API_RATE_LIMIT_DELAY,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Jobs agendados
# ─────────────────────────────────────────────────────────────────────────────

_collector: IntelligentCollector | None = None


async def run_full_collection() -> None:
    """
    Executa uma varredura completa de todos os produtos cadastrados.

    - Verifica se já existe job em execução (R9.3)
    - Loga resumo ao finalizar (R9.4)
    """
    global _collector
    if _collector is None:
        _collector = _build_collector()

    async with AsyncSessionLocal() as session:
        job_repo = CollectionJobRepository(session)
        running = await job_repo.find_running_job("full")
        if running:
            logger.warning(
                "Job full já em execução — ignorando novo disparo",
                existing_job_id=running.id,
            )
            return

    logger.info("Iniciando coleta full agendada")
    try:
        job = await _collector.collect(mode=CollectionMode.FULL)
        logger.info(
            "Coleta full concluída",
            job_id=job.id,
            status=job.status,
            processed=job.processed,
            events=job.events_generated,
            errors=job.errors,
            duration_seconds=job.duration_seconds,
        )
    except Exception as exc:
        logger.error("Coleta full falhou", error=str(exc))


async def run_incremental_collection() -> None:
    """
    Executa coleta incremental — apenas produtos não verificados recentemente.

    - Verifica se já existe job em execução (R9.3)
    - Usa COLLECTOR_INCREMENTAL_THRESHOLD_HOURS como limiar de staleness
    """
    global _collector
    if _collector is None:
        _collector = _build_collector()

    async with AsyncSessionLocal() as session:
        job_repo = CollectionJobRepository(session)
        running = await job_repo.find_running_job("incremental")
        if running:
            logger.warning(
                "Job incremental já em execução — ignorando novo disparo",
                existing_job_id=running.id,
            )
            return

    logger.info("Iniciando coleta incremental agendada")
    try:
        job = await _collector.collect(
            mode=CollectionMode.INCREMENTAL,
            incremental_threshold_hours=settings.COLLECTOR_INCREMENTAL_THRESHOLD_HOURS,
        )
        logger.info(
            "Coleta incremental concluída",
            job_id=job.id,
            status=job.status,
            processed=job.processed,
            events=job.events_generated,
            errors=job.errors,
            duration_seconds=job.duration_seconds,
        )
    except Exception as exc:
        logger.error("Coleta incremental falhou", error=str(exc))


async def run_intelligence_analysis() -> None:
    """
    Executa a Intelligence Engine em produtos que possuem historico recente
    mas ainda nao foram analisados (ou foram analisados ha mais de N horas).

    Limite: INTELLIGENCE_MAX_PRODUCTS_PER_CYCLE por execucao.
    """
    logger.info("Iniciando analise inteligente agendada")
    analyzed = 0

    async with AsyncSessionLocal() as session:
        product_repo = ProductRepository(session)
        products = await product_repo.list_all()

        from sqlalchemy import select, func
        from models.signal import Signal
        from models.events import PriceEvent

        for product in products[:settings.INTELLIGENCE_MAX_PRODUCTS_PER_CYCLE]:
            try:
                latest_signal_q = (
                    select(Signal.computed_at)
                    .where(Signal.product_id == product.id)
                    .order_by(Signal.computed_at.desc())
                    .limit(1)
                )
                latest_result = await session.execute(latest_signal_q)
                latest_signal = latest_result.scalar_one_or_none()

                latest_event_q = (
                    select(PriceEvent.occurred_at)
                    .where(PriceEvent.product_id == product.id)
                    .order_by(PriceEvent.occurred_at.desc())
                    .limit(1)
                )
                event_result = await session.execute(latest_event_q)
                latest_event = event_result.scalar_one_or_none()

                if latest_signal and latest_event and latest_signal >= latest_event:
                    continue

                engine = IntelligenceEngine(session)
                signals = await engine.analyze_product(
                    product_id=product.id,
                    category_id=product.category_id,
                    days=settings.INTELLIGENCE_ANALYSIS_DAYS,
                )
                if signals:
                    analyzed += 1
                    logger.debug(
                        "Produto analisado",
                        product_id=product.id,
                        signals=len(signals),
                    )
            except Exception as exc:
                logger.warning(
                    "Erro ao analisar produto",
                    product_id=product.id,
                    error=str(exc),
                )

    logger.info(
        "Analise inteligente concluida",
        products_analyzed=analyzed,
        max_products=settings.INTELLIGENCE_MAX_PRODUCTS_PER_CYCLE,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Entrypoint
# ─────────────────────────────────────────────────────────────────────────────

async def run_scheduler() -> None:
    """
    Inicia o scheduler APScheduler e registra os dois jobs periódicos.

    Intervalos controlados por settings:
      SCHEDULER_FULL_INTERVAL_HOURS       (padrão: 24h)
      SCHEDULER_INCREMENTAL_INTERVAL_HOURS (padrão: 6h)
    """
    logger.info(
        "Iniciando scheduler",
        full_interval_hours=settings.SCHEDULER_FULL_INTERVAL_HOURS,
        incremental_interval_hours=settings.SCHEDULER_INCREMENTAL_INTERVAL_HOURS,
    )

    scheduler = AsyncIOScheduler()

    scheduler.add_job(
        run_full_collection,
        trigger="interval",
        hours=settings.SCHEDULER_FULL_INTERVAL_HOURS,
        id="full_collection",
        name="Full Collection",
        replace_existing=True,
    )
    scheduler.add_job(
        run_incremental_collection,
        trigger="interval",
        hours=settings.SCHEDULER_INCREMENTAL_INTERVAL_HOURS,
        id="incremental_collection",
        name="Incremental Collection",
        replace_existing=True,
    )
    scheduler.add_job(
        run_intelligence_analysis,
        trigger="interval",
        hours=settings.INTELLIGENCE_ANALYSIS_INTERVAL_HOURS,
        id="intelligence_analysis",
        name="Intelligence Analysis",
        replace_existing=True,
    )

    scheduler.start()
    logger.info("Scheduler iniciado. Jobs registrados: full, incremental")

    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler encerrado")
        scheduler.shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(run_scheduler())
    except KeyboardInterrupt:
        logger.info("Scheduler interrompido pelo usuário")
