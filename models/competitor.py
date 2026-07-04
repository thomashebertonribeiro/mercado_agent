"""
models/competitor.py

Tabela de concorrentes monitorados manualmente pelo vendedor.

O usuario cadastra itens do Mercado Livre (por item_id ou URL)
e o sistema passa a monitorar preco, estoque e status automaticamente.
"""

from datetime import datetime, timezone
from sqlalchemy import BigInteger, String, DateTime, Integer, Boolean, Text
from sqlalchemy.orm import Mapped, mapped_column
from models.base import Base


class Competitor(Base):
    __tablename__ = "competitors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ml_item_id: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    seller_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    title: Mapped[str] = mapped_column(String(500), nullable=True, default="")
    category_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    current_price: Mapped[float | None] = mapped_column(nullable=True)
    original_price: Mapped[float | None] = mapped_column(nullable=True)
    available_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    listing_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    shipping_free: Mapped[bool] = mapped_column(Boolean, default=False)
    permalink: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    thumbnail: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def __repr__(self) -> str:
        return f"<Competitor(id={self.id}, ml_item_id='{self.ml_item_id}', title='{(self.title or '')[:30]}')>"
