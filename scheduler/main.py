"""
scheduler/main.py

Scheduler da Plataforma de Inteligencia de Mercado.

Jobs registrados:
  1. Sync Seller Items    → sincroniza anuncios do vendedor (1h)
  2. Sync Competitors     → monitora concorrentes (6h)
  3. Sync Categories      → atualiza catalogo de categorias (24h)
  4. Sync Trends          → coleta tendencias de mercado (24h)
  5. Sync Metrics         → coleta metricas dos vendedores (24h)
  6. Intelligence Engine  → gera sinais matematicos (12h)
  7. Opportunity Score    → computa scores de oportunidade (6h)
  8. Recommendation Engine → gera rankings priorizados (12h)
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
# Jobs
# ─────────────────────────────────────────────────────────────────────────────


async def run_sync_seller_items() -> None:
    """Sincroniza anuncios do vendedor autenticado (a cada 1h)."""
    from jobs.sync_jobs import sync_seller_items
    await sync_seller_items()


async def run_sync_competitors() -> None:
    """Monitora concorrentes cadastrados (a cada 6h)."""
    from jobs.sync_jobs import sync_competitors
    await sync_competitors()


async def run_sync_categories() -> None:
    """Atualiza catalogo de categorias (1x por dia)."""
    from jobs.sync_jobs import sync_categories
    await sync_categories()


async def run_sync_trends() -> None:
    """Coleta tendencias de mercado (1x por dia)."""
    from jobs.sync_jobs import sync_trends
    await sync_trends()


async def run_sync_metrics() -> None:
    """Coleta metricas dos vendedores (1x por dia)."""
    from jobs.sync_jobs import sync_metrics
    await sync_metrics()


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
    """Inicia o scheduler com jobs periodicos."""
    logger.info("Iniciando scheduler da Plataforma de Inteligencia de Mercado")

    scheduler = AsyncIOScheduler()

    # Sync Seller Items (a cada 1h)
    scheduler.add_job(
        run_sync_seller_items,
        trigger="interval",
        hours=1,
        id="sync_seller_items",
        name="Sync Seller Items",
        replace_existing=True,
    )

    # Sync Competitors (a cada 6h)
    scheduler.add_job(
        run_sync_competitors,
        trigger="interval",
        hours=6,
        id="sync_competitors",
        name="Sync Competitors",
        replace_existing=True,
    )

    # Sync Categories (1x por dia)
    scheduler.add_job(
        run_sync_categories,
        trigger="interval",
        hours=24,
        id="sync_categories",
        name="Sync Categories",
        replace_existing=True,
    )

    # Sync Trends (1x por dia)
    scheduler.add_job(
        run_sync_trends,
        trigger="interval",
        hours=24,
        id="sync_trends",
        name="Sync Trends",
        replace_existing=True,
    )

    # Sync Metrics (1x por dia)
    scheduler.add_job(
        run_sync_metrics,
        trigger="interval",
        hours=24,
        id="sync_metrics",
        name="Sync Metrics",
        replace_existing=True,
    )

    # Intelligence Engine (a cada 12h)
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
        "Scheduler iniciado. Jobs: sync_seller(1h), sync_competitors(6h), "
        "sync_categories(24h), sync_trends(24h), sync_metrics(24h), "
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
