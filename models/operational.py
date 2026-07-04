"""
models/operational.py

Tabelas operacionais do sistema de coleta.

Não são particionadas — crescimento é proporcional ao número de jobs
executados, não ao número de produtos monitorados.

collection_jobs : rastreia cada execução do scheduler (job-level)
collection_logs : rastreia cada item processado dentro de um job (item-level)
insights        : resultados agregados derivados dos eventos (leitura otimizada)
"""

from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import (
    BigInteger, String, DateTime, Integer, Text,
    ForeignKey, Index, CheckConstraint, Numeric,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from models.base import Base


# ─────────────────────────────────────────────────────────────────────────────
# collection_jobs
# ─────────────────────────────────────────────────────────────────────────────

class CollectionJob(Base):
    """
    Rastreia cada execução do scheduler.

    Uma execução de "varredura completa" é um job.
    Cada produto/seller dentro dessa varredura é um collection_log.

    status lifecycle: pending → running → done | failed | partial
    """

    __tablename__ = "collection_jobs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    job_type: Mapped[str] = mapped_column(String(60), nullable=False)
    # "pending" | "running" | "done" | "failed" | "partial"
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")

    total_items: Mapped[int | None] = mapped_column(Integer, nullable=True)
    processed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    events_generated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    errors: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Duração em segundos — calculada ao fechar o job
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)

    source: Mapped[str] = mapped_column(String(50), nullable=False, default="scheduler")
    hash: Mapped[str] = mapped_column(String(64), nullable=False)
    extra_metrics: Mapped[dict | None] = mapped_column(JSONB, nullable=True, default=None)

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

    logs = relationship("CollectionLog", back_populates="job", lazy="noload")

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','running','done','failed','partial')",
            name="ck_collection_jobs_status",
        ),
        CheckConstraint("processed >= 0", name="ck_collection_jobs_processed"),
        CheckConstraint("errors >= 0", name="ck_collection_jobs_errors"),
        Index("ix_collection_jobs_status", "status"),
        Index("ix_collection_jobs_type", "job_type"),
        Index("ix_collection_jobs_started_at", "started_at"),
    )

    def __repr__(self) -> str:
        return f"<CollectionJob(id={self.id}, type='{self.job_type}', status='{self.status}')>"


# ─────────────────────────────────────────────────────────────────────────────
# collection_logs
# ─────────────────────────────────────────────────────────────────────────────

class CollectionLog(Base):
    """
    Log de cada item individual processado por um CollectionJob.

    Permite rastrear:
    - Quais produtos tiveram eventos gerados vs. ignorados
    - Quais falharam e o motivo (error_message)
    - Quantos eventos foram gerados por item

    status: "ok" | "skipped" | "error"
    """

    __tablename__ = "collection_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("collection_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )

    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(100), nullable=False)

    # "ok" | "skipped" (nenhuma mudança detectada) | "error"
    status: Mapped[str] = mapped_column(String(30), nullable=False)

    # Quantos eventos foram inseridos nas tabelas de eventos para este item
    events_generated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    source: Mapped[str] = mapped_column(String(50), nullable=False, default="scheduler")
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

    job = relationship("CollectionJob", back_populates="logs", lazy="noload")

    __table_args__ = (
        CheckConstraint(
            "status IN ('ok','skipped','error')",
            name="ck_collection_logs_status",
        ),
        CheckConstraint(
            "entity_type IN ('product','seller','review','question','ranking')",
            name="ck_collection_logs_entity_type",
        ),
        Index("ix_collection_logs_job_id", "job_id"),
        Index("ix_collection_logs_status", "status"),
        Index("ix_collection_logs_entity", "entity_id", "entity_type"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# insights
# ─────────────────────────────────────────────────────────────────────────────

class Insight(Base):
    """
    Resultados processados derivados dos eventos.

    Tabela de LEITURA otimizada — armazena agregações pré-computadas
    para evitar queries analíticas pesadas em tempo real.

    Exemplos de insight_type:
      "price_trend_7d"    → tendência de preço nos últimos 7 dias (%)
      "price_trend_30d"   → tendência de preço nos últimos 30 dias (%)
      "stock_risk"        → risco de ruptura de estoque (0.0 a 1.0)
      "review_velocity"   → reviews por semana no período recente
      "ranking_stability" → desvio padrão da posição (menor = mais estável)
      "competition_index" → score competitivo derivado de múltiplas métricas

    valid_until: TTL do insight — insights expirados devem ser recomputados.
    metadata   : JSONB para dados extras sem schema fixo (ex: séries numéricas,
                 contagens intermediárias usadas no cálculo).
    """

    __tablename__ = "insights"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    product_id: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
    )
    insight_type: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[Decimal | None] = mapped_column(Numeric(15, 4), nullable=True)

    # Metadados sem schema fixo — série temporal, contagens, breakdowns
    # Nota: "metadata" é reservado pelo SQLAlchemy — usamos "extra_data" no ORM,
    # mapeado para a coluna "metadata" no banco via Column(name=...)
    extra_data: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)

    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    valid_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    source: Mapped[str] = mapped_column(String(50), nullable=False, default="computed")
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

    product = relationship("Product", back_populates="insights", lazy="noload")

    __table_args__ = (
        # Um produto pode ter vários tipos de insight, mas só um ativo por tipo
        Index(
            "ix_insights_product_type_computed",
            "product_id", "insight_type", "computed_at",
        ),
        Index("ix_insights_valid_until", "valid_until"),
        Index("ix_insights_hash", "hash"),
    )
