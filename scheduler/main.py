"""
scheduler/main.py

Scheduler da Plataforma de Inteligência de Mercado.

Jobs registrados:
  1. Scanner Discovery   → percorre categorias e descobre produtos (dinâmico)
  2. Collection Engine   → coleta dados dos produtos descobertos
  3. Intelligence Engine → gera sinais matemáticos
  4. Opportunity Score   → computa scores de oportunidade
  5. Recommendation Engine → gera rankings priorizados
  6. AI Analyst          → gera relatórios executivos

Frequência dinâmica por categoria:
  - HIGH:   a cada 6h   (≥10k anúncios)
  - MEDIUM: a cada 24h  (≥1k anúncios)
  - LOW:    a cada 72h  (>0 anúncios)
  - ONCE:   não repete  (categoria vazia)
"""

from __future__ import annotations

import asyncio

from apscheduler.schedulers.asyncio import AsyncIOScheduler

import datetime as _dt

from config.settings import settings
from database.connection import AsyncSessionLocal
from providers.mercadolivre import MercadoLivreProvider
from scanner.scanner import MarketScanner
from utils.logger import logger


# ─────────────────────────────────────────────────────────────────────────────
# Globals
# ─────────────────────────────────────────────────────────────────────────────

_scanner: MarketScanner | None = None
_collector = None  # será o IntelligentCollector (já existe)


def _get_scanner() -> MarketScanner:
    global _scanner
    if _scanner is None:
        _scanner = MarketScanner(
            provider=MercadoLivreProvider(),
            session_factory=AsyncSessionLocal,
            max_concurrency=settings.COLLECTOR_MAX_CONCURRENCY,
        )
    return _scanner


# ─────────────────────────────────────────────────────────────────────────────
# Jobs
# ─────────────────────────────────────────────────────────────────────────────


async def run_scanner_discovery() -> None:
    """Varre categorias e descobre novos produtos automaticamente."""
    scanner = _get_scanner()
    try:
        if not scanner.get_category_tree():
            await scanner.initialize()
        result = await scanner.scan_all_categories()
        logger.info(
            "Scanner discovery concluído",
            categories=result.categories_scanned,
            new_products=result.new_products,
            errors=result.errors,
            duration=round(result.duration_seconds, 1),
        )
    except Exception as exc:
        logger.error("Scanner discovery falhou", error=str(exc))


