# Implementation Plan: Intelligent Collector

## Overview

Implements the full Intelligent Collector subsystem — change-detection pipeline, caching, rate limiting, metrics, concurrency control, and scheduler integration — by building components in strict dependency order: schema first, then models, then repository, then pure collector modules, then the orchestrator, then the scheduler rewrite, then the test suite.

The implementation language is **Python 3.12+** using the existing stack: FastAPI, SQLAlchemy 2.x async, Redis, APScheduler, Loguru, httpx.

---

## Tasks

- [x] 1. Add `hypothesis` to dev dependencies in `pyproject.toml`
  - Add `"hypothesis>=6.100.0"` to the `[dependency-groups] dev` list in `pyproject.toml`
  - Hypothesis is the PBT library used throughout the test suite (design: Testing Strategy)
  - _Requirements: none (tooling only)_

- [x] 2. Write Alembic migration `0004_intelligent_collector`
  - Create `database/migrations/versions/0004_intelligent_collector.py`
  - `upgrade()`: add `products.last_checked_at` (DateTime with timezone, nullable) with index `ix_products_last_checked_at`; add `collection_jobs.extra_metrics` (JSONB, nullable)
  - `downgrade()`: drop both in reverse order
  - Set `revision = "0004"`, `down_revision = "0003"` to chain on the existing 0003 migration
  - _Requirements: 6.7, Data Models section_

- [x] 3. Add `last_checked_at` column to `models/product.py`
  - Add `last_checked_at: Mapped[datetime | None]` mapped column with `DateTime(timezone=True)`, `nullable=True`, `default=None`, and `index=True`
  - Import `datetime` from `datetime` if not already present
  - Do not change any other field or relationship
  - _Requirements: 2.3 (incremental query), Data Models section_

- [x] 4. Add `extra_metrics` column to `models/operational.py`
  - Add `extra_metrics: Mapped[dict | None]` mapped column using `JSONB` (already imported from `sqlalchemy.dialects.postgresql`), `nullable=True`, `default=None` on `CollectionJob`
  - Do not modify `CollectionLog` or `Insight`
  - _Requirements: 6.7_

- [x] 5. Create `collector/modes.py` — `CollectionMode` enum
  - Define `CollectionMode(str, Enum)` with four members: `BY_PRODUCT = "by_product"`, `BY_CATEGORY = "by_category"`, `INCREMENTAL = "incremental"`, `FULL = "full"`
  - No logic in this file — enum only
  - _Requirements: 2.1–2.4_

- [x] 6. Create `collector/cache_manager.py` — `CacheManager`
  - Implement `CacheManager.__init__(redis_client, hash_ttl_seconds=3600, payload_ttl_seconds=300)`
  - Implement `get_hash(product_id) -> Optional[str]`: returns stored hash or None; catches `RedisError`, logs WARNING with `product_id` and `error` fields, returns None
  - Implement `set_hash(product_id, state_hash)`: calls `redis.set(key, value, ex=ttl)` atomically; catches `RedisError`, logs WARNING, returns without raising
  - Implement `get_payload(product_id) -> Optional[Dict]`: on cache hit tries `json.loads`; if `JSONDecodeError`, deletes key, logs WARNING with `product_id`, returns None; on `RedisError`, logs WARNING, returns None
  - Implement `set_payload(product_id, payload)`: serialises with `json.dumps`, calls `redis.set` with EX; catches `RedisError`, logs WARNING
  - Implement private `_hash_key(pid)` and `_payload_key(pid)` helpers
  - Key prefixes: `collector:hash:` and `collector:payload:`
  - Use `from utils.logger import logger` for all log output
  - _Requirements: 5.1–5.7_

- [ ] 7. Create `collector/rate_limiter.py` — `RateLimiter`
  - Implement `RateLimiter.__init__(max_concurrency=5)`: creates `asyncio.Semaphore(max_concurrency)` and an `asyncio.Event` initialised as set (not paused)
  - Implement `acquire()`: awaits the pause event first, then acquires the semaphore
  - Implement `release()`: releases the semaphore
  - Implement `pause_for(seconds: float)`: clears the pause event, sleeps `seconds`, then sets the event; guards against stacking — if already paused, simply awaits rather than launching a concurrent sleep
  - Implement `is_paused` property: returns `not self._pause_event.is_set()`
  - _Requirements: 4.1–4.5_

