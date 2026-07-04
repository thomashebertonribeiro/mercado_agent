# Design Document — Intelligent Collector

## Overview

The Intelligent Collector is a redesign of the existing `collector/` and `scheduler/` subsystems. It replaces the Redis-Stream-based task queue approach with a direct, in-process orchestration loop that applies change-detection logic before any database write, enforces rate limits and concurrency control, and tracks every run as a `CollectionJob` record with per-item `CollectionLog` entries.

The design introduces five new files in the `collector/` package and one new repository, rewrites `scheduler/main.py`, adds two columns to existing tables (`products.last_checked_at` and `collection_jobs.extra_metrics`), adds one method to `APIClient`, and adds three new settings to `config/settings.py`.

No external dependencies beyond those already in the project are introduced. The property-based testing library chosen is **Hypothesis** (Python), which integrates cleanly with `pytest` and supports async strategies.

---

## Architecture

### Component Diagram

```
┌──────────────────────────────────────────────────────────────────────────┐
│  scheduler/main.py  (APScheduler AsyncIOScheduler)                       │
│                                                                          │
│   ┌─────────────────────┐       ┌────────────────────────────────────┐  │
│   │  run_full_collection │       │  run_incremental_collection        │  │
│   │  (every 24 h)        │       │  (every 6 h)                       │  │
│   └──────────┬──────────┘       └──────────────┬─────────────────────┘  │
└──────────────┼──────────────────────────────────┼───────────────────────┘
               │                                  │
               ▼                                  ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  collector/intelligent_collector.py  —  IntelligentCollector             │
│                                                                          │
│   collect(mode, product_ids?, category_ids?, threshold_hours?)           │
│   ├── _resolve_items()          — build item list for chosen mode        │
│   ├── _dispatch_workers()       — asyncio.gather + semaphore             │
│   └── _process_one()            — single-product pipeline                │
│        ├── CacheManager.get_payload()                                    │
│        ├── CacheManager.get_hash()                                       │
│        ├── [hash match?] → update last_checked_at + skipped log          │
│        ├── [no match]   → APIClient.fetch_product()                      │
│        │                → IngestionService.ingest_product_payload()      │
│        │                → CacheManager.set_hash()                        │
│        └── CollectionJobRepository.write_log()                           │
│                                                                          │
│  Dependencies (injected via constructor):                                │
│   ├── MercadoLivreAPICollector   (collector/api_client.py)              │
│   ├── CacheManager               (collector/cache_manager.py)           │
│   ├── RateLimiter                (collector/rate_limiter.py)            │
│   ├── MetricsCollector           (collector/metrics.py)                 │
│   ├── CollectionJobRepository    (repositories/collection_job.py)       │
│   ├── IngestionService           (services/ingestion.py)                │
│   └── AsyncSession               (database/connection.py)               │
└──────────────────────────────────────────────────────────────────────────┘
         │               │               │               │
         ▼               ▼               ▼               ▼
  ┌────────────┐  ┌────────────┐  ┌──────────┐  ┌───────────────┐
  │ api_client │  │   Redis    │  │PostgreSQL│  │  Loguru logger│
  │ (httpx)    │  │(cache_mgr) │  │(SQLAlch.)│  │  (structured) │
  └────────────┘  └────────────┘  └──────────┘  └───────────────┘
```

### Data Flow per Collection Mode

**by_product**
```
collect(mode=by_product, product_ids=[...])
  └─ _resolve_items()  →  returns product_ids list as-is (validates non-empty)
       └─ _dispatch_workers(items)
            └─ for each id: _process_one(id)
```

**by_category**
```
collect(mode=by_category, category_ids=[...])
  └─ _resolve_items()
       └─ for each category_id:
            APIClient.search_by_category(category_id, offset=0..min(total,1000))
            accumulate unique product_ids (deduplicate with set)
       → returns deduplicated product_ids list
  └─ _dispatch_workers(items)
```

**incremental**
```
collect(mode=incremental, threshold_hours=6)
  └─ _resolve_items()
       └─ DB query: SELECT id FROM products
            WHERE last_checked_at IS NULL
               OR last_checked_at < (now - threshold_hours)
       → returns stale product_ids
  └─ _dispatch_workers(items)
```

**full**
```
collect(mode=full)
  └─ _resolve_items()
       └─ DB query: SELECT id FROM products ORDER BY id
            (paginated in batches of 500 to avoid loading all into memory)
       → yields all product_ids
  └─ _dispatch_workers(items)
```

