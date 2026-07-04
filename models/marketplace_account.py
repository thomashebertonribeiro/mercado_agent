"""
models/marketplace_account.py

Conta de marketplace conectada (preparado para multi-marketplace).

Atualmente suporta apenas Mercado Livre, mas a estrutura permite
adicionar Amazon, Shopee, Magalu, AliExpress no futuro.
"""

from datetime import datetime, timezone
from sqlalchemy import BigInteger, String, DateTime, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column
from models.base import Base


class MarketplaceAccount(Base):
    __tablename__ = "marketplace_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    marketplace: Mapped[str] = mapped_column(String(50), nullable=False, default="mercadolivre")
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    nickname: Mapped[str] = mapped_column(String(200), nullable=True)
    country: Mapped[str | None] = mapped_column(String(10), nullable=True)
    access_token: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    scope: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def __repr__(self) -> str:
        return f"<MarketplaceAccount(marketplace='{self.marketplace}', user_id={self.user_id})>"
