"""
models/product.py

Tabela dimensão de produtos do Mercado Livre.

Armazena apenas o estado ATUAL do produto (metadados estáticos/raramente mutáveis).
Toda variação dinâmica (preço, estoque, status, ranking, reviews) é registrada
nas respectivas tabelas de eventos por event sourcing.

Campos adicionados vs. versão anterior:
  - category_id   : FK → categories(id)
  - condition     : "new", "used", "refurbished"
  - listing_type_id: "gold_special", "gold_pro", "free", etc.
  - initial_price : preço no momento do primeiro crawl (âncora histórica)
  - initial_qty   : estoque no momento do primeiro crawl
  - source, hash  : rastreabilidade e deduplicação

Removido:
  - relacionamento com ProductMetricsHistory (substituído por price_events)

hash: SHA-256 de (id + title + category_id + seller_id + condition + listing_type_id)
"""

from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import (
    BigInteger, String, DateTime, Numeric, Integer,
    ForeignKey, Index, CheckConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from models.base import Base


class Product(Base):
    __tablename__ = "products"

    # ID oficial ML: ex "MLB3396781234"
    id: Mapped[str] = mapped_column(String(50), primary_key=True)

    title: Mapped[str] = mapped_column(String(500), nullable=False)

    # FK → categories(id) — pode ser null temporariamente durante ingestão
    category_id: Mapped[str | None] = mapped_column(
        String(50),
        ForeignKey("categories.id", ondelete="SET NULL"),
        nullable=True,
    )

    # FK → sellers(id)
    seller_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("sellers.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Condição do produto
    # CHECK constraint garante integridade sem precisar de tabela de lookup
    condition: Mapped[str | None] = mapped_column(String(30), nullable=True)

    # Tipo de listagem: "gold_special", "gold_pro", "free", "bronze", etc.
    listing_type_id: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Preço e estoque no momento do PRIMEIRO crawl — âncora histórica imutável
    # Permite calcular variação total sem consultar eventos
    initial_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    initial_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)

    permalink: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    # Rastreabilidade e deduplicação
    source: Mapped[str] = mapped_column(String(50), nullable=False, default="api")
    hash: Mapped[str] = mapped_column(String(64), nullable=False)  # SHA-256

    last_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relacionamentos
    seller = relationship("Seller", back_populates="products", lazy="noload")
    category = relationship("Category", back_populates="products", lazy="noload")

    # Eventos — lazy=noload para não carregar automaticamente (tabelas grandes)
    price_events = relationship("PriceEvent", back_populates="product", lazy="noload")
    product_events = relationship("ProductEvent", back_populates="product", lazy="noload")
    ranking_events = relationship("RankingEvent", back_populates="product", lazy="noload")
    review_events = relationship("ReviewEvent", back_populates="product", lazy="noload")
    question_events = relationship("QuestionEvent", back_populates="product", lazy="noload")
    insights = relationship("Insight", back_populates="product", lazy="noload")

    __table_args__ = (
        CheckConstraint(
            "condition IN ('new', 'used', 'refurbished')",
            name="ck_products_condition",
        ),
        CheckConstraint("initial_price > 0", name="ck_products_initial_price_positive"),
        CheckConstraint("initial_quantity >= 0", name="ck_products_initial_qty_positive"),
        Index("ix_products_seller_id", "seller_id"),
        Index("ix_products_category_id", "category_id"),
        Index("ix_products_hash", "hash"),
        Index("ix_products_listing_type", "listing_type_id"),
    )

    def __repr__(self) -> str:
        return f"<Product(id='{self.id}', title='{self.title[:30]}')>"
