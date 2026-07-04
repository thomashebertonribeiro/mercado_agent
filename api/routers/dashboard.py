"""
api/routers/dashboard.py

Endpoints do dashboard de inteligencia de mercado.

Indicadores:
  - Meus anuncios (quantidade, vendas, faturamento)
  - Concorrentes (alteracoes de preco, estoque, status)
  - Mercado (tendencias, categorias, oportunidades)
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from database.connection import get_db_session
from services.sync_service import SyncService
from repositories.competitor import CompetitorRepository
from repositories.market_trend import MarketTrendRepository
from repositories.marketplace_account import MarketplaceAccountRepository
from utils.logger import logger

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


@router.get("/overview")
async def get_overview(
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """
    Visao geral do dashboard com indicadores principais.
    """
    account_repo = MarketplaceAccountRepository(db)
    comp_repo = CompetitorRepository(db)
    trend_repo = MarketTrendRepository(db)

    accounts = await account_repo.get_all("mercadolivre")
    active_competitors = await comp_repo.count_active()
    recent_changes = await comp_repo.get_recent_changes(limit=10)
    trends = await trend_repo.get_latest(limit=10)

    return {
        "accounts": {
            "total": len(accounts),
            "connected": [
                {"user_id": a.user_id, "nickname": a.nickname, "marketplace": a.marketplace}
                for a in accounts
            ],
        },
        "competitors": {
            "active": active_competitors,
            "recent_changes": [
                {
                    "competitor_id": c.competitor_id,
                    "type": c.change_type,
                    "detail": c.change_detail,
                    "recorded_at": c.recorded_at.isoformat(),
                }
                for c in recent_changes
            ],
        },
        "market": {
            "trends_count": len(trends),
            "trends": [{"keyword": t.keyword, "collected_at": t.collected_at.isoformat()} for t in trends],
        },
    }


@router.get("/my-items")
async def get_my_items(
    user_id: int = Query(..., description="User ID do vendedor ML"),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """
    Retorna metricas dos anuncios do vendedor.
    """
    try:
        sync = SyncService(db)
        item_ids = await sync.fetch_seller_items(user_id)
        items = await sync.fetch_items_batch(item_ids[:50], user_id)

        total_value = sum(float(i.get("price", 0)) * i.get("available_quantity", 0) for i in items)
        free_shipping = sum(1 for i in items if i.get("shipping", {}).get("free_shipping"))

        return {
            "user_id": user_id,
            "total_items": len(item_ids),
            "items_fetched": len(items),
            "total_stock_value": round(total_value, 2),
            "items_with_free_shipping": free_shipping,
            "items": [
                {
                    "id": i.get("id"),
                    "title": i.get("title", "")[:60],
                    "price": i.get("price"),
                    "available_quantity": i.get("available_quantity"),
                    "status": i.get("status"),
                    "category_id": i.get("category_id"),
                }
                for i in items[:20]
            ],
        }
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/competitors")
async def get_competitors(
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """
    Lista concorrentes monitorados e suas alteracoes recentes.
    """
    comp_repo = CompetitorRepository(db)
    competitors = await comp_repo.get_active_competitors()
    changes = await comp_repo.get_recent_changes(limit=20)

    return {
        "total": len(competitors),
        "competitors": [
            {
                "id": c.id,
                "ml_item_id": c.ml_item_id,
                "title": (c.title or "")[:60],
                "current_price": c.current_price,
                "available_quantity": c.available_quantity,
                "status": c.status,
                "last_checked_at": c.last_checked_at.isoformat() if c.last_checked_at else None,
            }
            for c in competitors
        ],
        "recent_changes": [
            {
                "competitor_id": ch.competitor_id,
                "type": ch.change_type,
                "detail": ch.change_detail,
                "recorded_at": ch.recorded_at.isoformat(),
            }
            for ch in changes
        ],
    }


@router.post("/competitors")
async def add_competitor(
    ml_item_id: str = Query(..., description="Item ID do ML (ex: MLB1234567890)"),
    user_id: int = Query(..., description="User ID do vendedor autenticado"),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """
    Cadastra um concorrente para monitoramento.

    O sistema extrai o item_id de URLs como:
    - MLB1234567890
    - https://produto.mercadolivre.com.br/MLB-1234567890
    """
    clean_id = _extract_item_id(ml_item_id)
    if not clean_id:
        raise HTTPException(status_code=400, detail="Item ID invalido")

    try:
        sync = SyncService(db)
        result = await sync.monitor_competitor(clean_id, user_id)
        return {"message": f"Concorrente {clean_id} cadastrado com sucesso", "result": result}
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Erro ao cadastrar concorrente: {e}")
        raise HTTPException(status_code=500, detail="Erro ao cadastrar concorrente")


@router.get("/trends")
async def get_trends(
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """
    Retorna tendencias de mercado coletadas.
    """
    trend_repo = MarketTrendRepository(db)
    trends = await trend_repo.get_latest(limit=30)
    return {
        "total": len(trends),
        "trends": [
            {
                "keyword": t.keyword,
                "category_id": t.category_id,
                "category_name": t.category_name,
                "trend_type": t.trend_type,
                "collected_at": t.collected_at.isoformat(),
            }
            for t in trends
        ],
    }


@router.post("/sync/trends")
async def trigger_sync_trends(
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """
    Dispara sincronizacao manual de tendencias.
    """
    sync = SyncService(db)
    trends = await sync.fetch_trends()
    return {"message": f"{len(trends)} tendencias coletadas"}


def _extract_item_id(raw: str) -> str | None:
    """Extrai item_id de uma string (URL ou ID direto)."""
    import re
    raw = raw.strip()
    if re.match(r"^MLB\d+$", raw):
        return raw
    match = re.search(r"(MLB-?\d+)", raw, re.IGNORECASE)
    if match:
        return match.group(1).replace("-", "")
    return None
