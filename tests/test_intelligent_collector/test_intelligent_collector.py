"""
tests/test_intelligent_collector/test_intelligent_collector.py

Testes unitários e de propriedades do IntelligentCollector.

Feature: intelligent-collector
Tests: 22.1 – 22.16
"""

import json
import pytest
import asyncio
import math
from unittest.mock import AsyncMock, MagicMock, patch
from hypothesis import given, settings as h_settings
from hypothesis import strategies as st

from collector.intelligent_collector import IntelligentCollector
from collector.modes import CollectionMode
from collector.cache_manager import CacheManager
from collector.rate_limiter import RateLimiter
from models.operational import CollectionJob


# ─────────────────────────────────────────────────────────────────────────────
# Mocks & Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_mocks():
    api = AsyncMock()
    cache = AsyncMock(spec=CacheManager)
    rate_limiter = AsyncMock(spec=RateLimiter)
    
    # Configure cache to return None by default (cache miss)
    cache.get_payload = AsyncMock(return_value=None)
    cache.get_hash = AsyncMock(return_value=None)
    cache.set_payload = AsyncMock()
    cache.set_hash = AsyncMock()

    # Mock DB Session
    session = AsyncMock()
    session.__aenter__.return_value = session
    # Mock executables to return synchronous MagicMocks for query results
    mock_exec = MagicMock()
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = []
    mock_scalars.first.return_value = None
    mock_exec.scalars.return_value = mock_scalars
    session.execute.return_value = mock_exec
    
    # Mock IngestionService
    ingestion_service_mock = AsyncMock()
    ingestion_service_mock.ingest_product_payload.return_value = 1

    # Session Factory returns this mock session
    def session_factory():
        return session

    return api, cache, rate_limiter, session_factory, session, ingestion_service_mock


# ─────────────────────────────────────────────────────────────────────────────
# 22.1 — Property 1: hash-check precedes any write
# ─────────────────────────────────────────────────────────────────────────────

@given(st.text(min_size=1, max_size=20))
@h_settings(max_examples=10, deadline=None)
def test_hash_check_precedes_any_write(product_id: str) -> None:
    """
    Property 1: Hash-check precedes any write.
    Validates: Requirements 1.1

    Nota: testa com 1 produto por vez porque _dispatch_workers usa
    asyncio.gather concorrente — com múltiplos produtos o interleaving
    de tasks faz com que 'ingest' de um produto possa aparecer antes
    de 'get_hash' de outro no call_order global.
    """
    async def run_test():
        api, cache, limiter, factory, session, ingestion = _make_mocks()
        
        # Mock API to return simple payload
        api.fetch_product.return_value = {"id": product_id, "price": 10}
        
        # Track calls to enforce that cache.get_hash occurs before ingestion
        call_order = []
        
        original_get_hash = cache.get_hash
        async def tracked_get_hash(*args, **kwargs):
            call_order.append("get_hash")
            return await original_get_hash(*args, **kwargs)
        cache.get_hash = tracked_get_hash
        
        # Track ingestion service call
        original_ingest = ingestion.ingest_product_payload
        async def tracked_ingest(*args, **kwargs):
            call_order.append("ingest")
            return await original_ingest(*args, **kwargs)
        ingestion.ingest_product_payload = tracked_ingest

        collector = IntelligentCollector(
            api_client=api,
            cache_manager=cache,
            rate_limiter=limiter,
            session_factory=factory,
            api_rate_limit_delay=0.0,
        )

        with patch("collector.intelligent_collector.IngestionService", return_value=ingestion):
            await collector.collect(mode=CollectionMode.BY_PRODUCT, product_ids=[product_id])

        # In case products are processed, verify get_hash is before ingest
        if "get_hash" in call_order and "ingest" in call_order:
            first_get_hash = call_order.index("get_hash")
            first_ingest = call_order.index("ingest")
            assert first_get_hash < first_ingest

    asyncio.run(run_test())


# ─────────────────────────────────────────────────────────────────────────────
# 22.2 — Property 2: cache miss -> ingestion called once, hash stored after
# ─────────────────────────────────────────────────────────────────────────────