**_process_one pipeline (all modes)**
```
_process_one(product_id)
  1. RateLimiter.acquire()          — wait for semaphore slot
  2. asyncio.sleep(rate_limit_delay)— per-worker inter-call delay
  3. CacheManager.get_payload(id)   — try cache first
     ├─ hit (valid JSON)  → use cached_payload, skip API call
     └─ miss / corrupt    → APIClient.fetch_product(id)
                             → CacheManager.set_payload(id, payload)
  4. compute new_hash(payload)
  5. CacheManager.get_hash(id)      — check stored hash
     ├─ match             → update products.last_checked_at
     │                      job.skipped += 1
     │                      write CollectionLog(status='skipped')
     └─ no match (or None)→ IngestionService.ingest_product_payload(payload)
                             → n_events = count events written
                             → CacheManager.set_hash(id, new_hash)
                             → update products.last_checked_at
                             IF n_events == 0:
                               job.skipped += 1
                               write CollectionLog(status='skipped')
                             ELSE:
                               job.changes += 1
                               job.events_generated += n_events
                               write CollectionLog(status='ok', events=n_events)
  6. RateLimiter.release()
  7. MetricsCollector.record_request()
```


---

## Components and Interfaces

### 1. `collector/modes.py` — CollectionMode enum

```python
from enum import Enum

class CollectionMode(str, Enum):
    BY_PRODUCT   = "by_product"
    BY_CATEGORY  = "by_category"
    INCREMENTAL  = "incremental"
    FULL         = "full"
```

No logic lives here — the enum is a typed constant used by `IntelligentCollector` and the scheduler.

---

### 2. `collector/cache_manager.py` — CacheManager

Wraps `redis.asyncio.Redis`. All key formatting and TTL logic is encapsulated here so that callers never construct Redis keys manually.

```python
import json
from typing import Any, Dict, Optional
import redis.asyncio as aioredis
from utils.logger import logger


HASH_KEY_PREFIX    = "collector:hash:"
PAYLOAD_KEY_PREFIX = "collector:payload:"


class CacheManager:
    def __init__(
        self,
        redis_client: aioredis.Redis,
        hash_ttl_seconds: int = 3600,
        payload_ttl_seconds: int = 300,
    ) -> None:
        self._redis = redis_client
        self._hash_ttl = hash_ttl_seconds
        self._payload_ttl = payload_ttl_seconds

    # ── Hash operations ──────────────────────────────────────────────────

    async def get_hash(self, product_id: str) -> Optional[str]:
        """
        Returns the stored StateHash for product_id, or None on miss/error.
        On Redis connection error: logs WARNING, returns None (triggers fallback).
        """

    async def set_hash(self, product_id: str, state_hash: str) -> None:
        """
        Atomically stores state_hash under collector:hash:{product_id} with EX TTL.
        Uses redis SET with ex= parameter (single atomic command).
        On Redis connection error: logs WARNING and returns without raising.
        """

    # ── Payload operations ───────────────────────────────────────────────

    async def get_payload(self, product_id: str) -> Optional[Dict[str, Any]]:
        """
        Returns the cached API payload for product_id, or None.
        If cache value exists but cannot be JSON-decoded:
          - discards the key (redis.delete)
          - logs WARNING with product_id
          - returns None (caller will issue fresh API request)
        On Redis connection error: logs WARNING, returns None.
        """

    async def set_payload(self, product_id: str, payload: Dict[str, Any]) -> None:
        """
        Stores serialised payload under collector:payload:{product_id} with EX TTL.
        On Redis connection error: logs WARNING and returns without raising.
        """

    # ── Helpers ──────────────────────────────────────────────────────────

    def _hash_key(self, product_id: str) -> str:
        return f"{HASH_KEY_PREFIX}{product_id}"

    def _payload_key(self, product_id: str) -> str:
        return f"{PAYLOAD_KEY_PREFIX}{product_id}"
```

**Key design decisions:**
- All public methods swallow `redis.asyncio.RedisError` by logging WARNING and returning a safe default, never propagating to callers (R5.6, R5.7).
- `set_hash` uses `await self._redis.set(key, value, ex=ttl)` — a single atomic command (R5.5).
- Corrupted JSON is deleted from Redis before returning `None` so the next call doesn't repeatedly encounter the same bad entry (R5.4).

---

### 3. `collector/rate_limiter.py` — RateLimiter

Manages two concerns: a shared `asyncio.Semaphore` for concurrency, and a 429-triggered global pause.

```python
import asyncio
from typing import Optional
from utils.logger import logger


class RateLimiter:
    def __init__(self, max_concurrency: int = 5) -> None:
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._pause_event = asyncio.Event()
        self._pause_event.set()   # set = not paused; clear = paused

    async def acquire(self) -> None:
        """
        Waits for:
          1. The global pause to be lifted (pause_event.wait())
          2. A semaphore slot to become available
        Both conditions must be met before a new request is dispatched.
        Already in-flight requests (holding the semaphore) are NOT affected
        when a 429 pause is triggered.
        """

    def release(self) -> None:
        """Releases the semaphore slot acquired by acquire()."""

    async def pause_for(self, seconds: float) -> None:
        """
        Clears the pause event (blocks all future acquire() calls),
        waits `seconds`, then sets the event again.
        Concurrent calls to pause_for() are coalesced: if already paused,
        the caller awaits the existing pause rather than stacking a second one.
        """

    @property
    def is_paused(self) -> bool:
        return not self._pause_event.is_set()
```