- [~] 8. Create `collector/metrics.py` — `MetricsCollector`
  - Implement the `MetricsCollector` dataclass exactly as in the design: fields `requests`, `products`, `errors`, `changes`, `events_generated`, `skipped` (all int, default 0); `duration_seconds` (int, default 0); `memory_rss_start_mb` and `memory_rss_end_mb` (Optional[float]); private `_start_time` and `_page_durations`
  - Implement `start()`: records `time.monotonic()` and calls `_sample_rss_mb()` for start memory
  - Implement `finish()`: computes `int(monotonic() - _start_time)` for `duration_seconds`, samples end RSS
  - Implement `record_page_duration(seconds: float)`: appends to `_page_durations`
  - Implement `avg_seconds_per_page` property: returns `sum/len` or `None`
  - Implement `to_extra_metrics()`: returns dict with keys `avg_seconds_per_page`, `memory_rss_start_mb`, `memory_rss_end_mb` (None values included)
  - Implement `_sample_rss_mb()`: uses `psutil.Process().memory_info().rss`, converts to MB with `round(..., 2)`
  - _Requirements: 6.1–6.4_

- [~] 9. Create `repositories/collection_job.py` — `CollectionJobRepository`
  - Implement `CollectionJobRepository(BaseRepository[CollectionJob])` with `__init__(session: AsyncSession)` calling `super().__init__(CollectionJob, session)`
  - Implement `create_job(job_type, source="scheduler", total_items=None) -> CollectionJob`: creates with `status="pending"`, persists and commits; a `hash` field must be generated (SHA-256 of `job_type + str(datetime.now(timezone.utc))`)
  - Implement `set_running(job)`: sets `status="running"`, `started_at=datetime.now(timezone.utc)`, commits
  - Implement `set_done(job, metrics)`: writes all counters + `extra_metrics` + `duration_seconds`, sets `status="done"`, `finished_at=now`, commits in one transaction (counters before status, per R6.5)
  - Implement `set_partial(job, metrics)`: identical to `set_done` but `status="partial"`
  - Implement `set_failed(job, metrics)`: best-effort — writes partial counters and `status="failed"`, `finished_at=now`; if commit raises, logs WARNING and does not re-raise (R6.6)
  - Implement `write_log(job_id, entity_type, entity_id, status, events_generated=0, error_message=None, source="scheduler") -> CollectionLog`: enforces that `error_message` is non-empty when `status="error"` (raises `ValueError` otherwise); generates `hash`; commits immediately
  - Implement `find_running_job(job_type) -> Optional[CollectionJob]`: queries `WHERE job_type=:jt AND status='running'`
  - _Requirements: 8.1–8.7_


- [~] 10. Refactor `collector/api_client.py` — replace tenacity with manual retry loop
  - Remove the `@retry` tenacity decorator and all `tenacity` imports from `_make_request`
  - Rewrite `_make_request` as a manual exponential back-off loop accepting additional keyword args: `rate_limiter: Optional[RateLimiter] = None`, `max_retries: int = 5`, `backoff_base: float = 2.0`, `max_wait: float = 60.0`
  - Retry on: `httpx.RequestError` and HTTP 429 / 5xx; raise immediately on HTTP 404
  - For 429, parse `Retry-After` header via `parse_retry_after()`; if `rate_limiter` is provided call `rate_limiter.pause_for(wait)`, otherwise `asyncio.sleep(wait)`
  - For other retryable errors use `compute_backoff_wait(attempt, base, max_wait)` then `asyncio.sleep(wait)` / `rate_limiter.pause_for(wait)` as appropriate
  - After exhausting `max_retries`, re-raise the last exception
  - Add module-level pure function `compute_backoff_wait(attempt: int, base: float, max_wait: float) -> float`: returns `min(base ** attempt, max_wait)`; attempt is 1-indexed, no jitter
  - Add module-level pure function `parse_retry_after(header_value: str, now: Optional[datetime] = None) -> float`: handles integer string and HTTP-date formats; returns `0.0` for past dates; `now` defaults to `datetime.now(timezone.utc)`
  - Add method `search_by_category(category_id, offset=0, limit=50) -> Optional[Dict]`: calls `GET /sites/MLB/search?category={id}&offset={offset}&limit={limit}`; returns raw dict or None on 404
  - Remove `tenacity` from `pyproject.toml` dependencies only after verifying no other module imports it (keep it for now; just stop using it in this file)
  - _Requirements: 3.1–3.6, 10.1_