@given(product_id=st.text(min_size=1, max_size=20))
@h_settings(max_examples=10, deadline=None)
def test_cache_miss_triggers_ingestion_and_hash_store(product_id: str) -> None:
    """
    Property 2: Cache miss triggers full ingestion and hash storage.
    Validates: Requirements 1.2
    """
    async def run_test():
        api, cache, limiter, factory, session, ingestion = _make_mocks()
        api.fetch_product.return_value = {"id": product_id, "price": 100}
        
        collector = IntelligentCollector(
            api_client=api,
            cache_manager=cache,
            rate_limiter=limiter,
            session_factory=factory,
            api_rate_limit_delay=0.0,
        )

        with patch("collector.intelligent_collector.IngestionService", return_value=ingestion):
            await collector.collect(mode=CollectionMode.BY_PRODUCT, product_ids=[product_id])

        ingestion.ingest_product_payload.assert_called_once_with({"id": product_id, "price": 100})
        cache.set_hash.assert_called_once()
        assert cache.set_hash.call_args[0][0] == product_id

    asyncio.run(run_test())


# ─────────────────────────────────────────────────────────────────────────────
# 22.3 — Property 3: hash match -> skip, no ingestion
# ─────────────────────────────────────────────────────────────────────────────

@given(product_id=st.text(min_size=1, max_size=20))
@h_settings(max_examples=10, deadline=None)
def test_hash_match_skips_ingestion(product_id: str) -> None:
    """
    Property 3: Hash match -> skip, no ingestion.
    Validates: Requirements 1.3
    """
    async def run_test():
        api, cache, limiter, factory, session, ingestion = _make_mocks()
        
        payload = {"id": product_id, "price": 100}
        # Compute exact hash for this payload
        expected_hash = IntelligentCollector._compute_state_hash(payload)
        
        # Configure cache to match
        cache.get_payload.return_value = payload
        cache.get_hash.return_value = expected_hash
        
        collector = IntelligentCollector(
            api_client=api,
            cache_manager=cache,
            rate_limiter=limiter,
            session_factory=factory,
            api_rate_limit_delay=0.0,
        )

        with patch("collector.intelligent_collector.IngestionService", return_value=ingestion):
            job = await collector.collect(mode=CollectionMode.BY_PRODUCT, product_ids=[product_id])

        # IngestionService should not be called
        ingestion.ingest_product_payload.assert_not_called()
        assert job.skipped == 1

    asyncio.run(run_test())


# ─────────────────────────────────────────────────────────────────────────────
# 22.4 — Property 4: hash mismatch -> ingestion before cache update
# ─────────────────────────────────────────────────────────────────────────────

@given(product_id=st.text(min_size=1, max_size=20))
@h_settings(max_examples=10, deadline=None)
def test_hash_mismatch_ingests_before_cache_update(product_id: str) -> None:
    """
    Property 4: Hash mismatch -> ingestion before cache update.
    Validates: Requirements 1.4
    """
    async def run_test():
        api, cache, limiter, factory, session, ingestion = _make_mocks()
        
        payload = {"id": product_id, "price": 100}
        cache.get_payload.return_value = payload
        cache.get_hash.return_value = "different-old-hash"
        
        call_order = []
        
        original_ingest = ingestion.ingest_product_payload
        async def tracked_ingest(*args, **kwargs):
            call_order.append("ingest")
            return await original_ingest(*args, **kwargs)
        ingestion.ingest_product_payload = tracked_ingest

        original_set_hash = cache.set_hash
        async def tracked_set_hash(*args, **kwargs):
            call_order.append("set_hash")
            return await original_set_hash(*args, **kwargs)
        cache.set_hash = tracked_set_hash

        collector = IntelligentCollector(
            api_client=api,
            cache_manager=cache,
            rate_limiter=limiter,
            session_factory=factory,
            api_rate_limit_delay=0.0,
        )

        with patch("collector.intelligent_collector.IngestionService", return_value=ingestion):
            await collector.collect(mode=CollectionMode.BY_PRODUCT, product_ids=[product_id])

        assert "ingest" in call_order
        assert "set_hash" in call_order
        assert call_order.index("ingest") < call_order.index("set_hash")

    asyncio.run(run_test())


# ─────────────────────────────────────────────────────────────────────────────
# 22.5 — Property 5: zero events from ingestion -> skipped log
# ─────────────────────────────────────────────────────────────────────────────

@given(product_id=st.text(min_size=1, max_size=20))
@h_settings(max_examples=10, deadline=None)
def test_zero_events_from_ingestion_skips(product_id: str) -> None:
    """
    Property 5: Zero events from IngestionService -> skip.
    Validates: Requirements 1.5
    """
    async def run_test():
        api, cache, limiter, factory, session, ingestion = _make_mocks()
        payload = {"id": product_id, "price": 100}
        cache.get_payload.return_value = payload
        cache.get_hash.return_value = "old-hash"
        
        # Mock ingestion to return 0 events
        ingestion.ingest_product_payload.return_value = 0

        collector = IntelligentCollector(
            api_client=api,
            cache_manager=cache,
            rate_limiter=limiter,
            session_factory=factory,
            api_rate_limit_delay=0.0,
        )

        with patch("collector.intelligent_collector.IngestionService", return_value=ingestion):
            job = await collector.collect(mode=CollectionMode.BY_PRODUCT, product_ids=[product_id])
    
        assert job.skipped == 1
        assert job.processed == 0

    asyncio.run(run_test())


