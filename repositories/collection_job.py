"""
repositories/collection_job.py

Repositório para CollectionJob e CollectionLog.

Responsabilidades:
  - Criar e persistir jobs de coleta
  - Atualizar status do job ao longo de seu ciclo de vida
  - Gravar logs individuais por item processado
  - Verificar se já existe um job em execução (prevenção de duplicatas)

Ciclo de vida do status:
  pending → running → done | partial | failed

Feature: intelligent-collector
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from collector.metrics import MetricsCollector
from models.operational import CollectionJob, CollectionLog
from repositories.base import BaseRepository
from utils.logger import logger


def _make_hash(*parts: str) -> str:
    """SHA-256 de múltiplas strings concatenadas."""
    raw = "".join(parts)
    return hashlib.sha256(raw.encode()).hexdigest()


class CollectionJobRepository(BaseRepository[CollectionJob]):
    """Repositório para gerenciar jobs e logs de coleta."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(CollectionJob, session)

    # ------------------------------------------------------------------ #
    # Lifecycle                                                            #
    # ------------------------------------------------------------------ #

    async def create_job(
        self,
        job_type: str,
        source: str = "scheduler",
        total_items: Optional[int] = None,
    ) -> CollectionJob:
        """
        Cria um novo job com status 'pending' e persiste imediatamente.

        hash: SHA-256 de (job_type + timestamp ISO) para unicidade.
        """
        now = datetime.now(timezone.utc)
        job = CollectionJob(
            job_type=job_type,
            status="pending",
            source=source,
            total_items=total_items,
            hash=_make_hash(job_type, now.isoformat()),
            created_at=now,
            updated_at=now,
        )
        self.session.add(job)
        await self.session.commit()
        await self.session.refresh(job)
        logger.info("Job criado", job_id=job.id, job_type=job_type, status="pending")
        return job

    async def set_running(self, job: CollectionJob) -> CollectionJob:
        """Transiciona job para 'running', registra started_at."""
        job.status = "running"
        job.started_at = datetime.now(timezone.utc)
        job.updated_at = datetime.now(timezone.utc)
        await self.session.commit()
        return job

    async def set_done(
        self, job: CollectionJob, metrics: MetricsCollector
    ) -> CollectionJob:
        """
        Persiste todos os contadores + extra_metrics e transiciona para 'done'.

        Ordem garantida: counters first, status last (R6.5).
        """
        return await self._close_job(job, metrics, status="done")

    async def set_partial(
        self, job: CollectionJob, metrics: MetricsCollector
    ) -> CollectionJob:
        """Igual a set_done mas status='partial' (jobs com erros parciais)."""
        return await self._close_job(job, metrics, status="partial")

    async def set_failed(
        self, job: CollectionJob, metrics: MetricsCollector
    ) -> CollectionJob:
        """
        Best-effort: grava o que foi coletado até o momento de falha.
        Se o commit falhar, loga WARNING e não re-raise (R6.6).
        """
        try:
            return await self._close_job(job, metrics, status="failed")
        except Exception as exc:
            logger.warning(
                "Falha ao persistir job como failed — continuando sem re-raise",
                job_id=getattr(job, "id", None),
                error=str(exc),
            )
            return job

    # ------------------------------------------------------------------ #
    # Logs individuais por item                                           #
    # ------------------------------------------------------------------ #

    async def write_log(
        self,
        job_id: int,
        entity_type: str,
        entity_id: str,
        status: str,
        events_generated: int = 0,
        error_message: Optional[str] = None,
        source: str = "scheduler",
    ) -> CollectionLog:
        """
        Grava um CollectionLog para um único item processado.

        Regra de integridade:
          - status='error' DEVE ter error_message não-vazio (raises ValueError)
          - status!='error' → error_message forçado para None

        (R8.7)
        """
        if status == "error":
            if not error_message:
                raise ValueError(
                    "error_message não pode ser vazio quando status='error'. "
                    f"entity_id={entity_id}"
                )
        else:
            error_message = None

        now = datetime.now(timezone.utc)
        log = CollectionLog(
            job_id=job_id,
            entity_type=entity_type,
            entity_id=str(entity_id),
            status=status,
            events_generated=events_generated,
            error_message=error_message,
            source=source,
            hash=_make_hash(str(job_id), entity_id, status, now.isoformat()),
            created_at=now,
            updated_at=now,
        )
        self.session.add(log)
        await self.session.commit()
        return log

    # ------------------------------------------------------------------ #
    # Queries                                                             #
    # ------------------------------------------------------------------ #

    async def find_running_job(self, job_type: str) -> Optional[CollectionJob]:
        """
        Retorna o job em execução para job_type, ou None se não houver.
        Usado para evitar jobs duplicados simultâneos (R9.3).
        """
        query = select(CollectionJob).where(
            CollectionJob.job_type == job_type,
            CollectionJob.status == "running",
        )
        result = await self.session.execute(query)
        return result.scalars().first()

    # ------------------------------------------------------------------ #
    # Interno                                                             #
    # ------------------------------------------------------------------ #

    async def _close_job(
        self,
        job: CollectionJob,
        metrics: MetricsCollector,
        status: str,
    ) -> CollectionJob:
        """Escreve contadores antes de atualizar status (R6.5)."""
        now = datetime.now(timezone.utc)

        # 1. Contadores — escritos ANTES de status (garantia R6.5)
        job.processed = metrics.products + metrics.errors
        job.events_generated = metrics.events_generated
        job.errors = metrics.errors
        job.skipped = metrics.skipped
        job.duration_seconds = metrics.duration_seconds
        job.extra_metrics = metrics.to_extra_metrics()
        job.finished_at = now
        job.updated_at = now

        # 2. Status — transição final
        job.status = status

        await self.session.commit()
        logger.info(
            "Job fechado",
            job_id=job.id,
            status=status,
            processed=job.processed,
            errors=job.errors,
            events=job.events_generated,
            duration_seconds=job.duration_seconds,
        )
        return job