- [~] 11. Checkpoint — run existing tests
  - Ensure all tests pass, ask the user if questions arise.

- [~] 12. Create `collector/intelligent_collector.py` — `IntelligentCollector`
  - Implement `IntelligentCollector.__init__` accepting: `api_client`, `cache_manager`, `rate_limiter`, `session_factory=AsyncSessionLocal`, `max_concurrency=5`, `max_retries=5`, `backoff_base=2.0`, `max_wait=60.0`, `api_rate_limit_delay=0.5`
  - Implement `collect(mode, product_ids=None, category_ids=None, incremental_threshold_hours=6) -> CollectionJob`:
    - Pre-flight: raise `ValueError` without creating a job if `mode=by_product` and `product_ids` is empty/None (R2.7)
    - Pre-flight: if mode is an unrecognised string, create a job with `status=failed`, then raise `ValueError` (R2.6)
    - Create CollectionJob via `CollectionJobRepository.create_job()`
    - `set_running()`, resolve items, dispatch workers, then `set_done()` or `set_partial()` based on `metrics.errors`
    - Outer try/except sets job to `failed` and re-raises on unhandled exception (R8.5)
    - Emit structured log on start (INFO: `job_id`, `collection_mode`, `total_items`) and finish (INFO: `job_id`, `duration_seconds`, `processed`, `changes`, `errors`, `status`) (R7.1, R7.6)
  - Implement `_resolve_items(mode, product_ids, category_ids, incremental_threshold_hours, job_id, metrics) -> List[str]`:
    - `by_product`: validate and return list as-is
    - `by_category`: paginate `APIClient.search_by_category()` for each category_id; deduplicate with a set; truncate at 1000 items per category with INFO log if exceeded (R10.2–10.5); record page durations via `metrics.record_page_duration()`
    - `incremental`: query `SELECT id FROM products WHERE last_checked_at IS NULL OR last_checked_at < :threshold` (R2.3)
    - `full`: query all product IDs in batches of 500 to avoid loading all into memory (R2.4)
  - Implement `_dispatch_workers(product_ids, job, metrics, session)`: creates one `_process_one()` task per product ID wrapped in the semaphore; uses `asyncio.gather(*tasks, return_exceptions=True)` so failures do not cancel siblings
  - Implement `_process_one(product_id, job, metrics, session)`:
    - `RateLimiter.acquire()` → `asyncio.sleep(api_rate_limit_delay)` → `CacheManager.get_payload()` (or API call on miss) → compute state hash → `CacheManager.get_hash()` → branch on match/mismatch → `IngestionService.ingest_product_payload()` on mismatch → `CacheManager.set_hash()` after ingestion → `write_log()` → `_update_last_checked_at()` → `metrics` increments → `RateLimiter.release()` in `finally`
    - Catch `httpx.HTTPStatusError`: log ERROR with `http_status` field (R7.4); write CollectionLog `error`; `metrics.errors += 1`
    - Catch all other exceptions: log ERROR without `http_status` field (R7.5); write CollectionLog `error`; `metrics.errors += 1`
    - Emit DEBUG log on no change (R7.2); INFO log on change with `events_generated` (R7.3)
  - Implement `_update_last_checked_at(product_id, session)`: executes `UPDATE products SET last_checked_at = now WHERE id = :pid`
  - Implement `_compute_state_hash(payload) -> str`: extracts fields `price`, `original_price`, `available_quantity`, `sold_quantity`, `status`, `title`, `listing_type_id` from payload dict; serialises with `json.dumps(data, sort_keys=True)`; returns `hashlib.sha256(serialised.encode()).hexdigest()`
  - _Requirements: 1.1–1.5, 2.1–2.7, 3.1–3.6, 4.1–4.5, 7.1–7.6, 8.1–8.7, 10.1–10.6_