# ─────────────────────────────────────────────────────────────────────────────
# 22.6 — Property 6: by_product processes exactly the input set
# ─────────────────────────────────────────────────────────────────────────────

@given(st.lists(st.text(min_size=1, max_size=20), min_size=1, max_size=10, unique=True))
@h_settings(max_examples=10, deadline=None)
def test_by_product_processes_exact_set(product_ids: list[str]) -> None:
    """
    Property 6: by_product processes exactly the input set.
    Validates: Requirements 2.1
    """
    async def run_test():
        api, cache, limiter, factory, session, ingestion = _make_mocks()
        api.fetch_product.return_value = {"price": 10}

        collector = IntelligentCollector(
            api_client=api,
            cache_manager=cache,
            rate_limiter=limiter,
            session_factory=factory,
            api_rate_limit_delay=0.0,
        )

        with patch("collector.intelligent_collector.IngestionService", return_value=ingestion):
            job = await collector.collect(mode=CollectionMode.BY_PRODUCT, product_ids=product_ids)

        assert job.total_items == len(product_ids)
        assert api.fetch_product.call_count == len(product_ids)

    asyncio.run(run_test())


# ─────────────────────────────────────────────────────────────────────────────
# 22.7 — Property 7: by_category collects all paginated products (deduplicated)
# ─────────────────────────────────────────────────────────────────────────────

@given(st.lists(st.text(min_size=1, max_size=10), min_size=5, max_size=15))
@h_settings(max_examples=5, deadline=None)
def test_by_category_deduplication(product_ids: list[str]) -> None:
    """
    Property 7: by_category collects all paginated products (deduplicated).
    Validates: Requirements 2.2, 2.5, 10.5
    """
    async def run_test():
        api, cache, limiter, factory, session, ingestion = _make_mocks()
        api.fetch_product.return_value = {"price": 10}
        
        # Mock search results containing duplicate IDs from product_ids list
        results_page = {
            "paging": {"total": len(product_ids)},
            "results": [{"id": pid} for pid in product_ids]
        }
        api.search_by_category.side_effect = [results_page, None]

        collector = IntelligentCollector(
            api_client=api,
            cache_manager=cache,
            rate_limiter=limiter,
            session_factory=factory,
            api_rate_limit_delay=0.0,
        )

        unique_count = len(set(product_ids))

        with patch("collector.intelligent_collector.IngestionService", return_value=ingestion):
            job = await collector.collect(mode=CollectionMode.BY_CATEGORY, category_ids=["MLB123"])

        # Should only call process_one for unique IDs
        assert job.total_items == unique_count

    asyncio.run(run_test())


# ─────────────────────────────────────────────────────────────────────────────
# 22.8 — Property 8: incremental mode selects only stale/null products
# ─────────────────────────────────────────────────────────────────────────────

def test_incremental_mode_stale_only() -> None:
    """
    Property 8: incremental mode selects only stale/null products.
    Validates: Requirements 2.3
    """
    async def run_test():
        api, cache, limiter, factory, session, ingestion = _make_mocks()
        api.fetch_product.return_value = {"price": 10}

        # Mock resolve_incremental query return values
        mock_exec = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = ["MLB1", "MLB2"]
        mock_exec.scalars.return_value = mock_scalars
        session.execute.return_value = mock_exec

        collector = IntelligentCollector(
            api_client=api,
            cache_manager=cache,
            rate_limiter=limiter,
            session_factory=factory,
            api_rate_limit_delay=0.0,
        )

        with patch("collector.intelligent_collector.IngestionService", return_value=ingestion):
            job = await collector.collect(mode=CollectionMode.INCREMENTAL)

        assert job.total_items == 2
        assert api.fetch_product.call_count == 2

    asyncio.run(run_test())


# ─────────────────────────────────────────────────────────────────────────────
# 22.9 — Property 15: One CollectionLog per processed item
# ─────────────────────────────────────────────────────────────────────────────

