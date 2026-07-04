"""
providers/base.py

Interface abstrata que todo marketplace deve implementar.

Cada provider expõe operações de:
  - Categorias (árvore, subcategorias)
  - Produtos (listagem por categoria, detalhes)
  - Vendedores (detalhes, reputação)
  - Busca (posição nos resultados)

Nenhuma lógica de negócio vive aqui — apenas contratos.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


# ─────────────────────────────────────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────────────────────────────────────


class ProductCondition(str, Enum):
    NEW = "new"
    USED = "used"
    REFURBISHED = "refurbished"


class ListingType(str, Enum):
    FREE = "free"
    BRONZE = "bronze"
    SILVER = "silver"
    GOLD_SPECIAL = "gold_special"
    GOLD_PREMIUM = "gold_premium"
    GOLD_PRO = "gold_pro"


class Currency(str, Enum):
    BRL = "BRL"
    ARS = "ARS"
    MXN = "MXN"
    COP = "COP"
    CLP = "CLP"
    PEN = "PEN"
    UYU = "UYU"


# ─────────────────────────────────────────────────────────────────────────────
# Schemas de dados (comuns a todos os providers)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class ProviderConfig:
    """Configuração compartilhada por todos os providers."""
    max_retries: int = 5
    backoff_base: float = 2.0
    max_wait: float = 60.0
    rate_limit_delay: float = 0.5
    max_concurrency: int = 5


@dataclass
class CategoryTree:
    """Categoria conforme retornado pelo marketplace."""
    id: str
    name: str
    parent_id: Optional[str] = None
    children: list[CategoryTree] = field(default_factory=list)
    total_items: Optional[int] = None
    path_from_root: Optional[list[dict]] = None


@dataclass
class ProductListing:
    """Produto em uma listagem (search ou categoria)."""
    id: str
    title: str
    price: float
    currency: Currency = Currency.BRL
    available_quantity: int = 0
    seller_id: Optional[int] = None
    seller_nickname: Optional[str] = None
    category_id: Optional[str] = None
    condition: ProductCondition = ProductCondition.NEW
    listing_type: ListingType = ListingType.FREE
    thumbnail: Optional[str] = None
    permalink: Optional[str] = None
    installments: Optional[dict] = None
    shipping_free: bool = False
    original_price: Optional[float] = None
    review_count: int = 0
    review_average: Optional[float] = None
    crawled_at: datetime = field(default_factory=datetime.now)


@dataclass
class ProductPage:
    """Detalhes completos de um produto."""
    id: str
    title: str
    price: float
    currency: Currency = Currency.BRL
    available_quantity: int = 0
    condition: ProductCondition = ProductCondition.NEW
    listing_type: ListingType = ListingType.FREE
    seller_id: Optional[int] = None
    seller_nickname: Optional[str] = None
    category_id: Optional[str] = None
    category_path: Optional[list[dict]] = None
    description: Optional[str] = None
    thumbnail: Optional[str] = None
    pictures: list[str] = field(default_factory=list)
    permalink: Optional[str] = None
    original_price: Optional[float] = None
    shipping_free: bool = False
    shipping_cost: Optional[float] = None
    weight: Optional[float] = None  # kg
    volume: Optional[float] = None  # cm³
    review_count: int = 0
    review_average: Optional[float] = None
    questions_count: int = 0
    tags: list[str] = field(default_factory=list)
    attributes: dict = field(default_factory=dict)
    crawled_at: datetime = field(default_factory=datetime.now)


@dataclass
class SellerPage:
    """Detalhes de um vendedor."""
    id: int
    nickname: str
    registration_date: Optional[datetime] = None
    seller_reputation: Optional[str] = None
    transactions_total: Optional[int] = None
    transactions_completed: Optional[int] = None
    rating_average: Optional[float] = None
    listing_type: Optional[str] = None
    country: Optional[str] = None
    domain: Optional[str] = None
    tags: list[str] = field(default_factory=list)
    crawled_at: datetime = field(default_factory=datetime.now)


@dataclass
class SearchResultPage:
    """Página de resultados de busca."""
    query: str
    results: list[ProductListing]
    total: int
    page: int
    page_size: int
    filters: Optional[dict] = None


# ─────────────────────────────────────────────────────────────────────────────
# Interface abstrata
# ─────────────────────────────────────────────────────────────────────────────


class MarketplaceProvider(ABC):
    """Interface que todo marketplace provider deve implementar.

    Operações separadas em 4 grupos:
      1. Categorias — navegação na árvore de categorias
      2. Produtos — listagem e detalhes
      3. Vendedores — informações do seller
      4. Busca — pesquisa textual
    """

    # ── 1. Categorias ──────────────────────────────────────────────────

    @abstractmethod
    async def get_root_categories(self) -> list[CategoryTree]:
        """Retorna categorias raiz do marketplace."""
        ...

    @abstractmethod
    async def get_subcategories(self, category_id: str) -> list[CategoryTree]:
        """Retorna subcategorias de uma categoria."""
        ...

    @abstractmethod
    async def get_category_path(self, category_id: str) -> list[dict]:
        """Retorna o caminho completo da raiz até a categoria."""
        ...

    # ── 2. Produtos ────────────────────────────────────────────────────

    @abstractmethod
    async def get_products_by_category(
        self,
        category_id: str,
        offset: int = 0,
        limit: int = 50,
    ) -> list[ProductListing]:
        """Retorna produtos de uma categoria (paginação)."""
        ...

    @abstractmethod
    async def get_product_detail(self, product_id: str) -> Optional[ProductPage]:
        """Retorna detalhes completos de um produto."""
        ...

    @abstractmethod
    async def get_product_description(self, product_id: str) -> Optional[str]:
        """Retorna descrição textual de um produto."""
        ...

    # ── 3. Vendedores ──────────────────────────────────────────────────

    @abstractmethod
    async def get_seller_detail(self, seller_id: int) -> Optional[SellerPage]:
        """Retorna detalhes de um vendedor."""
        ...

    # ── 4. Busca ───────────────────────────────────────────────────────

    @abstractmethod
    async def search(
        self,
        query: str,
        category_id: Optional[str] = None,
        offset: int = 0,
        limit: int = 50,
    ) -> SearchResultPage:
        """Pesquisa textual no marketplace."""
        ...

    # ── 5. Atributos do provider ───────────────────────────────────────

    @property
    @abstractmethod
    def name(self) -> str:
        """Nome único do provider (ex: 'mercadolivre', 'amazon')."""
        ...

    @property
    @abstractmethod
    def supported_currencies(self) -> list[Currency]:
        """Moedas suportadas por este marketplace."""
        ...
