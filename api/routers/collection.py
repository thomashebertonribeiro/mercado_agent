"""api/routers/collection.py — Endpoints para disparar coleta de dados."""

from fastapi import APIRouter, HTTPException, Query, status

from collector.intelligent_collector import IntelligentCollector
from collector.modes import CollectionMode
from collector.api_client import MercadoLivreAPICollector
from collector.rate_limiter import RateLimiter
from database.connection import AsyncSessionLocal
from config.settings import settings
from utils.logger import logger
from observability.metrics import platform_metrics

router = APIRouter(prefix="/collection", tags=["Collection"])

_collector: IntelligentCollector | None = None


def _get_collector() -> IntelligentCollector:
    global _collector
    if _collector is None:
        logger.info("Inicializando IntelligentCollector...")
        api_client = MercadoLivreAPICollector()
        rate_limiter = RateLimiter(max_concurrency=settings.COLLECTOR_MAX_CONCURRENCY)
        _collector = IntelligentCollector(
            api_client=api_client,
            cache_manager=None,
            rate_limiter=rate_limiter,
            session_factory=AsyncSessionLocal,
            max_concurrency=settings.COLLECTOR_MAX_CONCURRENCY,
            max_retries=settings.COLLECTOR_MAX_RETRIES,
            backoff_base=settings.COLLECTOR_BACKOFF_BASE,
            max_wait=settings.COLLECTOR_MAX_WAIT,
            api_rate_limit_delay=settings.API_RATE_LIMIT_DELAY,
        )
    return _collector


@router.post("/start", status_code=status.HTTP_202_ACCEPTED)
async def start_collection(
    mode: str = Query("incremental", description="Modo: full | incremental | by_product | by_category"),
    product_ids: str | None = Query(None, description="IDs separados por vírgula (modo by_product)"),
    category_ids: str | None = Query(None, description="IDs separados por vírgula (modo by_category)"),
) -> dict:
    """Dispara coleta de dados com o IntelligentCollector."""
    collector = _get_collector()

    try:
        mode_enum = CollectionMode(mode)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Modo inválido: {mode}. Opções: full, incremental, by_product, by_category")

    parsed_product_ids = [p.strip() for p in product_ids.split(",")] if product_ids else None
    parsed_category_ids = [c.strip() for c in category_ids.split(",")] if category_ids else None

    logger.info("Coleta disparada via API", mode=mode, products=parsed_product_ids, categories=parsed_category_ids)
    platform_metrics.collections_total.inc()

    job = await collector.collect(
        mode=mode_enum,
        product_ids=parsed_product_ids,
        category_ids=parsed_category_ids,
    )

    return {
        "status": "accepted",
        "job_id": job.id,
        "mode": mode,
        "total_items": job.total_items,
        "message": f"Coleta {mode} iniciada — job #{job.id}",
    }


@router.get("/status")
async def collection_status() -> dict:
    """Status do IntelligentCollector (sem consulta ao banco)."""
    return {
        "collector_initialized": _collector is not None,
        "max_concurrency": settings.COLLECTOR_MAX_CONCURRENCY,
        "max_retries": settings.COLLECTOR_MAX_RETRIES,
    }