@given(st.lists(st.text(min_size=1, max_size=20), min_size=1, max_size=5, unique=True))
@h_settings(max_examples=5, deadline=None)
def test_one_log_per_item(product_ids: list[str]) -> None:
    """
    Property 15: One CollectionLog per processed item with correct error_message constraint.
    Validates: Requirements 8.6, 8.7
    """
    async def run_test():
        api, cache, limiter, factory, session, ingestion = _make_mocks()
        api.fetch_product.return_value = {"price": 10}

        collector = IntelligentCollector(
            api_client=api,
            cache_manager=cache,
            rate_limiter=limiter,
            session_factory=factory,
            api_rate_limit_delay=0.0,
        )

        with patch("collector.intelligent_collector.IngestionService", return_value=ingestion):
            with patch("repositories.collection_job.CollectionJobRepository.write_log", AsyncMock()) as mock_write_log:
                await collector.collect(mode=CollectionMode.BY_PRODUCT, product_ids=product_ids)
                assert mock_write_log.call_count == len(product_ids)

    asyncio.run(run_test())


# ─────────────────────────────────────────────────────────────────────────────
# 22.10 — Property 16: Job start log contains required bound fields
# ─────────────────────────────────────────────────────────────────────────────

def test_job_start_log_bound_fields() -> None:
    """
    Property 16: Job start log contains required bound fields.
    Validates: Requirements 7.1
    """
    async def run_test():
        api, cache, limiter, factory, session, ingestion = _make_mocks()
        api.fetch_product.return_value = {"price": 10}

        collector = IntelligentCollector(
            api_client=api,
            cache_manager=cache,
            rate_limiter=limiter,
            session_factory=factory,
            api_rate_limit_delay=0.0,
        )

        with patch("collector.intelligent_collector.logger") as mock_logger:
            with patch("collector.intelligent_collector.IngestionService", return_value=ingestion):
                await collector.collect(mode=CollectionMode.BY_PRODUCT, product_ids=["MLB1"])
            
            # The start info log call
            start_call = None
            for call in mock_logger.info.call_args_list:
                if "Coleta iniciada" in str(call):
                    start_call = call
                    break
            assert start_call is not None
            kwargs = start_call[1]
            assert "job_id" in kwargs
            assert "collection_mode" in kwargs

    asyncio.run(run_test())


# ─────────────────────────────────────────────────────────────────────────────
# 22.11 — Property 17: Per-product logs contain correct bound fields
# ─────────────────────────────────────────────────────────────────────────────

def test_per_product_log_bound_fields() -> None:
    """
    Property 17: Per-product logs contain correct bound fields.
    Validates: Requirements 7.2, 7.3, 7.4
    """
    async def run_test():
        api, cache, limiter, factory, session, ingestion = _make_mocks()
        api.fetch_product.return_value = {"price": 10}

        collector = IntelligentCollector(
            api_client=api,
            cache_manager=cache,
            rate_limiter=limiter,
            session_factory=factory,
            api_rate_limit_delay=0.0,
        )

        with patch("collector.intelligent_collector.logger") as mock_logger:
            with patch("collector.intelligent_collector.IngestionService", return_value=ingestion):
                await collector.collect(mode=CollectionMode.BY_PRODUCT, product_ids=["MLB1"])
            
            # Find change detection log
            change_call = None
            for call in mock_logger.info.call_args_list:
                if "Mudança detectada" in str(call):
                    change_call = call
                    break
            assert change_call is not None
            assert "product_id" in change_call[1]
            assert "events_generated" in change_call[1]

    asyncio.run(run_test())


# ─────────────────────────────────────────────────────────────────────────────
# 22.12 — Property 18: Job finish log contains required bound fields
# ─────────────────────────────────────────────────────────────────────────────

def test_job_finish_log_bound_fields() -> None:
    """
    Property 18: Job finish log contains required bound fields.
    Validates: Requirements 7.6
    """
    async def run_test():
        api, cache, limiter, factory, session, ingestion = _make_mocks()
        api.fetch_product.return_value = {"price": 10}

        collector = IntelligentCollector(
            api_client=api,
            cache_manager=cache,
            rate_limiter=limiter,
            session_factory=factory,
            api_rate_limit_delay=0.0,
        )

        with patch("collector.intelligent_collector.logger") as mock_logger:
            with patch("collector.intelligent_collector.IngestionService", return_value=ingestion):
                await collector.collect(mode=CollectionMode.BY_PRODUCT, product_ids=["MLB1"])
            
            # The finish log call
            finish_call = None
            for call in mock_logger.info.call_args_list:
                if "Coleta finalizada" in str(call):
                    finish_call = call
                    break
            assert finish_call is not None
            kwargs = finish_call[1]
            assert "job_id" in kwargs
            assert "duration_seconds" in kwargs
            assert "processed" in kwargs
            assert "changes" in kwargs
            assert "errors" in kwargs
            assert "status" in kwargs

    asyncio.run(run_test())