**Key design decisions:**
- The `_pause_event` separates "429 global pause" from the per-slot semaphore (R4.5). In-flight requests that already hold the semaphore continue to completion.
- `acquire()` always checks the pause event first, then waits for a semaphore slot. This ensures that during a 429 pause, all new dispatches block at the `pause_event.wait()` line.
- `pause_for()` is idempotent when already paused: subsequent 429s simply extend no additional wait since the gate is already closed.

---

### 4. `collector/metrics.py` — MetricsCollector

A lightweight dataclass accumulator. All mutation is synchronous (no I/O). Persistence to the DB is the responsibility of `IntelligentCollector`.

```python
import time
from dataclasses import dataclass, field
from typing import Optional, List
import psutil


@dataclass
class MetricsCollector:
    # Core counters
    requests:         int = 0
    products:         int = 0
    errors:           int = 0
    changes:          int = 0
    events_generated: int = 0
    skipped:          int = 0

    # Timing
    _start_time:      float = field(default_factory=time.monotonic, init=False, repr=False)
    duration_seconds: int   = 0

    # Memory (RSS in MB, sampled via psutil)
    memory_rss_start_mb: Optional[float] = None
    memory_rss_end_mb:   Optional[float] = None

    # For avg_seconds_per_page (by_category mode)
    _page_durations: List[float] = field(default_factory=list, init=False, repr=False)

    def start(self) -> None:
        """Samples memory at job start. Call once before dispatching workers."""
        self._start_time = time.monotonic()
        self.memory_rss_start_mb = self._sample_rss_mb()

    def finish(self) -> None:
        """
        Finalises timing and end-of-run memory sample.
        duration_seconds = int(elapsed_wall_time).
        """
        self.duration_seconds = int(time.monotonic() - self._start_time)
        self.memory_rss_end_mb = self._sample_rss_mb()

    def record_page_duration(self, seconds: float) -> None:
        """Records a single page fetch duration for avg_seconds_per_page."""
        self._page_durations.append(seconds)

    @property
    def avg_seconds_per_page(self) -> Optional[float]:
        """Returns average page duration, or None if no pages were recorded."""
        if not self._page_durations:
            return None
        return sum(self._page_durations) / len(self._page_durations)

    def to_extra_metrics(self) -> dict:
        """
        Returns a dict suitable for storage in CollectionJob.extra_metrics JSONB.
        Keys: avg_seconds_per_page, memory_rss_start_mb, memory_rss_end_mb.
        None values are included so the schema remains consistent.
        """
        return {
            "avg_seconds_per_page":  self.avg_seconds_per_page,
            "memory_rss_start_mb":   self.memory_rss_start_mb,
            "memory_rss_end_mb":     self.memory_rss_end_mb,
        }

    @staticmethod
    def _sample_rss_mb() -> float:
        """Reads process RSS from psutil and converts bytes → MB (2 d.p.)."""
        proc = psutil.Process()
        return round(proc.memory_info().rss / (1024 * 1024), 2)
```

**Key design decisions:**
- `psutil` is already a transitive dependency via APScheduler; no new package needed.
- All counters are plain `int` so thread-safe increment (`+= 1`) works safely in async context (single-threaded event loop).
- `to_extra_metrics()` always includes all three keys — downstream consumers can check for `None` rather than `KeyError`.

---

### 5. `repositories/collection_job.py` — CollectionJobRepository

```python
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from models.operational import CollectionJob, CollectionLog
from repositories.base import BaseRepository


class CollectionJobRepository(BaseRepository[CollectionJob]):

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(CollectionJob, session)

    async def create_job(
        self,
        job_type: str,
        source: str = "scheduler",
        total_items: Optional[int] = None,
    ) -> CollectionJob:
        """
        Persists a new CollectionJob with status='pending'.
        Commits immediately so the job_id is available before processing begins.
        """

    async def set_running(self, job: CollectionJob) -> None:
        """Updates status='running', started_at=now(UTC). Commits."""

    async def set_done(
        self,
        job: CollectionJob,
        metrics: "MetricsCollector",   # forward ref
    ) -> None:
        """
        Persists all counters + extra_metrics, sets status='done',
        finished_at=now(UTC), duration_seconds.
        Commits BEFORE status transition (R6.5).
        """

    async def set_partial(
        self,
        job: CollectionJob,
        metrics: "MetricsCollector",
    ) -> None:
        """Same as set_done but status='partial'."""

    async def set_failed(
        self,
        job: CollectionJob,
        metrics: "MetricsCollector",
    ) -> None:
        """
        Best-effort persistence of partial counters, status='failed'.
        If the commit itself fails, logs WARNING and does not re-raise (R6.6).
        """

    async def write_log(
        self,
        job_id: int,
        entity_type: str,
        entity_id: str,
        status: str,          # 'ok' | 'skipped' | 'error'
        events_generated: int = 0,
        error_message: Optional[str] = None,
        source: str = "scheduler",
    ) -> CollectionLog:
        """
        Inserts one CollectionLog row.
        Enforces: if status == 'error', error_message must be non-empty (R8.7).
        Commits immediately so logs are visible even if the job fails mid-run.
        """

    async def find_running_job(self, job_type: str) -> Optional[CollectionJob]:
        """Returns an existing running CollectionJob for job_type, or None."""
```

