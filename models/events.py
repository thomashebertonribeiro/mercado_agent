"""
models/events.py

Todas as tabelas de eventos do sistema — imutáveis após inserção.

Princípio: um evento só é criado quando houve uma MUDANÇA real.
O hash SHA-256 de cada evento é comparado com o último evento
gravado antes de qualquer INSERT. Se iguais, nada é escrito.

Tabelas particionadas por RANGE (occurred_at) — partições mensais.
A PK composta (id, occurred_at) é obrigatória para tabelas particionadas
no PostgreSQL — a coluna de particionamento DEVE fazer parte da PK.

IMPORTANTE: A criação física das tabelas (DDL com PARTITION BY) é feita
na migration 0003 via raw SQL — SQLAlchemy não abstrai isso completamente.
Os modelos aqui servem para ORM queries (SELECT, INSERT via session.add).

Tabelas:
  - product_events   : mudanças de status, título, tipo de anúncio
  - price_events     : mudanças de preço, estoque, moeda
  - seller_events    : mudanças de reputação, nível, métricas
  - ranking_events   : mudanças de posição em buscas
  - review_events    : avaliações recebidas
  - question_events  : perguntas e respostas
"""

from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import (
    BigInteger, String, DateTime, Numeric, Integer,
    SmallInteger, Boolean, Text, ForeignKey, Index,
    PrimaryKeyConstraint, CheckConstraint, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from models.base import Base


# ─────────────────────────────────────────────────────────────────────────────
# product_events
# ─────────────────────────────────────────────────────────────────────────────

class ProductEvent(Base):
    """
    Registra mudanças estruturais no anúncio: status, título,
    tipo de listagem, thumbnail, etc.

    event_type: "status_change" | "title_change" | "listing_type_change" | "thumbnail_change"
    old_value / new_value: representação textual do valor anterior e novo.
    """

    __tablename__ = "product_events"

    id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    product_id: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(60), nullable=False)
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    source: Mapped[str] = mapped_column(String(50), nullable=False, default="api")
    hash: Mapped[str] = mapped_column(String(64), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    product = relationship("Product", back_populates="product_events", lazy="noload")

    __table_args__ = (
        PrimaryKeyConstraint("id", "occurred_at", name="pk_product_events"),
        CheckConstraint(
            "event_type IN ('status_change','title_change','listing_type_change','thumbnail_change','other')",
            name="ck_product_events_type",
        ),
        Index("ix_product_events_product_occurred", "product_id", "occurred_at"),
        Index("ix_product_events_type", "event_type"),
        Index("ix_product_events_hash", "hash"),
        {"postgresql_partition_by": "RANGE (occurred_at)"},
    )


# ─────────────────────────────────────────────────────────────────────────────
# price_events
# ─────────────────────────────────────────────────────────────────────────────

class PriceEvent(Base):
    """
    Registra mudanças de preço, preço original e estoque.

    Separado de product_events porque:
    1. É o dado mais consultado — índice isolado tem melhor performance
    2. É o mais frequente — cada partição mensal fica menor e mais gerenciável
    3. Permite queries analíticas específicas sem JOIN com outros eventos

    sold_qty: quantidade vendida acumulada no momento do evento.
              Permite calcular velocidade de vendas por período.
    """

    __tablename__ = "price_events"

    id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    product_id: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
    )
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    original_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    currency_id: Mapped[str] = mapped_column(String(10), nullable=False, default="BRL")
    available_qty: Mapped[int] = mapped_column(Integer, nullable=False)
    sold_qty: Mapped[int | None] = mapped_column(Integer, nullable=True)

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    source: Mapped[str] = mapped_column(String(50), nullable=False, default="api")
    hash: Mapped[str] = mapped_column(String(64), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    product = relationship("Product", back_populates="price_events", lazy="noload")

    __table_args__ = (
        PrimaryKeyConstraint("id", "occurred_at", name="pk_price_events"),
        CheckConstraint("price > 0", name="ck_price_events_price_positive"),
        CheckConstraint("available_qty >= 0", name="ck_price_events_qty_non_negative"),
        CheckConstraint("sold_qty >= 0", name="ck_price_events_sold_non_negative"),
        # Índice principal de lookup — a maioria das queries filtra por produto + período
        Index("ix_price_events_product_occurred", "product_id", "occurred_at"),
        Index("ix_price_events_hash", "hash"),
        {"postgresql_partition_by": "RANGE (occurred_at)"},
    )


# ─────────────────────────────────────────────────────────────────────────────
# seller_events
# ─────────────────────────────────────────────────────────────────────────────

class SellerEvent(Base):
    """
    Registra mudanças no perfil do vendedor: reputação, nível, métricas.

    positive_pct / negative_pct / neutral_pct:
        Percentuais de avaliações do período mais recente.
        Valores de 0.00 a 100.00.
    """

    __tablename__ = "seller_events"

    id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    seller_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("sellers.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    reputation_level: Mapped[str | None] = mapped_column(String(30), nullable=True)
    positive_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    negative_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    neutral_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    transactions_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    points: Mapped[int | None] = mapped_column(Integer, nullable=True)

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    source: Mapped[str] = mapped_column(String(50), nullable=False, default="api")
    hash: Mapped[str] = mapped_column(String(64), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    seller = relationship("Seller", back_populates="seller_events", lazy="noload")

    __table_args__ = (
        PrimaryKeyConstraint("id", "occurred_at", name="pk_seller_events"),
        CheckConstraint(
            "event_type IN ('reputation_change','level_change','transactions_milestone','other')",
            name="ck_seller_events_type",
        ),
        CheckConstraint(
            "positive_pct IS NULL OR (positive_pct >= 0 AND positive_pct <= 100)",
            name="ck_seller_events_positive_pct",
        ),
        Index("ix_seller_events_seller_occurred", "seller_id", "occurred_at"),
        Index("ix_seller_events_hash", "hash"),
        {"postgresql_partition_by": "RANGE (occurred_at)"},
    )


# ─────────────────────────────────────────────────────────────────────────────
# ranking_events
# ─────────────────────────────────────────────────────────────────────────────

class RankingEvent(Base):
    """
    Registra mudanças de posição de um produto em buscas orgânicas.

    Dado extremamente volátil — coletar e gravar apenas quando position mudar.

    search_term: termo exato buscado (ex: "iphone 15 pro")
    position   : posição na página de resultados (1 = primeiro)
    page       : número da página (começa em 1)

    Índice composto (product_id, search_term, occurred_at DESC) permite
    recuperar o histórico de ranking de um produto para um termo específico
    em uma única operação de índice.
    """

    __tablename__ = "ranking_events"

    id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    product_id: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
    )
    search_term: Mapped[str] = mapped_column(String(255), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    page: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    source: Mapped[str] = mapped_column(String(50), nullable=False, default="api")
    hash: Mapped[str] = mapped_column(String(64), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    product = relationship("Product", back_populates="ranking_events", lazy="noload")

    __table_args__ = (
        PrimaryKeyConstraint("id", "occurred_at", name="pk_ranking_events"),
        CheckConstraint("position > 0", name="ck_ranking_events_position_positive"),
        CheckConstraint("page > 0", name="ck_ranking_events_page_positive"),
        # Índice principal: histórico de ranking por produto+termo
        Index(
            "ix_ranking_events_product_term_occurred",
            "product_id", "search_term", "occurred_at",
        ),
        Index("ix_ranking_events_hash", "hash"),
        {"postgresql_partition_by": "RANGE (occurred_at)"},
    )


# ─────────────────────────────────────────────────────────────────────────────
# review_events
# ─────────────────────────────────────────────────────────────────────────────

class ReviewEvent(Base):
    """
    Registra avaliações recebidas pelo produto.

    Cada review é um evento imutável — reviews não mudam após publicação.
    UNIQUE (product_id, reviewer_id, occurred_at) previne duplicatas
    caso o crawler passe pelo mesmo review em coletas diferentes.

    fulfilled: True se a compra foi com fulfillment do ML (entrega ML).
    """

    __tablename__ = "review_events"

    id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    product_id: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
    )
    # ID do review no ML — usado para deduplicação natural
    review_ml_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    rating: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewer_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    fulfilled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    source: Mapped[str] = mapped_column(String(50), nullable=False, default="api")
    hash: Mapped[str] = mapped_column(String(64), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    product = relationship("Product", back_populates="review_events", lazy="noload")

    __table_args__ = (
        PrimaryKeyConstraint("id", "occurred_at", name="pk_review_events"),
        CheckConstraint(
            "rating >= 1 AND rating <= 5", name="ck_review_events_rating_range"
        ),
        # Deduplicação: mesmo review não pode ser inserido duas vezes
        # (product_id, review_ml_id) é suficiente quando disponível
        Index("ix_review_events_product_occurred", "product_id", "occurred_at"),
        Index("ix_review_events_ml_id", "review_ml_id"),
        Index("ix_review_events_hash", "hash"),
        {"postgresql_partition_by": "RANGE (occurred_at)"},
    )


# ─────────────────────────────────────────────────────────────────────────────
# question_events
# ─────────────────────────────────────────────────────────────────────────────

class QuestionEvent(Base):
    """
    Registra perguntas recebidas no anúncio e suas respostas.

    question_ml_id: ID único da pergunta no Mercado Livre.
    answered      : True quando já tem resposta do vendedor.
    answer_text   : null enquanto a pergunta estiver sem resposta.

    O estado "respondido" gera um segundo evento (answered=True, answer_text preenchido)
    sem sobrescrever o evento original — preservando o histórico de tempo de resposta.
    """

    __tablename__ = "question_events"

    id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    product_id: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
    )
    question_ml_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    question_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    answer_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    answered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    source: Mapped[str] = mapped_column(String(50), nullable=False, default="api")
    hash: Mapped[str] = mapped_column(String(64), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    product = relationship("Product", back_populates="question_events", lazy="noload")

    __table_args__ = (
        PrimaryKeyConstraint("id", "occurred_at", name="pk_question_events"),
        Index(
            "ix_question_events_product_occurred", "product_id", "occurred_at"
        ),
        Index("ix_question_events_ml_id", "question_ml_id"),
        Index("ix_question_events_hash", "hash"),
        {"postgresql_partition_by": "RANGE (occurred_at)"},
    )
