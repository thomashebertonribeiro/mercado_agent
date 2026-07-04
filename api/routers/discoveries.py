"""
api/routers/discoveries.py

Endpoints de descoberta, oportunidades, sinais, recomendações e relatórios.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from database.connection import get_db_session
from models.category import Category
from models.recommendation import Recommendation
from models.signal import Signal
from models.product import Product
from models.opportunity_score import OpportunityScoreResult
from models.analysis_report import AnalysisReport
from repositories.product import ProductRepository
from repositories.signal import SignalRepository
from utils.logger import logger

router = APIRouter(tags=["Discovery & Intelligence"])


# ─────────────────────────────────────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────────────────────────────────────


class CategoryResponse(BaseModel):
    id: str
    name: str
    product_count: int | None = None


class SignalResponse(BaseModel):
    id: int
    signal_type: str
    product_id: str | None
    category_id: str | None
    confidence: float
    value: float | None
    explanation: str
    computed_at: str


class RecommendationResponse(BaseModel):
    id: int
    recommendation_type: str
    product_id: str | None
    category_id: str | None
    priority_score: int
    title: str
    reason: str
    created_at: str


class OpportunityScoreResponse(BaseModel):
    id: int
    product_id: str
    total_score: int
    raw_score: float
    factors: dict
    computed_at: str


class ReportResponse(BaseModel):
    id: int
    product_id: str
    report: dict
    generated_at: str


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/categories", response_model=list[CategoryResponse])
async def list_categories(
    db: AsyncSession = Depends(get_db_session),
) -> list[CategoryResponse]:
    """Lista todas as categorias conhecidas."""
    result = await db.execute(select(Category).order_by(Category.name))
    categories = result.scalars().all()

    from sqlalchemy import func as sf
    from sqlalchemy import select as sel

    response = []
    for cat in categories:
        count_q = sel(sf.count()).select_from(Product).where(Product.category_id == cat.id)
        count_result = await db.execute(count_q)
        count = count_result.scalar() or 0
        response.append(CategoryResponse(id=cat.id, name=cat.name, product_count=count))
    return response


@router.get("/signals", response_model=list[SignalResponse])
async def list_signals(
    signal_type: str | None = Query(None, description="Filtrar por tipo de sinal"),
    product_id: str | None = Query(None, description="Filtrar por produto"),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db_session),
) -> list[SignalResponse]:
    """Lista sinais gerados pela Intelligence Engine."""
    q = select(Signal).order_by(desc(Signal.computed_at)).limit(limit)
    if signal_type:
        q = q.where(Signal.signal_type == signal_type)
    if product_id:
        q = q.where(Signal.product_id == product_id)

    result = await db.execute(q)
    signals = result.scalars().all()

    return [
        SignalResponse(
            id=s.id,
            signal_type=s.signal_type,
            product_id=s.product_id,
            category_id=s.category_id,
            confidence=s.confidence,
            value=float(s.value) if s.value is not None else None,
            explanation=s.explanation[:300],
            computed_at=s.computed_at.isoformat(),
        )
        for s in signals
    ]


@router.get("/opportunities", response_model=list[OpportunityScoreResponse])
async def list_opportunities(
    limit: int = Query(50, ge=1, le=200),
    product_id: str | None = Query(None),
    db: AsyncSession = Depends(get_db_session),
) -> list[OpportunityScoreResponse]:
    """Lista resultados do Opportunity Score."""
    q = (
        select(OpportunityScoreResult)
        .order_by(desc(OpportunityScoreResult.total_score))
        .limit(limit)
    )
    if product_id:
        q = q.where(OpportunityScoreResult.product_id == product_id)
        q = q.order_by(desc(OpportunityScoreResult.computed_at))

    result = await db.execute(q)
    scores = result.scalars().all()

    return [
        OpportunityScoreResponse(
            id=s.id,
            product_id=s.product_id,
            total_score=s.total_score,
            raw_score=s.raw_score,
            factors=s.factors,
            computed_at=s.computed_at.isoformat(),
        )
        for s in scores
    ]


@router.get("/recommendations", response_model=list[RecommendationResponse])
async def list_recommendations(
    recommendation_type: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db_session),
) -> list[RecommendationResponse]:
    """Lista recomendações geradas pelo Recommendation Engine."""
    q = (
        select(Recommendation)
        .order_by(desc(Recommendation.priority_score))
        .limit(limit)
    )
    if recommendation_type:
        q = q.where(Recommendation.recommendation_type == recommendation_type)

    result = await db.execute(q)
    recs = result.scalars().all()

    return [
        RecommendationResponse(
            id=r.id,
            recommendation_type=r.recommendation_type,
            product_id=r.product_id,
            category_id=r.category_id,
            priority_score=r.priority_score,
            title=r.title,
            reason=r.reason[:300],
            created_at=r.created_at.isoformat(),
        )
        for r in recs
    ]


@router.post("/recommendations/generate", response_model=dict)
async def generate_recommendations(
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Gera recomendações on-demand via Recommendation Engine."""
    from recommendation.engine import RecommendationEngine

    engine = RecommendationEngine(db)
    recs = await engine.generate_all(limit=limit)
    return {
        "status": "ok",
        "total": len(recs),
        "message": f"{len(recs)} recomendações geradas com sucesso.",
    }


@router.get("/reports", response_model=list[ReportResponse])
async def list_reports(
    product_id: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db_session),
) -> list[ReportResponse]:
    """Lista relatórios do AI Analyst."""
    q = (
        select(AnalysisReport)
        .order_by(desc(AnalysisReport.generated_at))
        .limit(limit)
    )
    if product_id:
        q = q.where(AnalysisReport.product_id == product_id)

    result = await db.execute(q)
    reports = result.scalars().all()

    return [
        ReportResponse(
            id=r.id,
            product_id=r.product_id,
            report=r.report,
            generated_at=r.generated_at.isoformat(),
        )
        for r in reports
    ]