---

### 6. `collector/api_client.py` — additions to `MercadoLivreAPICollector`

The existing `_make_request` tenacity decorator is replaced with a manual retry loop, and `search_by_category` is added.

#### 6a. Replace `_make_request` retry strategy

The new `_make_request` signature is unchanged externally but internally uses a manual loop instead of tenacity:

```python
async def _make_request(
    self,
    method: str,
    path: str,
    authenticated: bool = False,
    extra_headers: Optional[Dict[str, str]] = None,
    # Injected by IntelligentCollector when calling under rate-limit context:
    rate_limiter: Optional["RateLimiter"] = None,
    max_retries: int = 5,
    backoff_base: float = 2.0,
    max_wait: float = 60.0,
) -> Dict[str, Any]:
    """
    Manual exponential back-off retry loop.

    Retry on: HTTP 429, HTTP 5xx, httpx.RequestError (network timeout).
    No retry on: HTTP 404 (raises immediately with HTTPStatusError).

    Wait formula (attempt starts at 1):
        wait = min(backoff_base ** attempt, max_wait)

    Retry-After override (HTTP 429 only):
        - integer string  → int(header_value) seconds
        - HTTP-date string → max(0, (parsed_date - now()).total_seconds())

    If rate_limiter is provided, calls rate_limiter.pause_for(wait)
    instead of asyncio.sleep() for 429 responses so the global gate
    is respected across all concurrent workers.
    """
```

The `@retry` tenacity decorator is removed entirely from the class.

#### 6b. New method `search_by_category`

```python
async def search_by_category(
    self,
    category_id: str,
    offset: int = 0,
    limit: int = 50,
) -> Optional[Dict[str, Any]]:
    """
    GET /sites/MLB/search?category={category_id}&offset={offset}&limit={limit}

    Returns the raw search response dict (including paging.total) or None on 404.
    Raises for other HTTP errors after exhausting retries.
    """
```

#### Back-off helper (module-level, pure function)

```python
def compute_backoff_wait(attempt: int, base: float, max_wait: float) -> float:
    """
    Returns min(base ** attempt, max_wait).
    attempt is 1-indexed (first retry = attempt 1).
    No jitter applied.
    """
    return min(base ** attempt, max_wait)
```

#### Retry-After header parser (module-level, pure function)

```python
from email.utils import parsedate_to_datetime

def parse_retry_after(header_value: str, now: Optional[datetime] = None) -> float:
    """
    Parses a Retry-After header value and returns seconds to wait.

    Handles two formats:
      - Integer string: "120" → 120.0
      - HTTP-date:      "Wed, 21 Oct 2025 07:28:00 GMT" → delta seconds from now

    Returns 0.0 if the parsed date is in the past.
    now defaults to datetime.now(timezone.utc) if not provided.
    """
```

---

### 7. `collector/intelligent_collector.py` — IntelligentCollector

```python
import asyncio
import hashlib
import json
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Set

from sqlalchemy.ext.asyncio import AsyncSession

from collector.api_client import MercadoLivreAPICollector
from collector.cache_manager import CacheManager
from collector.metrics import MetricsCollector
from collector.modes import CollectionMode
from collector.rate_limiter import RateLimiter
from config.settings import settings
from database.connection import AsyncSessionLocal
from repositories.collection_job import CollectionJobRepository
from repositories.product import ProductRepository
from services.ingestion import IngestionService
from utils.logger import logger


class IntelligentCollector:
    """
    Orchestrates all collection modes with change detection, caching,
    rate limiting, parallelism, metrics, and structured logging.
    """

    def __init__(
        self,
        api_client:   MercadoLivreAPICollector,
        cache_manager: CacheManager,
        rate_limiter:  RateLimiter,
        session_factory = AsyncSessionLocal,
        max_concurrency: int = 5,
        max_retries:     int = 5,
        backoff_base:    float = 2.0,
        max_wait:        float = 60.0,
        api_rate_limit_delay: float = 0.5,
    ) -> None: ...

    async def collect(
        self,
        mode: CollectionMode,
        product_ids:        Optional[List[str]] = None,
        category_ids:       Optional[List[str]] = None,
        incremental_threshold_hours: int = 6,
    ) -> "CollectionJob":
        """
        Entry point. Creates CollectionJob, dispatches workers, finalises job.

        Raises ValueError (without creating a job) if:
          - mode == by_product and product_ids is empty or None

        Creates a CollectionJob with status=failed (before raising) if:
          - mode is not a recognised CollectionMode value
        """

    async def _resolve_items(
        self,
        mode: CollectionMode,
        product_ids: Optional[List[str]],
        category_ids: Optional[List[str]],
        incremental_threshold_hours: int,
        job_id: int,
        metrics: MetricsCollector,
    ) -> List[str]:
        """
        Builds and returns the list of product_ids to process.
        For by_category: paginates search, deduplicates, logs truncation.
        For incremental: queries DB for stale/null last_checked_at.
        For full: queries DB for all product IDs (batched, not loaded at once).
        """

    async def _dispatch_workers(
        self,
        product_ids: List[str],
        job: "CollectionJob",
        metrics: MetricsCollector,
        session: AsyncSession,
    ) -> None:
        """
        Wraps each _process_one() coroutine in a semaphore-controlled task.
        Uses asyncio.gather(*tasks, return_exceptions=True) so one failure
        does not cancel siblings.
        """

    async def _process_one(
        self,
        product_id: str,
        job: "CollectionJob",
        metrics: MetricsCollector,
        session: AsyncSession,
    ) -> None:
        """
        Full single-product pipeline:
          acquire rate_limiter → sleep(delay) → get_payload → hash compare
          → ingest or skip → write_log → update last_checked_at → release
        Catches all exceptions, writes error log, increments errors counter.
        """

    async def _update_last_checked_at(
        self,
        product_id: str,
        session: AsyncSession,
    ) -> None:
        """Updates products.last_checked_at = now(UTC) for the given product_id."""

    def _compute_state_hash(self, payload: Dict[str, Any]) -> str:
        """
        Computes SHA-256 over the fields that define product state:
        price, original_price, available_quantity, sold_quantity, status,
        title, listing_type_id.

        Fields are serialised deterministically (sorted JSON + SHA-256).
        """
```

