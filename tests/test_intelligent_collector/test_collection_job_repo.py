"""
tests/test_intelligent_collector/test_collection_job_repo.py

Testes unitários do CollectionJobRepository.

Feature: intelligent-collector
Tests: 21.1 – 21.8
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy.ext.asyncio import AsyncSession
from models.operational import CollectionJob, CollectionLog
from repositories.collection_job import CollectionJobRepository
from collector.metrics import MetricsCollector


# ─────────────────────────────────────────────────────────────────────────────
# 21.1 — create_job -> status pending, immediate commit, hash generated
# Validates: Requirements 8.1
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_job_pending_commit() -> None:
    """
    create_job deve salvar o job como pending e realizar o commit.
    Validates: Requirements 8.1
    """
    session = AsyncMock(spec=AsyncSession)
    repo = CollectionJobRepository(session)
    
    job = await repo.create_job(job_type="full", source="test_runner")
    
    assert job.status == "pending"
    assert job.job_type == "full"
    assert job.source == "test_runner"
    assert len(job.hash) == 64
    
    session.add.assert_called_once_with(job)
    session.commit.assert_called_once()
    session.refresh.assert_called_once_with(job)


# ─────────────────────────────────────────────────────────────────────────────
# 21.2 — set_running -> status running, started_at set
# Validates: Requirements 8.2
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_set_running_updates_status() -> None:
    """
    set_running deve mudar o status para running e registrar a hora de início.
    Validates: Requirements 8.2
    """
    session = AsyncMock(spec=AsyncSession)
    repo = CollectionJobRepository(session)
    
    job = CollectionJob(id=1, status="pending", job_type="full")
    updated_job = await repo.set_running(job)
    
    assert updated_job.status == "running"
    assert updated_job.started_at is not None
    session.commit.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# 21.3 — set_done -> counters persisted, status done
# Validates: Requirements 6.5, 8.3
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_set_done_persists_counters() -> None:
    """
    set_done deve gravar métricas e transicionar status para done.
    Validates: Requirements 6.5, 8.3
    """
    session = AsyncMock(spec=AsyncSession)
    repo = CollectionJobRepository(session)
    
    job = CollectionJob(id=1, status="running", job_type="full")
    metrics = MetricsCollector(
        requests=10,
        products=5,
        errors=0,
        changes=2,
        events_generated=4,
        skipped=3,
        duration_seconds=12,
    )
    
    updated_job = await repo.set_done(job, metrics)
    
    assert updated_job.status == "done"
    assert updated_job.processed == 5
    assert updated_job.events_generated == 4
    assert updated_job.errors == 0
    assert updated_job.skipped == 3
    assert updated_job.duration_seconds == 12
    assert updated_job.finished_at is not None
    assert updated_job.extra_metrics["avg_seconds_per_page"] is None
    
    session.commit.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# 21.4 — set_partial -> status partial
# Validates: Requirements 8.4
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_set_partial_when_errors() -> None:
    """
    set_partial deve transicionar o job para partial.
    Validates: Requirements 8.4
    """
    session = AsyncMock(spec=AsyncSession)
    repo = CollectionJobRepository(session)
    
    job = CollectionJob(id=1, status="running", job_type="full")
    metrics = MetricsCollector(
        requests=10,
        products=5,
        errors=1,
        changes=2,
        events_generated=4,
        skipped=3,
        duration_seconds=12,
    )
    
    updated_job = await repo.set_partial(job, metrics)
    assert updated_job.status == "partial"
    session.commit.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# 21.5 — set_failed -> best-effort, does not re-raise if commit raises
# Validates: Requirements 6.6, 8.5
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_set_failed_silences_exceptions() -> None:
    """
    set_failed deve mudar status para failed, mas se o commit estourar,
    deve capturar a exceção e logar um aviso sem propagar.
    Validates: Requirements 6.6, 8.5
    """
    session = AsyncMock(spec=AsyncSession)
    session.commit.side_effect = Exception("DB Connection Lost")
    repo = CollectionJobRepository(session)
    
    job = CollectionJob(id=1, status="running", job_type="full")
    metrics = MetricsCollector(products=1, errors=2)
    
    with patch("repositories.collection_job.logger") as mock_logger:
        # should not raise
        await repo.set_failed(job, metrics)
        
    assert job.status == "failed"
    mock_logger.warning.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# 21.6 — write_log with status="error" and empty error_message -> raises ValueError
# Validates: Requirements 8.7
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_write_log_error_requires_message() -> None:
    """
    write_log com status='error' exige error_message.
    Validates: Requirements 8.7
    """
    session = AsyncMock(spec=AsyncSession)
    repo = CollectionJobRepository(session)
    
    with pytest.raises(ValueError, match="error_message não pode ser vazio"):
        await repo.write_log(
            job_id=1,
            entity_type="product",
            entity_id="MLB123",
            status="error",
            error_message=None,
        )


# ─────────────────────────────────────────────────────────────────────────────
# 21.7 — write_log with status="ok" sets error_message=None
# Validates: Requirements 8.7
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_write_log_ok_resets_message() -> None:
    """
    write_log com status='ok' ignora e zera qualquer error_message fornecido.
    Validates: Requirements 8.7
    """
    session = AsyncMock(spec=AsyncSession)
    repo = CollectionJobRepository(session)
    
    await repo.write_log(
        job_id=1,
        entity_type="product",
        entity_id="MLB123",
        status="ok",
        error_message="this should be cleared",
    )
    
    log: CollectionLog = session.add.call_args[0][0]
    assert log.status == "ok"
    assert log.error_message is None
    session.commit.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# 21.8 — find_running_job returns None when no running job exists
# Validates: Requirements 9.3
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_find_running_job_returns_none() -> None:
    """
    find_running_job retorna None quando não há jobs ativos no banco.
    Validates: Requirements 9.3
    """
    session = AsyncMock(spec=AsyncSession)
    mock_result = MagicMock()
    mock_scalars = MagicMock()
    mock_scalars.first.return_value = None
    mock_result.scalars.return_value = mock_scalars
    session.execute.return_value = mock_result
    
    repo = CollectionJobRepository(session)
    job = await repo.find_running_job("full")
    
    assert job is None
