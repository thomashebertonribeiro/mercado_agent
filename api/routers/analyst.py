"""
api/routers/analyst.py

REST API do AI Analyst.

Endpoints:
  POST /analyst/{product_id}     → gera relatório de análise completo
  POST /analyst/custom           → gera relatório a partir de dados fornecidos
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ai_analyst import analyze_product as run_analysis
from database.connection import get_db_session
from intelligence.formulas import compute_linear_regression
from intelligence.registry import get_definition, SignalType
from repositories.event import EventRepository
from repositories.product import ProductRepository
from repositories.signal import SignalRepository
from utils.logger import logger

router = APIRouter(prefix="/analyst", tags=["AI Analyst"])


# ─────────────────────────────────────────────────────────────────────────────
# Schemas de resposta
# ─────────────────────────────────────────────────────────────────────────────


class RiskResponse(BaseModel):
    risk: str
    severity: str
    justification: str
    source_signals: list[str]


class OpportunityResponse(BaseModel):
    opportunity: str
    potential: str
    justification: str
    source_signals: list[str]


class ActionResponse(BaseModel):
    action: str
    priority: str
    reasoning: str


class AnalystReportResponse(BaseModel):
    product_id: str
    product_title: str
    category_name: str | None
    generated_at: str
    executive_summary: str
    risks: list[RiskResponse]
    opportunities: list[OpportunityResponse]
    recommendation_reasons: list[str]
    possible_actions: list[ActionResponse]
    confidence: dict


class CustomAnalysisInput(BaseModel):
    product_id: str
    product_title: str = ""
    category_id: str | None = None
    category_name: str | None = None
    seller_id: int = 0
    product_condition: str | None = None
    listing_type: str | None = None
    initial_price: float | None = None
    signals: list[dict] = Field(default_factory=list)
    opportunity_score: int | None = None
    opportunity_raw_score: float | None = None
    factors: list[dict] = Field(default_factory=list)
    price_history: list[list] = Field(default_factory=list, description="[[iso_timestamp, price], ...]")
    price_slope: float = 0.0
    price_r_squared: float = 0.0
    mean_price: float = 0.0
    coefficient_of_variation: float = 0.0
    review_count: int = 0
    price_events_count: int = 0
    seller_count: int = 0
    days_of_data: int = 0


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────


@router.post(
    "/{product_id}",
    summary="Gerar relatório de análise para um produto",
    response_model=AnalystReportResponse,
)
async def analyze_product_endpoint(
    product_id: str,
    db: AsyncSession = Depends(get_db_session),
) -> AnalystReportResponse:
    """Carrega todos os dados do produto do banco e gera um relatório completo.

    Dados carregados:
      - Produto (metadados)
      - Histórico de preços
      - Sinais gerados pela Intelligence Engine
      - Opportunity Score (recalculado com dados atuais)
    """
    product_id = product_id.strip().upper()

    # 1. Carrega produto
    product_repo = ProductRepository(db)
    product = await product_repo.get_by_id(product_id)
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Produto {product_id} não encontrado.",
        )

    # 2. Carrega histórico de preços
    event_repo = EventRepository(db)
    prices = await event_repo.get_price_history(product_id, limit=200)

    if not prices:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Produto {product_id} não possui histórico de preços.",
        )

    price_tuples = [(p.occurred_at, float(p.price)) for p in prices]
    price_values = [float(p.price) for p in prices]
    price_dates = [p.occurred_at for p in prices]
    latest_price = price_values[-1]
    mean_price = sum(price_values) / len(price_values)

    # 3. Regressão linear
    slope, r_sq = compute_linear_regression(price_dates, price_values)

    # 4. Coeficiente de variação
    cv = 0.0
    if mean_price > 0:
        variance = sum((p - mean_price) ** 2 for p in price_values) / len(price_values)
        std_dev = variance ** 0.5
        cv = std_dev / mean_price

    # 5. Review count
    from models.events import ReviewEvent
    from sqlalchemy import select, func

    review_count_q = (
        select(func.count())
        .select_from(ReviewEvent)
        .where(ReviewEvent.product_id == product_id)
    )
    rc_result = await db.execute(review_count_q)
    review_count = rc_result.scalar() or 0

    # 6. Seller count
    seller_count = 1
    if product.seller_id:
        from models.product import Product as ProductModel
        count_q = (
            select(func.count())
            .select_from(ProductModel)
            .where(ProductModel.seller_id == product.seller_id)
        )
        count_result = await db.execute(count_q)
        seller_count = count_result.scalar() or 1

    # 7. Dias de dados
    days_of_data = (price_dates[-1] - price_dates[0]).days if len(price_dates) >= 2 else 1

    # 8. Sinais
    signal_repo = SignalRepository(db)
    signal_models = await signal_repo.get_by_product(product_id, limit=100)
    signals_raw = [
        {
            "signal_type": s.signal_type,
            "confidence": s.confidence,
            "weight": s.weight,
            "value": float(s.value) if s.value is not None else None,
            "explanation": s.explanation,
            "extra_data": s.extra_data,
            "computed_at": s.computed_at,
        }
        for s in signal_models
    ]

    # 9. Opportunity Score (recalcula com dados atuais)
    from opportunity.score import OpportunityScorer
    scorer = OpportunityScorer()
    opp_result = scorer.compute(
        review_count=review_count,
        price=latest_price,
        seller_count=seller_count,
        price_slope=slope,
        r_squared=r_sq,
        mean_price=mean_price,
        coefficient_of_variation=cv,
        price_events_count=len(price_values),
        optimal_price=latest_price * 1.1,
    )

    factors_raw = [
        {
            "name": f.name,
            "label": f.label,
            "weight": f.weight,
            "raw_score": f.raw_score,
            "contribution": f.contribution,
        }
        for f in opp_result.factors
    ]

    # 10. Nome da categoria
    category_name = None
    if product.category_id:
        from models.category import Category
        cat = await db.get(Category, product.category_id)
        if cat:
            category_name = cat.name

    # 11. Executa o AI Analyst
    report = await run_analysis(
        product_id=product.id,
        product_title=product.title,
        category_id=product.category_id,
        category_name=category_name,
        seller_id=product.seller_id,
        product_condition=product.condition,
        listing_type=product.listing_type_id,
        initial_price=float(product.initial_price) if product.initial_price else None,
        signals_raw=signals_raw,
        opportunity_score=opp_result.total_score,
        opportunity_raw_score=opp_result.raw_score,
        factors_raw=factors_raw,
        price_history=price_tuples,
        price_slope=slope,
        price_r_squared=r_sq,
        mean_price=mean_price,
        coefficient_of_variation=cv,
        review_count=review_count,
        price_events_count=len(price_values),
        seller_count=seller_count,
        days_of_data=days_of_data,
    )

    return _report_to_response(report)


@router.post(
    "/custom",
    summary="Gerar relatório de análise a partir de dados fornecidos",
    response_model=AnalystReportResponse,
)
async def analyze_custom(data: CustomAnalysisInput) -> AnalystReportResponse:
    """Gera relatório com dados enviados diretamente no body.

    Útil para testar o analisador ou quando os dados já estão
    disponíveis fora do banco.
    """
    # Converte price_history do formato JSON para tuplas (datetime, float)
    price_history = []
    for entry in data.price_history:
        if len(entry) >= 2:
            try:
                dt = datetime.fromisoformat(entry[0])
                price = float(entry[1])
                price_history.append((dt, price))
            except (ValueError, TypeError):
                continue

    report = await run_analysis(
        product_id=data.product_id,
        product_title=data.product_title,
        category_id=data.category_id,
        category_name=data.category_name,
        seller_id=data.seller_id,
        product_condition=data.product_condition,
        listing_type=data.listing_type,
        initial_price=data.initial_price,
        signals_raw=data.signals,
        opportunity_score=data.opportunity_score,
        opportunity_raw_score=data.opportunity_raw_score,
        factors_raw=data.factors,
        price_history=price_history,
        price_slope=data.price_slope,
        price_r_squared=data.price_r_squared,
        mean_price=data.mean_price,
        coefficient_of_variation=data.coefficient_of_variation,
        review_count=data.review_count,
        price_events_count=data.price_events_count,
        seller_count=data.seller_count,
        days_of_data=data.days_of_data,
    )

    return _report_to_response(report)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _report_to_response(report) -> AnalystReportResponse:
    return AnalystReportResponse(
        product_id=report.product_id,
        product_title=report.product_title,
        category_name=report.category_name,
        generated_at=report.generated_at,
        executive_summary=report.executive_summary,
        risks=[
            RiskResponse(
                risk=r.risk,
                severity=r.severity,
                justification=r.justification,
                source_signals=r.source_signals,
            )
            for r in report.risks
        ],
        opportunities=[
            OpportunityResponse(
                opportunity=o.opportunity,
                potential=o.potential,
                justification=o.justification,
                source_signals=o.source_signals,
            )
            for o in report.opportunities
        ],
        recommendation_reasons=report.recommendation_reasons,
        possible_actions=[
            ActionResponse(
                action=a.action,
                priority=a.priority,
                reasoning=a.reasoning,
            )
            for a in report.possible_actions
        ],
        confidence={
            "level": report.confidence_level,
            "score": report.confidence_score,
            "justification": report.confidence_justification,
        },
    )