---

### 8. `scheduler/main.py` — rewrite

```python
import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import redis.asyncio as aioredis

from collector.intelligent_collector import IntelligentCollector
from collector.cache_manager import CacheManager
from collector.modes import CollectionMode
from collector.rate_limiter import RateLimiter
from collector.api_client import MercadoLivreAPICollector
from config.settings import settings
from database.connection import AsyncSessionLocal
from repositories.collection_job import CollectionJobRepository
from utils.logger import logger


def _build_collector() -> IntelligentCollector:
    """Factory: constructs a fully-wired IntelligentCollector from settings."""


async def run_full_collection() -> None:
    """
    APScheduler job for full collection.
    Before starting: checks for a running job of type 'full' (R9.3).
    Logs job summary on completion (R9.4).
    """


async def run_incremental_collection() -> None:
    """
    APScheduler job for incremental collection.
    Before starting: checks for a running job of type 'incremental' (R9.3).
    """


async def run_scheduler() -> None:
    """
    Registers both jobs with AsyncIOScheduler and blocks the process.
    Intervals read from settings.SCHEDULER_FULL_INTERVAL_HOURS and
    settings.SCHEDULER_INCREMENTAL_INTERVAL_HOURS.
    """


if __name__ == "__main__":
    asyncio.run(run_scheduler())
```


---

## Data Models

### `products` table — add `last_checked_at` column

**Model change (`models/product.py`):**

```python
from sqlalchemy import DateTime
from sqlalchemy.orm import mapped_column, Mapped

# Add to Product class:
last_checked_at: Mapped[datetime | None] = mapped_column(
    DateTime(timezone=True),
    nullable=True,
    default=None,
    index=True,   # indexed for incremental mode queries
)
```

The column is nullable so existing rows require no backfill — `NULL` is treated as "never checked" by the incremental mode query, identical in behaviour to a very old timestamp.

**Index added:** `ix_products_last_checked_at` (used by incremental query `WHERE last_checked_at IS NULL OR last_checked_at < :threshold`).

---

### `collection_jobs` table — add `extra_metrics` column

**Model change (`models/operational.py`):**

```python
from sqlalchemy.dialects.postgresql import JSONB

# Add to CollectionJob class:
extra_metrics: Mapped[dict | None] = mapped_column(JSONB, nullable=True, default=None)
```

The column is nullable JSONB. Structure stored at runtime:

```json
{
  "avg_seconds_per_page": 1.34,
  "memory_rss_start_mb":  142.50,
  "memory_rss_end_mb":    148.20
}
```

`avg_seconds_per_page` is `null` for non-`by_category` runs. This is intentional — the key is always present so downstream queries can `IS NOT NULL` filter without `->` path checks.

---

### Migration script

**File:** `database/migrations/versions/0004_intelligent_collector.py`

```python
"""Add last_checked_at to products and extra_metrics to collection_jobs.

Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

def upgrade() -> None:
    # products.last_checked_at
    op.add_column(
        "products",
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_products_last_checked_at", "products", ["last_checked_at"]
    )

    # collection_jobs.extra_metrics
    op.add_column(
        "collection_jobs",
        sa.Column("extra_metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )

def downgrade() -> None:
    op.drop_index("ix_products_last_checked_at", table_name="products")
    op.drop_column("products", "last_checked_at")
    op.drop_column("collection_jobs", "extra_metrics")
```

---

## Configuration Settings Additions

Three new settings are added to `config/settings.py`. All have sensible defaults and are overridable via environment variables.

