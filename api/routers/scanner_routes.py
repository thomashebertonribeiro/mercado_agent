"""
api/routers/scanner_routes.py

Endpoints do Market Scanner e descoberta.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from database.connection import AsyncSessionLocal, get_db_session
from providers.mercadolivre import MercadoLivreProvider
from scanner.scanner import MarketScanner
from utils.logger import logger

router = APIRouter(prefix="/scanner", tags=["Market Scanner"])


class ScannerStatusResponse(BaseModel):
    running: bool
    categories_loaded: bool
    total_categories: int
    leaf_categories: int
    due_categories: int


class ScanResultResponse(BaseModel):
    categories_scanned: int
    products_found: int
    new_products: int
    removed_products: int
    new_sellers: int
    errors: int
    duration_seconds: float
    finished_at: str | None = None


# Scanner global (singleton por worker)
_scanner: MarketScanner | None = None


def _get_scanner() -> MarketScanner:
    global _scanner
    if _scanner is None:
        _scanner = MarketScanner(
            provider=MercadoLivreProvider(),
            session_factory=AsyncSessionLocal,
        )
    return _scanner


@router.post("/start", status_code=status.HTTP_202_ACCEPTED)
async def start_scanner() -> dict:
    """Inicia varredura completa de todas as categorias."""
    scanner = _get_scanner()
    try:
        await scanner.initialize()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Falha ao inicializar scanner: {exc}",
        )

    return {"message": "Scanner inicializado. Varredura em andamento.", "categories_loaded": True}


@router.post("/category/{category_id}")
async def scan_category(category_id: str) -> ScanResultResponse:
    """Varre uma categoria específica."""
    category_id = category_id.strip().upper()
    scanner = _get_scanner()
    try:
        await scanner.initialize()
    except Exception:
        pass

    result = await scanner.scan_category(category_id)
    return ScanResultResponse(
        categories_scanned=result.categories_scanned,
        products_found=result.products_found,
        new_products=result.new_products,
        removed_products=result.removed_products,
        new_sellers=len(result.new_sellers),
        errors=result.errors,
        duration_seconds=round(result.duration_seconds, 2),
        finished_at=result.finished_at.isoformat() if result.finished_at else None,
    )


@router.get("/status")
async def scanner_status() -> ScannerStatusResponse:
    """Retorna status atual do scanner."""
    scanner = _get_scanner()
    try:
        await scanner.initialize()
    except Exception:
        return ScannerStatusResponse(
            running=False,
            categories_loaded=False,
            total_categories=0,
            leaf_categories=0,
            due_categories=0,
        )

    tree = scanner.get_category_tree()

    def count_all(cats):
        return sum(1 + count_all(c.children) for c in cats)
    def count_leaves(cats):
        return sum(count_leaves(c.children) if not c.is_leaf else 1 for c in cats)
    def count_due(cats):
        return sum(count_due(c.children) if not c.is_leaf else 1 for c in cats if c.next_scan_at)

    return ScannerStatusResponse(
        running=True,
        categories_loaded=True,
        total_categories=count_all(tree),
        leaf_categories=count_leaves(tree),
        due_categories=count_due(tree),
    )
