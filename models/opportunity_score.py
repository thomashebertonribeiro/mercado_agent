"""models/opportunity_score.py — Resultados do Opportunity Score."""

from datetime import datetime, timezone
from sqlalchemy import BigInteger, String, DateTime, Float, Text, Index, Integer
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from models.base import Base


class OpportunityScoreResult(Base):
    """Resultado persistido do Opportunity Score para um produto.

    Armazena o score total, raw score, e detalhamento dos fatores
    em JSONB para consulta rápida sem recomputar.
    """

    __tablename__ = "opportunity_scores"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    product_id: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True,
    )
    category_id: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        index=True,
    )

    total_score: Mapped[int] = mapped_column(Integer, nullable=False)  # 0-100
    raw_score: Mapped[float] = mapped_column(Float, nullable=False)  # 0.0-1.0

    # Detalhamento completo em JSONB
    factors: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # Metadados da computação
    source: Mapped[str] = mapped_column(String(50), nullable=False, default="opportunity_engine")
    hash: Mapped[str] = mapped_column(String(64), nullable=False)

    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
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

    __table_args__ = (
        Index("ix_opp_score_product", "product_id", "computed_at"),
        Index("ix_opp_score_category", "category_id", "computed_at"),
    )

    def __repr__(self) -> str:
        return f"<OpportunityScoreResult(product='{self.product_id}', score={self.total_score})>"