- [~] 13. Rewrite `scheduler/main.py` — integrate `IntelligentCollector`
  - Remove all Redis Stream logic (`enqueue_task`, `schedule_products_crawl`, `schedule_sellers_crawl`)
  - Implement `_build_collector() -> IntelligentCollector`: constructs `MercadoLivreAPICollector`, `CacheManager` (with Redis from `settings.REDIS_URL`), `RateLimiter`, and `IntelligentCollector` wired from settings (`COLLECTOR_MAX_CONCURRENCY`, `COLLECTOR_MAX_RETRIES`, `COLLECTOR_BACKOFF_BASE`, `COLLECTOR_MAX_WAIT`, `API_RATE_LIMIT_DELAY`)
  - Implement `run_full_collection()`: creates a DB session, calls `CollectionJobRepository.find_running_job("full")`; if found, logs WARNING with existing job's ID and returns (R9.3); otherwise calls `collector.collect(mode=CollectionMode.FULL)`; logs job summary on completion (R9.4)
  - Implement `run_incremental_collection()`: same pattern with `job_type="incremental"` and `mode=CollectionMode.INCREMENTAL` using `settings.COLLECTOR_INCREMENTAL_THRESHOLD_HOURS`
  - Implement `run_scheduler()`: registers `run_full_collection` with `hours=settings.SCHEDULER_FULL_INTERVAL_HOURS` and `run_incremental_collection` with `hours=settings.SCHEDULER_INCREMENTAL_INTERVAL_HOURS` on an `AsyncIOScheduler`; starts it and loops (R9.1, R9.2, R9.5)
  - _Requirements: 9.1–9.5_

- [~] 14. Add 9 new settings to `config/settings.py`
  - Add to `Settings` class (with these exact field names and defaults):
    - `SCHEDULER_FULL_INTERVAL_HOURS: int = 24`
    - `SCHEDULER_INCREMENTAL_INTERVAL_HOURS: int = 6`
    - `COLLECTOR_MAX_CONCURRENCY: int = 5`
    - `COLLECTOR_MAX_RETRIES: int = 5`
    - `COLLECTOR_BACKOFF_BASE: float = 2.0`
    - `COLLECTOR_MAX_WAIT: float = 60.0`
    - `COLLECTOR_INCREMENTAL_THRESHOLD_HOURS: int = 6`
    - `CACHE_HASH_TTL_SECONDS: int = 3600`
    - `CACHE_PAYLOAD_TTL_SECONDS: int = 300`
  - Keep all existing fields unchanged
  - _Requirements: 2.3, 3.1–3.2, 4.1–4.2, 5.1–5.2, 9.1–9.2_

- [~] 15. Checkpoint — run existing tests
  - Ensure all tests pass, ask the user if questions arise.


- [~] 16. Create `tests/test_intelligent_collector/__init__.py`
  - Empty file to make the directory a Python package
  - _Requirements: none (test scaffolding)_

- [ ] 17. Write `tests/test_intelligent_collector/test_cache_manager.py`
  - [ ]* 17.1 Write property test for `set_hash` / `get_hash` key format and TTL (Property 13)
    - **Property 13: Cache key format and TTL are always correct**
    - Use `@given(st.text(min_size=1, max_size=50))` for `product_id` and `st.text(min_size=1)` for hash
    - Assert `redis.set` was called with `f"collector:hash:{pid}"` and `ex=hash_ttl_seconds`
    - **Validates: Requirements 5.1, 5.5**
  - [ ]* 17.2 Write property test for `set_payload` / `get_payload` key format and TTL (Property 13, payload side)
    - **Property 13: Cache key format and TTL are always correct (payload)**
    - Use `@given(st.text(min_size=1), st.dictionaries(st.text(), st.integers()))` for pid and payload
    - Assert `redis.set` was called with `f"collector:payload:{pid}"` and `ex=payload_ttl_seconds`
    - **Validates: Requirements 5.2, 5.5**
  - [ ]* 17.3 Write example test: corrupted JSON in cache → key deleted + WARNING logged + None returned
    - Mock `redis.get` to return a non-JSON byte string
    - Assert key is deleted, WARNING logged with `product_id`, return value is None
    - **Validates: Requirements 5.4**
  - [ ]* 17.4 Write example test: Redis read error → WARNING logged + fallback None returned
    - Mock `redis.get` to raise `RedisError`
    - Assert WARNING logged, method returns None without raising
    - **Validates: Requirements 5.6**
  - [ ]* 17.5 Write example test: Redis write error → WARNING logged + no exception raised
    - Mock `redis.set` to raise `RedisError`
    - Assert WARNING logged, method returns normally
    - **Validates: Requirements 5.7**

