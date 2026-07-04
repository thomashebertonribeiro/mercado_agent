"""api/routers/metrics.py — Endpoints de observability da plataforma."""

from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from database.connection import get_db_session
from models.product import Product
from models.operational import CollectionJob
from models.signal import Signal
from models.recommendation import Recommendation
from models.analysis_report import AnalysisReport
from observability.metrics import platform_metrics

router = APIRouter(tags=["Observability"])

_DASHBOARD_HTML = None


def _get_dashboard() -> str:
    global _DASHBOARD_HTML
    if _DASHBOARD_HTML is None:
        path = Path(__file__).resolve().parent.parent / "templates" / "dashboard.html"
        _DASHBOARD_HTML = path.read_text(encoding="utf-8")
    return _DASHBOARD_HTML


@router.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
async def dashboard() -> HTMLResponse:
    """Página HTML do dashboard de observability."""
    return HTMLResponse(_get_dashboard())


@router.get("/platform/metrics")
async def get_platform_metrics(
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Retorna métricas globais da plataforma (contadores, histogramas, gauges)."""
    snapshot = platform_metrics.snapshot()

    # Tenta enriquecer com dados do banco; falha silenciosamente se DB offline
    try:
        product_count = await db.scalar(select(func.count(Product.id)))
        collection_count = await db.scalar(select(func.count(CollectionJob.id)))
        signal_count = await db.scalar(select(func.count(Signal.id)))
        recommendation_count = await db.scalar(select(func.count(Recommendation.id)))
        report_count = await db.scalar(select(func.count(AnalysisReport.id)))

        snapshot["counters"]["products_in_db"] = product_count or 0
        snapshot["counters"]["collections_in_db"] = collection_count or 0
        snapshot["counters"]["signals_in_db"] = signal_count or 0
        snapshot["counters"]["recommendations_in_db"] = recommendation_count or 0
        snapshot["counters"]["reports_in_db"] = report_count or 0

        cat_counts_q = (
            select(Product.category_id, func.count(Product.id))
            .where(Product.category_id.isnot(None))
            .group_by(Product.category_id)
        )
        result = await db.execute(cat_counts_q)
        for cat_id, count in result.all():
            platform_metrics.set_category_product_count(cat_id, count)
            snapshot["categories"]["product_count"][cat_id] = count
    except Exception:
        snapshot["db_status"] = "unavailable"

    return snapshot


@router.post("/platform/metrics/reset")
async def reset_platform_metrics() -> dict:
    """Zera todas as métricas em memória."""
    platform_metrics.reset_all()
    return {"status": "ok", "message": "Métricas reiniciadas."}