async def run_collection() -> None:
    """Coleta dados dos produtos descobertos.

    Usa o IntelligentCollector existente em modo incremental.
    Se não houver collector, apenas loga aviso.
    """
    global _collector
    if _collector is None:
        try:
            from collector.api_client import MercadoLivreAPICollector
            from collector.cache_manager import CacheManager
            from collector.intelligent_collector import IntelligentCollector
            from collector.modes import CollectionMode
            from collector.rate_limiter import RateLimiter

            import redis.asyncio as aioredis

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
            _collector = IntelligentCollector(
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
        except Exception as exc:
            logger.warning("Collector não disponível", error=str(exc))
            return

    try:
        from collector.modes import CollectionMode
        job = await _collector.collect(mode=CollectionMode.INCREMENTAL)
        logger.info(
            "Coleta concluída",
            job_id=job.id,
            status=job.status,
            processed=job.processed,
            events=job.events_generated,
            errors=job.errors,
        )
    except Exception as exc:
        logger.error("Coleta falhou", error=str(exc))


async def run_intelligence_analysis() -> None:
    """Gera sinais matemáticos via Intelligence Engine."""
    from intelligence.engine import IntelligenceEngine
    from repositories.product import ProductRepository
    from sqlalchemy import select
    from models.signal import Signal
    from models.events import PriceEvent

    logger.info("Iniciando análise inteligente")
    analyzed = 0

    async with AsyncSessionLocal() as session:
        product_repo = ProductRepository(session)
        products = await product_repo.list_all()

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
            except Exception as exc:
                logger.warning(
                    "Erro ao analisar produto",
                    product_id=product.id,
                    error=str(exc),
                )

    logger.info("Análise inteligente concluída", products_analyzed=analyzed)


async def run_opportunity_scores() -> None:
    """Computa Opportunity Score para produtos com sinais recentes."""
    from models.signal import Signal
    from models.product import Product
    from models.events import PriceEvent
    from models.opportunity_score import OpportunityScoreResult
    from opportunity.score import OpportunityScorer
    from intelligence.formulas import compute_linear_regression
    from sqlalchemy import select, func, desc

    logger.info("Iniciando computação de Opportunity Scores")
    computed = 0

    async with AsyncSessionLocal() as session:
        recent_signals_q = (
            select(Signal.product_id)
            .distinct()
            .order_by(Signal.product_id)
            .limit(50)
        )
        result = await session.execute(recent_signals_q)
        product_ids = [row[0] for row in result.all() if row[0]]

        for pid in product_ids:
            try:
                product = await session.get(Product, pid)
                if not product:
                    continue

                event_repo = __import__("repositories.event", fromlist=["EventRepository"]).EventRepository(session)
                prices = await event_repo.get_price_history(pid, limit=200)
                if not prices:
                    continue

                price_values = [float(p.price) for p in prices]
                price_dates = [p.occurred_at for p in prices]
                latest = price_values[-1]
                mean_price = sum(price_values) / len(price_values)

                slope, r_sq = compute_linear_regression(price_dates, price_values)

                cv = 0.0
                if mean_price > 0:
                    variance = sum((p - mean_price) ** 2 for p in price_values) / len(price_values)
                    cv = (variance ** 0.5) / mean_price

                # review count
                from models.events import ReviewEvent
                rc_q = select(func.count()).select_from(ReviewEvent).where(ReviewEvent.product_id == pid)
                rc_result = await session.execute(rc_q)
                review_count = rc_result.scalar() or 0

                scorer = OpportunityScorer()
                opp_result = scorer.compute(
                    review_count=review_count,
                    price=latest,
                    seller_count=1,
                    price_slope=slope,
                    r_squared=r_sq,
                    mean_price=mean_price,
                    coefficient_of_variation=cv,
                    price_events_count=len(price_values),
                    optimal_price=latest * 1.1,
                )

                import hashlib
                raw_hash = f"opportunity:{pid}:{_dt.datetime.now(_dt.timezone.utc).isoformat()}"
                h = hashlib.sha256(raw_hash.encode()).hexdigest()

                session.add(OpportunityScoreResult(
                    product_id=pid,
                    category_id=product.category_id,
                    total_score=opp_result.total_score,
                    raw_score=opp_result.raw_score,
                    factors=[f.__dict__ for f in opp_result.factors],
                    source="scheduler",
                    hash=h,
                    computed_at=_dt.datetime.now(_dt.timezone.utc),
                ))
                computed += 1
            except Exception as exc:
                logger.warning("Erro ao computar score", product_id=pid, error=str(exc))

        await session.commit()

    logger.info("Opportunity Scores computados", total=computed)


async def run_recommendations() -> None:
    """Gera recomendações priorizadas."""
    from recommendation.engine import RecommendationEngine

    logger.info("Iniciando geração de recomendações")
    try:
        async with AsyncSessionLocal() as session:
            engine = RecommendationEngine(session)
            recs = await engine.generate_all()
            logger.info("Recomendações geradas", total=len(recs))
    except Exception as exc:
        logger.error("Geração de recomendações falhou", error=str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# Entrypoint
# ─────────────────────────────────────────────────────────────────────────────


async def run_scheduler() -> None:
    """Inicia o scheduler com jobs periódicos."""
    logger.info("Iniciando scheduler da Plataforma de Inteligência de Mercado")

    scheduler = AsyncIOScheduler()

    # Scanner: descobre produtos (a cada 6h)
    scheduler.add_job(
        run_scanner_discovery,
        trigger="interval",
        hours=6,
        id="scanner_discovery",
        name="Scanner Discovery",
        replace_existing=True,
    )

    # Coleta: dados dos produtos (a cada 1h)
    scheduler.add_job(
        run_collection,
        trigger="interval",
        hours=1,
        id="collection",
        name="Collection Engine",
        replace_existing=True,
    )

    # Intelligence Engine (a cada 6h)
    scheduler.add_job(
        run_intelligence_analysis,
        trigger="interval",
        hours=settings.INTELLIGENCE_ANALYSIS_INTERVAL_HOURS,
        id="intelligence_analysis",
        name="Intelligence Analysis",
        replace_existing=True,
    )

    # Opportunity Score (a cada 6h)
    scheduler.add_job(
        run_opportunity_scores,
        trigger="interval",
        hours=6,
        id="opportunity_scores",
        name="Opportunity Scores",
        replace_existing=True,
    )

    # Recommendation Engine (a cada 12h)
    scheduler.add_job(
        run_recommendations,
        trigger="interval",
        hours=12,
        id="recommendations",
        name="Recommendations",
        replace_existing=True,
    )

    scheduler.start()
    logger.info(
        "Scheduler iniciado. Jobs: scanner(6h), collection(1h), "
        "intelligence(12h), scores(6h), recommendations(12h)",
    )

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
