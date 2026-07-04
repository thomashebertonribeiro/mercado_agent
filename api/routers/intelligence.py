"""
api/routers/intelligence.py

REST API da Intelligence Engine.

Endpoints:
  POST   /intelligence/products/{product_id}/analyze  → analisa um produto
  GET    /intelligence/products/{product_id}/signals   → lista sinais de um produto
  POST   /intelligence/categories/{category_id}/analyze → analisa uma categoria
  GET    /intelligence/signals/types                   → lista tipos de sinal disponiveis
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from database.connection import get_db_session
from intelligence.engine import IntelligenceEngine
from intelligence.registry import list_signals, SIGNAL_REGISTRY
from models.signal import Signal
from repositories.product import ProductRepository
from repositories.signal import SignalRepository
from utils.logger import logger

router = APIRouter(prefix="/intelligence", tags=["Intelligence Engine"])


# ─────────────────────────────────────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────────────────────────────────────

class SignalResponse(BaseModel):
    id: int
    signal_type: str
    product_id: str | None
    category_id: str | None
    weight: float
    confidence: float
    value: float | None
    explanation: str
    extra_data: dict | None
    computed_at: str
    source: str

    @classmethod
    def from_orm(cls, signal: Signal) -> SignalResponse:
        return cls(
            id=signal.id,
            signal_type=signal.signal_type,
            product_id=signal.product_id,
            category_id=signal.category_id,
            weight=signal.weight,
            confidence=signal.confidence,
            value=float(signal.value) if signal.value is not None else None,
            explanation=signal.explanation,
            extra_data=signal.extra_data,
            computed_at=signal.computed_at.isoformat(),
            source=signal.source,
        )


class SignalTypeResponse(BaseModel):
    type: str
    label: str
    default_weight: float
    requires_product: bool
    requires_category: bool
    min_data_points: int


class AnalyzeResponse(BaseModel):
    product_id: str
    signals_generated: int
    signals: list[SignalResponse]


class AnalyzeCategoryResponse(BaseModel):
    category_id: str
    signals_generated: int
    signals: list[SignalResponse]


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/products/{product_id}/analyze",
    status_code=status.HTTP_201_CREATED,
    summary="Analisar um produto",
    response_model=AnalyzeResponse,
)
async def analyze_product(
    product_id: str,
    category_id: str | None = Query(None, description="ID da categoria (opcional)"),
    days: int = Query(30, ge=1, le=365, description="Janela de analise em dias"),
    db: AsyncSession = Depends(get_db_session),
) -> AnalyzeResponse:
    """Executa a Intelligence Engine em um produto e retorna os sinais gerados."""
    product_id = product_id.strip().upper()

    product_repo = ProductRepository(db)
    product = await product_repo.get_by_id(product_id)
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Produto {product_id} nao encontrado.",
        )

    engine = IntelligenceEngine(db)
    signals = await engine.analyze_product(
        product_id=product_id,
        category_id=category_id or product.category_id,
        days=days,
    )

    return AnalyzeResponse(
        product_id=product_id,
        signals_generated=len(signals),
        signals=[SignalResponse.from_orm(s) for s in signals],
    )


@router.get(
    "/products/{product_id}/signals",
    summary="Listar sinais de um produto",
    response_model=list[SignalResponse],
)
async def get_product_signals(
    product_id: str,
    signal_type: str | None = Query(None, description="Filtrar por tipo de sinal"),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db_session),
) -> list[SignalResponse]:
    """Retorna os sinais gerados para um produto."""
    product_id = product_id.strip().upper()

    signal_repo = SignalRepository(db)
    signals = await signal_repo.get_by_product(
        product_id=product_id,
        signal_type=signal_type,
        limit=limit,
    )

    return [SignalResponse.from_orm(s) for s in signals]


@router.post(
    "/categories/{category_id}/analyze",
    status_code=status.HTTP_201_CREATED,
    summary="Analisar uma categoria",
    response_model=AnalyzeCategoryResponse,
)
async def analyze_category(
    category_id: str,
    days: int = Query(30, ge=1, le=365, description="Janela de analise em dias"),
    db: AsyncSession = Depends(get_db_session),
) -> AnalyzeCategoryResponse:
    """Executa a Intelligence Engine em uma categoria e retorna os sinais agregados."""
    category_id = category_id.strip().upper()

    engine = IntelligenceEngine(db)
    signals = await engine.analyze_category(
        category_id=category_id,
        days=days,
    )

    return AnalyzeCategoryResponse(
        category_id=category_id,
        signals_generated=len(signals),
        signals=[SignalResponse.from_orm(s) for s in signals],
    )


@router.get(
    "/signals/types",
    summary="Listar tipos de sinal disponiveis",
    response_model=list[SignalTypeResponse],
)
async def list_signal_types() -> list[SignalTypeResponse]:
    """Retorna todos os tipos de sinal que a Intelligence Engine suporta."""
    definitions = list_signals()
    return [
        SignalTypeResponse(
            type=d.signal_type.value,
            label=d.label,
            default_weight=d.default_weight,
            requires_product=d.requires_product,
            requires_category=d.requires_category,
            min_data_points=d.min_data_points,
        )
        for d in definitions
    ]
