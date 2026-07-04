"""
models/signal.py

Modelo para os sinais gerados pela Intelligence Engine.

Cada sinal representa uma observação matemática sobre um produto ou
categoria, com peso, confiança e explicação legível.

Não utiliza IA — apenas estatística descritiva e álgebra simples.
"""

from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import (
    BigInteger, String, DateTime, Numeric, Integer,
    ForeignKey, Index, Text, Float,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from models.base import Base


class Signal(Base):
    """Sinal gerado pela Intelligence Engine.

    signal_type: identifica o tipo de sinal (ex: "price_growth", "high_volatility")
    weight: importancia relativa do sinal (0.0 a 1.0)
    confidence: grau de confianca matematica (0.0 a 1.0)
    explanation: texto legivel explicando o sinal
    """

    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )

    signal_type: Mapped[str] = mapped_column(
        String(60), nullable=False, index=True
    )

    product_id: Mapped[str | None] = mapped_column(
        String(50),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    category_id: Mapped[str | None] = mapped_column(
        String(50),
        ForeignKey("categories.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    weight: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    explanation: Mapped[str] = mapped_column(Text, nullable=False)

    value: Mapped[Decimal | None] = mapped_column(
        Numeric(15, 4), nullable=True
    )

    extra_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    source: Mapped[str] = mapped_column(
        String(50), nullable=False, default="intelligence_engine"
    )
    hash: Mapped[str] = mapped_column(String(64), nullable=False)

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
        Index("ix_signals_type_product", "signal_type", "product_id"),
        Index("ix_signals_type_category", "signal_type", "category_id"),
        Index("ix_signals_computed_at", "computed_at"),
        Index("ix_signals_hash", "hash"),
    )

    def __repr__(self) -> str:
        return (
            f"<Signal(id={self.id}, type='{self.signal_type}', "
            f"confidence={self.confidence}, weight={self.weight})>"
        )
