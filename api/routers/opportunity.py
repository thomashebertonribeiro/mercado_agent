"""
api/routers/opportunity.py

REST API do Opportunity Score.

Endpoints:
  POST   /opportunity/compute             → computa score a partir de dados fornecidos
  POST   /opportunity/compute/{product_id} → computa score carregando dados do banco
  GET    /opportunity/config              → retorna configuracao atual de pesos
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from database.connection import get_db_session
from intelligence.formulas import compute_linear_regression
from opportunity.config import OpportunityConfig
from opportunity.score import OpportunityScore, OpportunityScorer, FACTOR_LABELS
from repositories.event import EventRepository
from repositories.product import ProductRepository
from utils.logger import logger

router = APIRouter(prefix="/opportunity", tags=["Opportunity Score"])


# ─────────────────────────────────────────────────────────────────────────────
# Schemas
# ─────────────────────────────────────────────────────────────────────────────

class ComputeRequest(BaseModel):
    review_count: int = 0
    price: float = 0.0
    seller_count: int = 0
    price_slope: float = 0.0
    r_squared: float = 0.0
    mean_price: float = 1.0
    coefficient_of_variation: float = 0.0
    shipping_cost: float = 0.0
    weight_kg: float = 0.0
    volume_cm3: float = 0.0
    price_events_count: int = 0
    new_sellers_per_month: float = 0.0
    optimal_price: float = 100.0
    estimated_cost: float | None = None
    category_avg_price: float | None = None
    optimal_seller_count: int | None = None
    sold_quantity: int | None = None
    seasonality_index: float = 0.0


class FactorDetailResponse(BaseModel):
    name: str
    label: str
    weight: float
    raw_score: float
    contribution: float


class ComputeResponse(BaseModel):
    total_score: int
    raw_score: float
    factors: list[FactorDetailResponse]


class ComputeProductResponse(BaseModel):
    product_id: str
    total_score: int
    raw_score: float
    factors: list[FactorDetailResponse]


class ConfigResponse(BaseModel):
    weights: dict[str, float]
    total_weight: float


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.post(
    "/compute",
    summary="Computar Opportunity Score a partir de dados fornecidos",
    response_model=ComputeResponse,
)
async def compute_opportunity(
    data: ComputeRequest,
) -> ComputeResponse:
    """Computa o Opportunity Score com dados enviados diretamente no body.

    Util quando os dados ja estao disponiveis fora do banco.
    """
    scorer = OpportunityScorer()
    result = scorer.compute(**data.model_dump())
    return _to_compute_response(result)


@router.post(
    "/compute/{product_id}",
    summary="Computar Opportunity Score para um produto do banco",
    response_model=ComputeProductResponse,
)
async def compute_product_opportunity(
    product_id: str,
    optimal_price: float | None = Query(None, ge=1, description="Preco considerado otimo para o mercado"),
    db: AsyncSession = Depends(get_db_session),
) -> ComputeProductResponse:
    """Carrega dados historicos do produto do banco e computa o Opportunity Score.

    Inclui calculo automatico de:
      - price_slope e r_squared (regressao linear dos precos historicos)
      - coefficient_of_variation (CV dos precos)
      - mean_price (preco medio no periodo)
      - review_count (total de avaliacoes)
      - price_events_count (total de eventos de preco)
      - seller_count (numero de vendedores via dados do produto)
    """
    product_id = product_id.strip().upper()

    product_repo = ProductRepository(db)
    product = await product_repo.get_by_id(product_id)
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Produto {product_id} nao encontrado.",
        )

    event_repo = EventRepository(db)
    prices = await event_repo.get_price_history(product_id, limit=200)

    if not prices:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Produto {product_id} nao possui historico de precos.",
        )

    price_values = [float(p.price) for p in prices]
    price_dates = [p.occurred_at for p in prices]
    latest_price = price_values[-1]
    mean_price = sum(price_values) / len(price_values)

    # Regressao linear
    slope, r_sq = compute_linear_regression(price_dates, price_values)

    # Coeficiente de variacao
    cv = 0.0
    if mean_price > 0:
        variance = sum((p - mean_price) ** 2 for p in price_values) / len(price_values)
        std_dev = variance ** 0.5
        cv = std_dev / mean_price

    # Review count
    from models.events import ReviewEvent
    from sqlalchemy import select, func
    review_count_q = (
        select(func.count())
        .select_from(ReviewEvent)
        .where(ReviewEvent.product_id == product_id)
    )
    review_count_result = await db.execute(review_count_q)
    review_count = review_count_result.scalar() or 0

    # Quantos vendedores (monitored products pattern)
    seller_count = 1 if product.seller_id else 0
    if product.seller_id:
        from sqlalchemy import select as sel2
        from models.product import Product
        count_q = sel2(func.count()).select_from(Product).where(Product.seller_id == product.seller_id)
        count_result = await db.execute(count_q)
        seller_count = count_result.scalar() or 1

    scorer = OpportunityScorer()
    result = scorer.compute(
        review_count=review_count,
        price=latest_price,
        seller_count=seller_count,
        price_slope=slope,
        r_squared=r_sq,
        mean_price=mean_price,
        coefficient_of_variation=cv,
        price_events_count=len(price_values),
        optimal_price=optimal_price or latest_price * 1.1,
    )

    return ComputeProductResponse(
        product_id=product_id,
        total_score=result.total_score,
        raw_score=result.raw_score,
        factors=[
            FactorDetailResponse(
                name=f.name,
                label=f.label,
                weight=f.weight,
                raw_score=round(f.raw_score, 4),
                contribution=round(f.contribution, 4),
            )
            for f in result.factors
        ],
    )


@router.get(
    "/config",
    summary="Retornar configuracao atual dos pesos",
    response_model=ConfigResponse,
)
async def get_config() -> ConfigResponse:
    """Retorna os pesos padrao do Opportunity Score."""
    config = OpportunityConfig()
    weights = {
        "demand": config.demand_weight,
        "margin": config.margin_weight,
        "competition": config.competition_weight,
        "growth": config.growth_weight,
        "reviews": config.reviews_weight,
        "competitor_entry": config.competitor_entry_weight,
        "price": config.price_weight,
        "volatility": config.volatility_weight,
        "seasonality": config.seasonality_weight,
        "shipping": config.shipping_weight,
        "weight": config.weight_weight,
        "volume": config.volume_weight,
    }
    return ConfigResponse(weights=weights, total_weight=config.total_weight())


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _to_compute_response(result: OpportunityScore) -> ComputeResponse:
    return ComputeResponse(
        total_score=result.total_score,
        raw_score=result.raw_score,
        factors=[
            FactorDetailResponse(
                name=f.name,
                label=f.label,
                weight=f.weight,
                raw_score=round(f.raw_score, 4),
                contribution=round(f.contribution, 4),
            )
            for f in result.factors
        ],
    )