```python
class Settings(BaseSettings):
    # ... existing fields ...

    # Scheduler intervals (hours)
    SCHEDULER_FULL_INTERVAL_HOURS:        int   = 24
    SCHEDULER_INCREMENTAL_INTERVAL_HOURS: int   = 6

    # IntelligentCollector tuning
    COLLECTOR_MAX_CONCURRENCY:   int   = 5      # valid range: 1–50
    COLLECTOR_MAX_RETRIES:       int   = 5      # valid range: 1–10
    COLLECTOR_BACKOFF_BASE:      float = 2.0    # valid range: 1–10
    COLLECTOR_MAX_WAIT:          float = 60.0   # valid range: 1–300 s
    COLLECTOR_INCREMENTAL_THRESHOLD_HOURS: int = 6

    # Cache TTLs (seconds)
    CACHE_HASH_TTL_SECONDS:    int = 3600
    CACHE_PAYLOAD_TTL_SECONDS: int = 300
```

Environment variable names follow the existing `UPPER_SNAKE_CASE` convention and are read by Pydantic Settings from `.env`.

---

## Error Handling Strategy

### Hierarchy of failures

| Failure type | Scope | Handled by | Outcome |
|---|---|---|---|
| HTTP 404 | Single product | `_make_request` | No retry; CollectionLog error; processing continues |
| HTTP 429 | Global dispatch | `RateLimiter.pause_for()` | All new dispatches paused; in-flight complete |
| HTTP 5xx / timeout | Single product | `_make_request` retry loop | Up to `max_retries` retries with back-off |
| All retries exhausted | Single product | `_process_one` | CollectionLog error; `job.errors += 1`; next item processed |
| Redis read error | Single product | `CacheManager.get_*` | WARNING logged; API call proceeds (no abort) |
| Redis write error | Single product | `CacheManager.set_*` | WARNING logged; no abort |
| Corrupt Redis payload | Single product | `CacheManager.get_payload` | Entry deleted; WARNING logged; fresh API call |
| IngestionService exception | Single product | `_process_one` catch-all | CollectionLog error; `job.errors += 1`; continues |
| Unhandled exception in `collect()` | Entire job | `collect()` outer try/except | `CollectionJob.status = 'failed'`; partial counters persisted |
| Invalid mode | Call site | `collect()` pre-flight | ValueError raised; CollectionJob status=failed written first |
| Empty by_product list | Call site | `collect()` pre-flight | ValueError raised; NO CollectionJob written |

### `_process_one` exception catching

```python
async def _process_one(self, product_id, job, metrics, session):
    try:
        ...  # full pipeline
    except httpx.HTTPStatusError as e:
        status_code = e.response.status_code
        logger.bind(job_id=job.id, product_id=product_id,
                    http_status=status_code, error=str(e)).error("HTTP error")
        await job_repo.write_log(job.id, "product", product_id, "error",
                                 error_message=f"HTTP {status_code}: {e}")
        metrics.errors += 1
    except Exception as e:
        logger.bind(job_id=job.id, product_id=product_id,
                    error=str(e)).error("Unexpected error")
        await job_repo.write_log(job.id, "product", product_id, "error",
                                 error_message=str(e))
        metrics.errors += 1
    finally:
        self._rate_limiter.release()
```

### Job-level exception guard

```python
async def collect(self, mode, ...):
    job = await job_repo.create_job(...)
    try:
        await job_repo.set_running(job)
        items = await self._resolve_items(...)
        await self._dispatch_workers(items, job, metrics, session)
        if metrics.errors == 0:
            await job_repo.set_done(job, metrics)
        else:
            await job_repo.set_partial(job, metrics)
    except Exception as e:
        logger.error(f"Job {job.id} failed: {e}")
        await job_repo.set_failed(job, metrics)
        raise
    return job
```

### Metrics persistence ordering

`set_done()` and `set_partial()` follow this order:
1. Write `processed`, `errors`, `skipped`, `events_generated`, `changes` counters to DB.
2. Write `duration_seconds` and `extra_metrics` JSONB.
3. Set `status` to `done` / `partial` and `finished_at`.
4. Commit in a single transaction.

This ensures the status column reflects accurate counters even if later reads see the final status (R6.5).

---

## Structured Logging

All log calls use `logger.bind(**fields).level(message)` pattern from Loguru. No new logging configuration is needed beyond what `utils/logger.py` already sets up.

### Log call inventory

| Event | Level | Bound fields |
|---|---|---|
| Job started | INFO | `job_id`, `collection_mode`, `total_items` |
| Product — no change | DEBUG | `job_id`, `product_id` + message "no change detected" |
| Product — change | INFO | `job_id`, `product_id`, `events_generated` |
| Product — HTTP error | ERROR | `job_id`, `product_id`, `http_status`, `error` |
| Product — other error | ERROR | `job_id`, `product_id`, `error` |
| Job finished | INFO | `job_id`, `duration_seconds`, `processed`, `changes`, `errors`, `status` |
| 429 global pause | WARNING | `wait_seconds` |
| Redis read error | WARNING | `product_id`, `error` |
| Redis write error | WARNING | `product_id`, `error` |
| Corrupt cache entry | WARNING | `product_id` |
| Category truncated at 1000 | INFO | `category_id`, `paging_total` |
| Empty category | INFO | `category_id` |
| Duplicate running job skipped | WARNING | `job_type`, `existing_job_id` |


