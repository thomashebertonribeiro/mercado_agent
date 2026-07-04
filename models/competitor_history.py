"""
models/competitor_history.py

Historico de alteracoes de concorrentes monitorados.

Registra mudancas de preco, estoque, status e frete ao longo do tempo.
"""

from datetime import datetime, timezone
from sqlalchemy import Integer, String, DateTime, Float, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from models.base import Base


class CompetitorHistory(Base):
    __tablename__ = "competitor_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    competitor_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("competitors.id", ondelete="CASCADE"), nullable=False
    )
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    original_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    available_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    shipping_free: Mapped[bool | None] = mapped_column(nullable=True)
    listing_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    change_type: Mapped[str] = mapped_column(String(50), nullable=False)  # price, quantity, status, shipping
    change_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    def __repr__(self) -> str:
        return f"<CompetitorHistory(competitor_id={self.competitor_id}, type='{self.change_type}')>"