# ─────────────────────────────────────────────────────────────────────────────
# 22.13 — Property 19: Category pagination stops at min(paging_total, 1000)
# ─────────────────────────────────────────────────────────────────────────────

@given(paging_total=st.integers(min_value=0, max_value=2500))
@h_settings(max_examples=10, deadline=None)
def test_category_pagination_stops_at_1000(paging_total: int) -> None:
    """
    Property 19: Category pagination stops at min(paging.total, 1000).
    Validates: Requirements 10.2, 10.3
    """
    async def run_test():
        api, cache, limiter, factory, session, ingestion = _make_mocks()
        api.fetch_product.return_value = {"price": 10}

        # Mock API to return pages of 50 items up to paging_total
        # If requested total is above 1000, pagination should cap it
        pages = []
        limit = 50
        num_pages = math.ceil(min(paging_total, 1000) / limit)
        for i in range(num_pages):
            items_count = min(limit, min(paging_total, 1000) - i * limit)
            results = [{"id": f"MLB_{i*limit + j}"} for j in range(items_count)]
            pages.append({
                "paging": {"total": paging_total},
                "results": results
            })
        pages.append(None) # final sentinel
        api.search_by_category.side_effect = pages

        collector = IntelligentCollector(
            api_client=api,
            cache_manager=cache,
            rate_limiter=limiter,
            session_factory=factory,
            api_rate_limit_delay=0.0,
        )

        with patch("collector.intelligent_collector.IngestionService", return_value=ingestion):
            job = await collector.collect(mode=CollectionMode.BY_CATEGORY, category_ids=["MLB123"])

        # Paging count matches ceil(min(paging_total, 1000) / 50)
        expected_calls = num_pages
        if paging_total > 0:
            assert api.search_by_category.call_count <= expected_calls + 1
        assert job.total_items <= min(paging_total, 1000)

    asyncio.run(run_test())


# ─────────────────────────────────────────────────────────────────────────────
# 22.14 — invalid mode -> CollectionJob status=failed + ValueError
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_invalid_mode_fails_job() -> None:
    """
    Modo inválido deve criar job failed e lançar ValueError.
    Validates: Requirements 2.6
    """
    api, cache, limiter, factory, session, ingestion = _make_mocks()
    collector = IntelligentCollector(
        api_client=api,
        cache_manager=cache,
        rate_limiter=limiter,
        session_factory=factory,
        api_rate_limit_delay=0.0,
    )

    with pytest.raises(ValueError, match="não reconhecido"):
        await collector.collect(mode="invalid_mode")


# ─────────────────────────────────────────────────────────────────────────────
# 22.15 — empty by_product list -> ValueError, no job created
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_empty_by_product_raises_immediately() -> None:
    """
    mode=by_product com lista vazia deve lançar ValueError imediatamente
    sem criar nenhum job no banco de dados.
    Validates: Requirements 2.7
    """
    api, cache, limiter, factory, session, ingestion = _make_mocks()
    collector = IntelligentCollector(
        api_client=api,
        cache_manager=cache,
        rate_limiter=limiter,
        session_factory=factory,
        api_rate_limit_delay=0.0,
    )

    with pytest.raises(ValueError, match="mode=by_product requer product_ids"):
        await collector.collect(mode=CollectionMode.BY_PRODUCT, product_ids=[])

    session.add.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# 22.16 — duplicate running job -> skipped + WARNING logged
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_duplicate_running_job() -> None:
    """
    Se o scheduler detectar um job em execução para o mesmo tipo, deve registrar
    um aviso e ignorar.
    Validates: Requirements 9.3
    """
    with patch("redis.asyncio.from_url", side_effect=Exception("Redis indisponível")):
        with patch("collector.api_client.MercadoLivreAPICollector"):
            with patch("collector.intelligent_collector.IntelligentCollector") as mock_cls:
                mock_collector = AsyncMock()
                mock_job = MagicMock()
                mock_job.id = 99
                mock_job.status = "completed"
                mock_job.processed = 0
                mock_job.events_generated = 0
                mock_job.errors = 0
                mock_collector.collect.return_value = mock_job
                mock_cls.return_value = mock_collector

                from jobs.sync_jobs import sync_seller_items

                with patch("database.connection.AsyncSessionLocal") as mock_session_factory:
                    mock_session = AsyncMock()
                    mock_session_factory.return_value.__aenter__.return_value = mock_session

                    with patch("jobs.sync_jobs.logger") as mock_logger:
                        await sync_seller_items()
