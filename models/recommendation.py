"""models/recommendation.py — Recomendações priorizadas."""

from datetime import datetime, timezone
from sqlalchemy import BigInteger, String, DateTime, Float, Text, Index, Integer
from sqlalchemy.orm import Mapped, mapped_column
from models.base import Base


class Recommendation(Base):
    """Recomendação gerada pelo Recommendation Engine.

    Cada recomendação é um item priorizado (product, category, seller, etc.)
    com um score, motivo, e tipo de recomendação.
    """

    __tablename__ = "recommendations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # Tipo: "product_opportunity" | "category_trend" | "new_product" | "abandoned" | ...
    recommendation_type: Mapped[str] = mapped_column(String(60), nullable=False, index=True)

    # IDs associados
    product_id: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    category_id: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    seller_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # Score de prioridade (0-100)
    priority_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Título legível
    title: Mapped[str] = mapped_column(String(500), nullable=False)

    # Descrição / motivo
    reason: Mapped[str] = mapped_column(Text, nullable=False)

    # Metadados
    source: Mapped[str] = mapped_column(String(50), nullable=False, default="recommendation_engine")
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
        Index("ix_recommendations_type_score", "recommendation_type", "priority_score"),
        Index("ix_recommendations_hash", "hash"),
    )

    def __repr__(self) -> str:
        return f"<Recommendation(id={self.id}, type='{self.recommendation_type}', score={self.priority_score})>"
