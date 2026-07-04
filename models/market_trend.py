"""
models/market_trend.py

Tendencias de mercado coletadas periodicamente.

Armazena termos em alta, categorias com mais anuncios,
e oportunidades identificadas pelo sistema.
"""

from datetime import datetime, timezone
from sqlalchemy import Integer, String, DateTime, Float, Text
from sqlalchemy.orm import Mapped, mapped_column
from models.base import Base


class MarketTrend(Base):
    __tablename__ = "market_trends"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    keyword: Mapped[str] = mapped_column(String(200), nullable=False)
    category_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    category_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    trend_type: Mapped[str] = mapped_column(
        String(30), nullable=False, default="keyword"
    )  # keyword, category, opportunity
    total_items: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    def __repr__(self) -> str:
        return f"<MarketTrend(keyword='{self.keyword}', type='{self.trend_type}')>"
