"""
models/seller.py

Tabela dimensão de vendedores do Mercado Livre.

Armazena o estado ATUAL do vendedor. Mudanças de reputação, nível e
métricas são registradas em `seller_events` (event sourcing).

Campos adicionados vs. versão anterior:
  - site_id       : país/domínio (MLB, MLA, MLM...)
  - level_id      : nível ML (5_green, 4_light_green, etc.)
  - points        : pontos de reputação
  - transactions_total : total de transações concluídas
  - source, hash  : rastreabilidade e deduplicação

hash: SHA-256 de (id + nickname + level_id + points + transactions_total)
"""

from datetime import datetime, timezone
from sqlalchemy import BigInteger, String, DateTime, Integer, Index, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from models.base import Base


class Seller(Base):
    __tablename__ = "sellers"

    # ID oficial do Mercado Livre (BigInt — IDs ML podem ultrapassar 2^31)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    nickname: Mapped[str] = mapped_column(String(150), nullable=False)

    # País / domínio: "MLB" (Brasil), "MLA" (Argentina), "MLM" (México)...
    site_id: Mapped[str | None] = mapped_column(String(10), nullable=True)

    # Nível de reputação: "5_green", "4_light_green", "3_yellow", "2_orange", "1_red"
    level_id: Mapped[str | None] = mapped_column(String(30), nullable=True)

    # Pontos de reputação acumulados
    points: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Total de transações concluídas na conta
    transactions_total: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Data de registro no ML (quando a conta foi criada)
    registration_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Rastreabilidade e deduplicação
    source: Mapped[str] = mapped_column(String(50), nullable=False, default="api")
    hash: Mapped[str] = mapped_column(String(64), nullable=False)  # SHA-256

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
    products = relationship("Product", back_populates="seller", lazy="noload")
    seller_events = relationship("SellerEvent", back_populates="seller", lazy="noload")

    __table_args__ = (
        CheckConstraint("points >= 0", name="ck_sellers_points_positive"),
        CheckConstraint("transactions_total >= 0", name="ck_sellers_transactions_positive"),
        Index("ix_sellers_nickname", "nickname"),
        Index("ix_sellers_site_id", "site_id"),
        Index("ix_sellers_hash", "hash"),
    )

    def __repr__(self) -> str:
        return f"<Seller(id={self.id}, nickname='{self.nickname}', level='{self.level_id}')>"