- [ ] 18. Write `tests/test_intelligent_collector/test_rate_limiter.py`
  - [ ]* 18.1 Write property test: concurrency cap is never exceeded (Property 9)
    - **Property 9: Concurrency cap is never exceeded**
    - Use `@given(st.integers(min_value=1, max_value=10))` for `max_concurrency` and `st.integers(min_value=11, max_value=50)` for item count
    - Track simultaneously in-flight count using an atomic counter; assert peak never exceeds `max_concurrency`
    - **Validates: Requirements 4.2, 4.3, 4.4**
  - [ ]* 18.2 Write example test: 429 pause blocks new dispatches, in-flight requests complete
    - Call `acquire()` from multiple coroutines; call `pause_for(0.05)` from a 429 handler; assert that calls arriving after pause start block until pause ends
    - **Validates: Requirements 4.5**
  - [ ]* 18.3 Write example test: `is_paused` reflects pause state correctly
    - Assert `is_paused` is False initially, True during `pause_for()`, False after

- [ ] 19. Write `tests/test_intelligent_collector/test_metrics.py`
  - [ ]* 19.1 Write property test: `avg_seconds_per_page` equals arithmetic mean (Property 14)
    - **Property 14: avg_seconds_per_page equals arithmetic mean**
    - Use `@given(st.lists(st.floats(min_value=0.001, max_value=100.0), min_size=1, max_size=200))`
    - Assert `metrics.avg_seconds_per_page == sum(durations) / len(durations)` (within float tolerance)
    - **Validates: Requirements 6.3, 10.6**
  - [ ]* 19.2 Write example test: `avg_seconds_per_page` is None when no pages recorded
    - Assert `MetricsCollector().avg_seconds_per_page is None`
  - [ ]* 19.3 Write example test: `finish()` sets `duration_seconds` and end memory
    - Mock `psutil.Process.memory_info` to return controlled RSS; call `start()`, sleep a tick, call `finish()`
    - Assert `duration_seconds >= 0` and `memory_rss_end_mb` is not None
    - **Validates: Requirements 6.2, 6.4**
  - [ ]* 19.4 Write example test: `to_extra_metrics()` always contains all three keys
    - Assert keys `avg_seconds_per_page`, `memory_rss_start_mb`, `memory_rss_end_mb` are present even when values are None
    - **Validates: Requirements 6.7**

- [ ] 20. Write `tests/test_intelligent_collector/test_api_client.py`
  - [ ]* 20.1 Write property test: `compute_backoff_wait` returns exact `min(base**attempt, max_wait)` (Property 10)
    - **Property 10: Back-off formula is exact**
    - Use `@given(st.integers(1, 10), st.floats(1.0, 10.0), st.floats(1.0, 300.0))`
    - Assert `compute_backoff_wait(attempt, base, max_wait) == min(base ** attempt, max_wait)`
    - **Validates: Requirements 3.2**
  - [ ]* 20.2 Write property test: `parse_retry_after` integer string returns exact float (Property 11)
    - **Property 11: Retry-After integer parsing is exact**
    - Use `@given(st.integers(min_value=0, max_value=3600))`
    - Assert `parse_retry_after(str(n)) == float(n)`
    - **Validates: Requirements 3.3**
  - [ ]* 20.3 Write property test: `parse_retry_after` HTTP-date returns correct delta (Property 12)
    - **Property 12: Retry-After HTTP-date parsing returns correct delta**
    - Use `@given(st.timedeltas(min_value=timedelta(seconds=1), max_value=timedelta(hours=24)))` to build a future date
    - Assert result is within ±1 second of `delta.total_seconds()`; assert past dates return 0.0
    - **Validates: Requirements 3.4**
  - [ ]* 20.4 Write example test: HTTP 404 → no retry, raises immediately
    - Mock `httpx.AsyncClient.request` to return a 404; assert method raises `HTTPStatusError` on first attempt only
    - **Validates: Requirements 3.6**
  - [ ]* 20.5 Write example test: `search_by_category` returns None on 404
    - Mock a 404 response; assert `search_by_category()` returns None without raising
    - **Validates: Requirements 10.1**

