import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column
from models.base import Base

class RawPayload(Base):
    __tablename__ = "raw_payloads"
    __table_args__ = (
        # Partitioned table in PostgreSQL 17
        {"postgresql_partition_by": "RANGE (captured_at)"},
    )

    # Composite primary key of UUID and captured_at
    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid.uuid4)
    resource_type: Mapped[str] = mapped_column(String(50), nullable=False)  # e.g., "product", "seller", "category"
    resource_id: Mapped[str] = mapped_column(String(100), nullable=False)  # e.g., "MLB123456789", "12345678"
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime, primary_key=True, default=lambda: datetime.now(timezone.utc))

    def __repr__(self) -> str:
        return f"<RawPayload(resource_type='{self.resource_type}', resource_id='{self.resource_id}', captured_at={self.captured_at})>"
