"""
scanner/models.py

Modelos de domínio do Market Scanner.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class CategoryScanFrequency(str, Enum):
    """Frequência de varredura baseada na movimentação da categoria."""
    HIGH = "high"       # muito movimentada — varredura frequente
    MEDIUM = "medium"   # movimentação normal
    LOW = "low"         # pouca movimentação — varredura esporádica
    ONCE = "once"       # varredura única (categoria vazia ou irrelevante)


@dataclass
class CategoryScanState:
    """Estado de varredura de uma categoria."""
    category_id: str
    category_name: str
    parent_id: Optional[str]
    path: list[dict]
    total_items: int
    frequency: CategoryScanFrequency
    last_scanned_at: Optional[datetime] = None
    next_scan_at: Optional[datetime] = None
    scan_count: int = 0
    is_leaf: bool = True  # não tem subcategorias
    children: list[CategoryScanState] = field(default_factory=list)


@dataclass
class Discovery:
    """Item descoberto durante a varredura."""
    product_id: str
    title: str
    category_id: str
    seller_id: Optional[int]
    price: float
    discovered_at: datetime = field(default_factory=datetime.now)
    is_new: bool = True  # primeira vez que vemos este produto


@dataclass
class ScanResult:
    """Resultado de um ciclo de varredura."""
    categories_scanned: int = 0
    products_found: int = 0
    new_products: int = 0
    removed_products: int = 0
    new_sellers: set[int] = field(default_factory=set)
    errors: int = 0
    started_at: datetime = field(default_factory=datetime.now)
    finished_at: Optional[datetime] = None
    duration_seconds: float = 0.0