- [ ] 21. Write `tests/test_intelligent_collector/test_collection_job_repo.py`
  - [ ]* 21.1 Write example test: `create_job` → status `pending`, immediate commit
    - Use an in-memory async SQLite session or fully mocked `AsyncSession`
    - Assert returned `CollectionJob.status == "pending"` and session was committed
    - **Validates: Requirements 8.1**
  - [ ]* 21.2 Write example test: `set_running` → status `running`, `started_at` set
    - **Validates: Requirements 8.2**
  - [ ]* 21.3 Write example test: `set_done` → counters persisted before status transitions to `done`
    - Verify DB state after call: counters match metrics, `status == "done"`, `finished_at` not None
    - **Validates: Requirements 6.5, 8.3**
  - [ ]* 21.4 Write example test: `set_partial` → status `partial` when errors > 0
    - **Validates: Requirements 8.4**
  - [ ]* 21.5 Write example test: `set_failed` → logs WARNING if commit fails, does not re-raise
    - Mock session.commit to raise; assert WARNING logged and no exception propagates
    - **Validates: Requirements 6.6, 8.5**
  - [ ]* 21.6 Write example test: `write_log` with `status="error"` and empty `error_message` → raises `ValueError`
    - **Validates: Requirements 8.7**
  - [ ]* 21.7 Write example test: `write_log` with `status="ok"` sets `error_message=None`
    - **Validates: Requirements 8.7**
  - [ ]* 21.8 Write example test: `find_running_job` returns None when no running job exists
    - **Validates: Requirements 9.3**


- [ ] 22. Write `tests/test_intelligent_collector/test_intelligent_collector.py`
  - All tests mock: `redis.asyncio.Redis` (AsyncMock), `httpx.AsyncClient.request` (AsyncMock), `AsyncSessionLocal`, `psutil.Process.memory_info`, and `asyncio.sleep`
  - [ ]* 22.1 Write property test: hash-check precedes any write for every product (Property 1)
    - **Property 1: Hash-check precedes any write**
    - Use `@given(st.lists(st.text(min_size=1, max_size=20), min_size=1, max_size=10))`
    - Capture call order via mock side effects; assert `get_hash()` called before any `session.commit()`
    - **Validates: Requirements 1.1**
  - [ ]* 22.2 Write property test: cache miss → ingestion called once, hash stored after (Property 2)
    - **Property 2: Cache miss triggers full ingestion and hash storage**
    - `@given(st.text(min_size=1))` for product_id; configure `get_hash` mock to return None
    - Assert `ingest_product_payload` called once; `set_hash` called once with non-empty string after ingestion
    - **Validates: Requirements 1.2**
  - [ ]* 22.3 Write property test: hash match → skip, no ingestion (Property 3)
    - **Property 3: Hash match → skip, no ingestion**
    - Configure `get_hash` to return hash that matches computed hash of mock payload
    - Assert `ingest_product_payload` NOT called; `last_checked_at` updated; `job.skipped` incremented; `CollectionLog.status == "skipped"`
    - **Validates: Requirements 1.3**
  - [ ]* 22.4 Write property test: hash mismatch → ingestion before cache update (Property 4)
    - **Property 4: Hash mismatch → ingestion before cache update**
    - Use call-order tracking; assert `ingest` called before `set_hash`; if ingest raises, `set_hash` must not have been called
    - **Validates: Requirements 1.4**
  - [ ]* 22.5 Write property test: zero events from ingestion → skipped log (Property 5)
    - **Property 5: Zero events from IngestionService → skip**
    - Mock `ingest_product_payload` to return 0 events; assert `job.skipped += 1` and `CollectionLog.status == "skipped"`
    - **Validates: Requirements 1.5**
  - [ ]* 22.6 Write property test: `by_product` processes exactly the input set (Property 6)
    - **Property 6: by_product processes exactly the input set**
    - `@given(st.lists(st.text(min_size=1, max_size=20), min_size=1, max_size=20, unique=True))`
    - Assert `_process_one` invoked for each ID in the input set — no more, no fewer
    - **Validates: Requirements 2.1**
  - [ ]* 22.7 Write property test: `by_category` deduplicates across pages (Property 7)
    - **Property 7: by_category collects all paginated products (deduplicated)**
    - Generate a multiset of product IDs spread across mock pages; assert `_process_one` called once per unique ID
    - **Validates: Requirements 2.2, 2.5, 10.5**
  - [ ]* 22.8 Write property test: incremental mode selects only stale/null products (Property 8)
    - **Property 8: incremental mode selects only stale/null products**
    - Generate a product set with mixed `last_checked_at` values (null, past-threshold, future-threshold)
    - Assert `_process_one` invoked only for null and past-threshold products
    - **Validates: Requirements 2.3**
  - [ ]* 22.9 Write property test: exactly one `CollectionLog` per processed item, error_message constraint (Property 15)
    - **Property 15: One CollectionLog per processed item with correct error_message constraint**
    - `@given(st.lists(st.text(min_size=1), min_size=1, max_size=20))`
    - Assert `write_log` call count equals item count; verify `error_message` constraints on each call
    - **Validates: Requirements 8.6, 8.7**
  - [ ]* 22.10 Write property test: job start log contains required bound fields (Property 16)
    - **Property 16: Job start log contains required bound fields**
    - Capture Loguru bound fields by patching logger; assert INFO log has `job_id`, `collection_mode`, `total_items`
    - **Validates: Requirements 7.1**
  - [ ]* 22.11 Write property test: per-product logs contain correct bound fields (Property 17)
    - **Property 17: Per-product logs contain correct bound fields**
    - Three sub-cases: no-change (DEBUG with `job_id`, `product_id`); change (INFO with `events_generated`); HTTP error (ERROR with `http_status`)
    - **Validates: Requirements 7.2, 7.3, 7.4**
  - [ ]* 22.12 Write property test: job finish log contains required bound fields (Property 18)
    - **Property 18: Job finish log contains required bound fields**
    - Assert INFO finish log has `job_id`, `duration_seconds`, `processed`, `changes`, `errors`, `status`
    - **Validates: Requirements 7.6**
  - [ ]* 22.13 Write property test: category pagination stops at `min(paging_total, 1000)` (Property 19)
    - **Property 19: Category pagination stops at min(paging.total, 1000)**
    - `@given(st.integers(min_value=0, max_value=5000))` for `paging_total`
    - Assert page request count equals `ceil(min(paging_total, 1000) / 50)` and total items discovered ≤ `min(paging_total, 1000)`
    - **Validates: Requirements 10.2, 10.3**
  - [ ]* 22.14 Write example test: invalid mode → CollectionJob status=failed + ValueError
    - Assert job is created and set to failed before ValueError propagates
    - **Validates: Requirements 2.6**
  - [ ]* 22.15 Write example test: empty `by_product` list → ValueError, no job created
    - Assert `create_job` is never called
    - **Validates: Requirements 2.7**
  - [ ]* 22.16 Write example test: duplicate running job → skipped + WARNING logged
    - Mock `find_running_job` to return an existing job; assert no new job created, WARNING emitted
    - **Validates: Requirements 9.3**

