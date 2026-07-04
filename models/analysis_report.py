"""models/analysis_report.py — Relatórios do AI Analyst persistidos."""

from datetime import datetime, timezone
from sqlalchemy import BigInteger, String, DateTime, Text, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from models.base import Base


class AnalysisReport(Base):
    """Relatório completo gerado pelo AI Analyst.

    Armazena o relatório completo em JSONB para consulta futura
    e comparação histórica.
    """

    __tablename__ = "analysis_reports"

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

    # Relatório completo em JSONB
    report: Mapped[dict] = mapped_column(JSONB, nullable=False)

    # Metadados
    source: Mapped[str] = mapped_column(String(50), nullable=False, default="ai_analyst")
    hash: Mapped[str] = mapped_column(String(64), nullable=False)

    generated_at: Mapped[datetime] = mapped_column(
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
        Index("ix_analysis_reports_product", "product_id", "generated_at"),
    )

    def __repr__(self) -> str:
        return f"<AnalysisReport(product='{self.product_id}', generated_at={self.generated_at})>"