---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system — essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

The property-based testing library used is **Hypothesis** with `pytest-asyncio` for async support. Each property test is configured to run a minimum of 100 examples.

---

### Property Reflection

Before listing final properties, redundancies across the prework analysis are resolved:

- **2.2 and 2.5** both test full category pagination. Consolidated into Property 6.
- **4.2, 4.3, and 4.4** all test the same concurrency cap invariant from different angles. Consolidated into Property 9.
- **5.1 and 5.2** both test key-format + TTL correctness. Consolidated into Property 11.
- **6.3 and 10.6** both test `avg_seconds_per_page` computation. Consolidated into Property 14.
- **7.1 through 7.4 and 7.6** are logging field-presence properties with meaningful input variation (different job_ids, product_ids, status codes). Consolidated into three properties (Properties 16, 17, 18) grouped by log type.

---

### Property 1: Hash-check precedes any write

*For any* list of product IDs passed to `collect()`, for every product in that list, `CacheManager.get_hash()` must be called before any `session.add()` or `session.commit()` targeting that product's data.

**Validates: Requirements 1.1**

---

### Property 2: Cache miss triggers full ingestion and hash storage

*For any* product ID for which `CacheManager.get_hash()` returns `None`, after `_process_one()` completes without error, `IngestionService.ingest_product_payload()` must have been called exactly once, and `CacheManager.set_hash()` must have been called exactly once with a non-empty string, and only after the ingestion call returned.

**Validates: Requirements 1.2**

---

### Property 3: Hash match → skip, no ingestion

*For any* product ID and API payload whose computed state hash equals the value returned by `CacheManager.get_hash()`, after `_process_one()` completes: `IngestionService.ingest_product_payload()` must NOT have been called, `products.last_checked_at` must have been updated, the job's `skipped` counter must have incremented by 1, and the `CollectionLog` for that product must have `status='skipped'`.

**Validates: Requirements 1.3**

---

### Property 4: Hash mismatch → ingestion before cache update

*For any* product ID where the computed hash differs from the stored hash, after `_process_one()` completes: `IngestionService.ingest_product_payload()` must have been called before `CacheManager.set_hash()`. If `IngestionService` raises an exception, `CacheManager.set_hash()` must NOT have been called at all.

**Validates: Requirements 1.4**

---

### Property 5: Zero events from IngestionService → skip

*For any* product where `IngestionService.ingest_product_payload()` returns without generating any events (mocked to return 0), after `_process_one()` completes: `job.skipped` must have incremented by 1 and the `CollectionLog` must have `status='skipped'`.

**Validates: Requirements 1.5**

---

### Property 6: by_product processes exactly the input set

*For any* non-empty list of product ID strings passed to `collect(mode=by_product, product_ids=ids)`, the set of product IDs for which `_process_one()` is invoked must equal the input set exactly — no more, no fewer.

**Validates: Requirements 2.1**

---

### Property 7: by_category collects all paginated products (deduplicated)

*For any* mock paginated category search response containing an arbitrary multiset of product IDs spread across multiple pages, `collect(mode=by_category)` must invoke `_process_one()` exactly once per unique product ID, regardless of how many times that ID appears across pages.

**Validates: Requirements 2.2, 2.5, 10.5**

---

### Property 8: incremental mode selects only stale/null products

*For any* set of products with varying `last_checked_at` timestamps (null, older than threshold, newer than threshold), `collect(mode=incremental)` must invoke `_process_one()` for exactly the subset where `last_checked_at IS NULL OR last_checked_at < (job_start - threshold)`.

**Validates: Requirements 2.3**

---

### Property 9: Concurrency cap is never exceeded

*For any* list of N product IDs (N > `max_concurrency`) processed by `_dispatch_workers()`, the peak number of simultaneously in-flight `_process_one()` coroutines must never exceed `max_concurrency`.

**Validates: Requirements 4.2, 4.3, 4.4**

---

### Property 10: Back-off formula is exact

*For any* triple `(attempt: int in [1..10], base: float in [1.0..10.0], max_wait: float in [1.0..300.0])`, `compute_backoff_wait(attempt, base, max_wait)` must equal exactly `min(base ** attempt, max_wait)`.

**Validates: Requirements 3.2**

---

### Property 11: Retry-After integer parsing is exact

*For any* non-negative integer N, `parse_retry_after(str(N))` must return exactly `float(N)`.

**Validates: Requirements 3.3**

---

### Property 12: Retry-After HTTP-date parsing returns correct delta

*For any* future datetime D and reference time T where D > T, `parse_retry_after(http_date_format(D), now=T)` must return a value within ±1 second of `(D - T).total_seconds()`. For past dates (D ≤ T), the result must be 0.0.

**Validates: Requirements 3.4**

---

### Property 13: Cache key format and TTL are always correct

*For any* product ID string `pid`:
- `CacheManager.set_hash(pid, h)` must call `redis.set(f"collector:hash:{pid}", h, ex=hash_ttl_seconds)` using a single atomic SET command.
- `CacheManager.set_payload(pid, p)` must call `redis.set(f"collector:payload:{pid}", json.dumps(p), ex=payload_ttl_seconds)` using a single atomic SET command.