- [~] 23. Final checkpoint — run full test suite
  - Ensure all tests pass, ask the user if questions arise.

---

## Notes

- Tasks marked with `*` are optional and can be skipped for a faster MVP
- Dependency order is strict: migration (2) → models (3, 4) → enum (5) → pure modules (6, 7, 8) → repository (9) → api_client refactor (10) → orchestrator (12) → scheduler (13) → settings (14) → tests (16–22)
- Settings task (14) is placed after the scheduler rewrite (13) because the scheduler is the primary consumer; however, `IntelligentCollector` (12) reads settings at construction time, so settings must be added before running `12` in practice — the executor should apply task 14 alongside or just before task 12 if needed
- Property tests use `hypothesis.settings(max_examples=100)` minimum; tag format: `# Feature: intelligent-collector, Property {N}: {title}`
- No new external runtime dependencies are introduced; `hypothesis` is dev-only
- All test files use `pytest-asyncio` with `asyncio_mode = "auto"` (already set in `pyproject.toml`)
- Integration tests (real DB/Redis) are tagged `@pytest.mark.integration` and excluded from the default `pytest` run

## Task Dependency Graph

```json
{
  "waves": [
    {"wave": 1, "tasks": ["1", "2", "5"]},
    {"wave": 2, "tasks": ["3", "4", "6", "7", "8"]},
    {"wave": 3, "tasks": ["9"]},
    {"wave": 4, "tasks": ["10"]},
    {"wave": 5, "tasks": ["11"]},
    {"wave": 6, "tasks": ["12", "14"]},
    {"wave": 7, "tasks": ["13"]},
    {"wave": 8, "tasks": ["15"]},
    {"wave": 9, "tasks": ["16", "17", "18", "19", "20", "21"]},
    {"wave": 10, "tasks": ["22"]},
    {"wave": 11, "tasks": ["23"]}
  ]
}
```
