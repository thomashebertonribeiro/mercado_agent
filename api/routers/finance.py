"""
api/routers/finance.py

Endpoints do Finance Engine.

Gerenciamento de custos e metricas financeiras.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Body
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from database.connection import get_db_session
from services.finance_service import FinanceService
from dto.finance import CostInput
from utils.logger import logger

router = APIRouter(prefix="/finance", tags=["Finance Engine"])


# ── Cost Management ──────────────────────────────────────────────────

@router.post("/costs")
async def set_cost(
    product_id: str = Query(..., description="ID do produto ML"),
    seller_id: int = Query(..., description="ID do vendedor"),
    product_cost: float = Query(0, description="Custo do produto (COGS)"),
    shipping_cost: float = Query(0, description="Custo de frete por unidade"),
    packaging_cost: float = Query(0, description="Custo de embalagem por unidade"),
    ads_cost: float = Query(0, description="Custo de ads por unidade"),
    other_variable_cost: float = Query(0, description="Outros custos variaveis"),
    monthly_fixed_cost: float = Query(0, description="Custos fixos mensais rateados"),
    tax_rate: float = Query(0, description="Taxa de imposto (%)"),
    ml_commission_rate: float = Query(13, description="Comissao ML (%)"),
    notes: str = Query(None, description="Observacoes"),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Define ou atualiza o custo de um produto."""
    try:
        finance = FinanceService(db)
        input_data = CostInput(
            product_id=product_id,
            seller_id=seller_id,
            product_cost=product_cost,
            shipping_cost=shipping_cost,
            packaging_cost=packaging_cost,
            ads_cost=ads_cost,
            other_variable_cost=other_variable_cost,
            monthly_fixed_cost=monthly_fixed_cost,
            tax_rate=tax_rate,
            ml_commission_rate=ml_commission_rate,
            notes=notes,
        )
        return await finance.set_product_cost(input_data)
    except Exception as e:
        logger.error(f"Erro ao salvar custo: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class BulkCostRequest(BaseModel):
    seller_id: int
    costs: list[dict]


@router.post("/costs/bulk")
async def bulk_set_costs(
    request: BulkCostRequest,
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Atualizacao em lote de custos."""
    try:
        finance = FinanceService(db)
        return await finance.bulk_set_costs(request.seller_id, request.costs)
    except Exception as e:
        logger.error(f"Erro ao atualizar custos em lote: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/costs/{product_id}")
async def get_cost_history(
    product_id: str,
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Historico de alteracoes de custo de um produto."""
    finance = FinanceService(db)
    history = await finance.get_cost_history(product_id)
    return {"product_id": product_id, "history": history}


# ── Financial Metrics ────────────────────────────────────────────────

@router.get("/metrics/{product_id}")
async def get_product_metrics(
    product_id: str,
    sold_quantity: int = Query(0, description="Quantidade vendida (periodo)"),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Metricas financeiras completas de um produto."""
    try:
        finance = FinanceService(db)
        metrics = await finance.calculate_product_metrics(product_id, sold_quantity)
        return metrics.to_dict()
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Erro ao calcular metricas: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/summary/{seller_id}")
async def get_seller_summary(
    seller_id: int,
    days: int = Query(30, description="Periodo em dias"),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Resumo financeiro consolidado do vendedor."""
    try:
        finance = FinanceService(db)
        summary = await finance.calculate_seller_summary(seller_id, days)
        return summary.to_dict()
    except Exception as e:
        logger.error(f"Erro ao calcular resumo: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/analysis/{seller_id}")
async def analyze_all_products(
    seller_id: int,
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Analise financeira de todos os produtos do vendedor."""
    try:
        finance = FinanceService(db)
        results = await finance.analyze_all_products(seller_id)
        return {
            "seller_id": seller_id,
            "total_products": len(results),
            "products": results,
        }
    except Exception as e:
        logger.error(f"Erro na analise: {e}")
        raise HTTPException(status_code=500, detail=str(e))