**Validates: Requirements 5.1, 5.2, 5.5**

---

### Property 14: avg_seconds_per_page equals arithmetic mean

*For any* non-empty list of page durations `[d1, d2, ..., dn]` recorded via `MetricsCollector.record_page_duration()`, `metrics.avg_seconds_per_page` must equal `sum(durations) / len(durations)`.

**Validates: Requirements 6.3, 10.6**

---

### Property 15: One CollectionLog per processed item with correct error_message constraint

*For any* collection run processing N product IDs, exactly N `CollectionLog` records must be created. For every log with `status='error'`, `error_message` must be a non-empty string. For every log with `status='ok'` or `status='skipped'`, `error_message` must be `NULL`.

**Validates: Requirements 8.6, 8.7**

---

### Property 16: Job start log contains required bound fields

*For any* `(job_id, collection_mode, total_items)` combination, the INFO log emitted when a job starts must contain Loguru-bound fields `job_id`, `collection_mode`, and `total_items` with the correct values.

**Validates: Requirements 7.1**

---

### Property 17: Per-product logs contain correct bound fields

*For any* processed product:
- If no change detected: DEBUG log must contain bound fields `job_id`, `product_id`.
- If change detected: INFO log must contain bound fields `job_id`, `product_id`, `events_generated`.
- If HTTP error: ERROR log must contain bound fields `job_id`, `product_id`, `http_status`, `error`.

**Validates: Requirements 7.2, 7.3, 7.4**

---

### Property 18: Job finish log contains required bound fields

*For any* finished job, the INFO log emitted on completion must contain Loguru-bound fields `job_id`, `duration_seconds`, `processed`, `changes`, `errors`, and `status` with values matching the job's final state.

**Validates: Requirements 7.6**

---

### Property 19: Category pagination stops at min(paging.total, 1000)

*For any* `paging_total` value, the number of API pages fetched during `by_category` collection must equal `ceil(min(paging_total, 1000) / 50)`, and the total number of items discovered before deduplication must not exceed `min(paging_total, 1000)`.

**Validates: Requirements 10.2, 10.3**

---

## Testing Strategy

### Dual-layer approach

The test suite uses two complementary layers:

1. **Property-based tests** (Hypothesis) — validate universal properties across wide input spaces. Each property maps directly to a design property above. Minimum 100 examples per test.
2. **Example-based unit tests** (pytest) — validate specific scenarios, edge cases, and interactions that require deterministic inputs.

### Property test configuration

```python
from hypothesis import given, settings as h_settings, HealthCheck
from hypothesis import strategies as st

@h_settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
@given(st.lists(st.text(min_size=1, max_size=20), min_size=1, max_size=50))
async def test_property_6_by_product_exact_set(product_ids):
    """Feature: intelligent-collector, Property 6: by_product processes exactly the input set"""
    ...
```

Tag format for all property tests:
```
# Feature: intelligent-collector, Property {N}: {property_title}
```

### Example-based tests (not properties)

The following behaviours are covered by targeted example-based tests rather than property tests, because input variation does not reveal additional bugs:

| Scenario | Test type | Reasoning |
|---|---|---|
| Invalid mode → status=failed + ValueError | Example | Only one failure mode, no meaningful variation |
| Empty by_product list → ValueError, no job | Edge case | Single boundary condition |
| 429 global pause, in-flight requests complete | Example | Concurrency scenario requires controlled timing |
| Duplicate running job → skip + WARNING log | Example | Single DB-state precondition |
| `CollectionJob` status transitions (pending→running→done/partial/failed) | Example | Deterministic state machine |
| Memory sampling via psutil | Example | Side effect, not input-variant logic |
| `CollectionJob.extra_metrics` schema presence | Smoke | Model/schema check |
| Scheduler job registration with correct interval | Smoke | Wiring/configuration check |

### Test isolation approach

All tests mock:
- `redis.asyncio.Redis` → `AsyncMock` with configurable responses per key
- `httpx.AsyncClient.request` → `AsyncMock` returning canned JSON responses
- `AsyncSessionLocal` → `AsyncMock` or SQLite-based async test session
- `psutil.Process.memory_info` → `MagicMock` returning controlled RSS values
- `asyncio.sleep` → patched to record call args without actual sleeping

No tests make real network or database calls. Integration tests (if added later) are explicitly tagged `@pytest.mark.integration` and excluded from the default `pytest` run.

### File layout for tests

```
tests/
  test_intelligent_collector/
    __init__.py
    test_cache_manager.py          # Properties 11, 13 + edge cases
    test_rate_limiter.py           # Property 9 + 429 pause example
    test_metrics.py                # Property 14 + memory sampling example
    test_api_client.py             # Properties 10, 11, 12 (back-off + Retry-After)
    test_intelligent_collector.py  # Properties 1–8, 15–19 (orchestrator)
    test_collection_job_repo.py    # Example tests for status transitions
```

