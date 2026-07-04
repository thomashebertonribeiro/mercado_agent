from datetime import datetime, timezone
from sqlalchemy import BigInteger, String, DateTime, Integer
from sqlalchemy.orm import Mapped, mapped_column
from models.base import Base

class MLAccount(Base):
    """Represents a connected Mercado Livre account with stored OAuth credentials."""
    
    __tablename__ = "ml_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nickname: Mapped[str] = mapped_column(String(100), nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    country: Mapped[str] = mapped_column(String(10), nullable=True)  # e.g., "BR", "AR", "MX"
    access_token: Mapped[str] = mapped_column(String(500), nullable=False)
    refresh_token: Mapped[str] = mapped_column(String(500), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)  # UTC timestamp of expiration
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    def __repr__(self) -> str:
        return f"<MLAccount(id={self.id}, nickname='{self.nickname}', user_id={self.user_id})>"
