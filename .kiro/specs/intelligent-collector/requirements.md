# Requirements Document

## Introduction

The Intelligent Collector is an enhanced data collection subsystem for the Mercado Livre Data Lake.
It replaces and extends the basic `collector/` and `scheduler/` modules with a smart, resource-efficient pipeline that avoids unnecessary work: before collecting any product or category, the system verifies whether data already exists and whether it has actually changed. Only real changes generate events; unchanged items only update a lightweight timestamp.

The system supports four collection modes (category, product, incremental, full), implements resilience mechanisms (retries with exponential back-off, rate limiting, concurrency control), uses Redis for multi-layer caching, and records structured metrics and logs for observability.

---

## Glossary

- **Collector**: The intelligent data collection subsystem described in this document.
- **CollectionJob**: A database record (`collection_jobs` table) that tracks one execution run.
- **CollectionLog**: A database record (`collection_logs` table) that tracks the outcome of processing one individual item within a CollectionJob.
- **IngestionService**: The existing `services/ingestion.py` service that performs hash-based deduplication and writes events to the event-sourced schema.
- **APIClient**: The existing `collector/api_client.py` HTTP client for the Mercado Livre public/authenticated REST API.
- **CacheManager**: The Redis-backed component responsible for storing and retrieving cached API responses and product state hashes.
- **MetricsCollector**: The component that accumulates run-time metrics (timing, request counts, error counts, change counts, memory usage) for a CollectionJob.
- **RateLimiter**: The component that enforces inter-request delays and concurrency limits to avoid overwhelming the Mercado Livre API.
- **CollectionMode**: One of four execution modes: `full`, `incremental`, `by_category`, `by_product`.
- **IncrementalCollection**: A mode that only processes products whose `last_checked_at` timestamp is older than a configured threshold, or products that have never been collected (null `last_checked_at`).
- **FullCollection**: A mode that processes every tracked product regardless of previous collection state.
- **CategoryCollection**: A mode that collects all products belonging to one or more specified Mercado Livre category IDs.
- **ProductCollection**: A mode that collects one or more specific products by their Mercado Livre item ID.
- **StateHash**: The SHA-256 fingerprint computed from a product's current API response payload (covering price, stock, status, title, listing type). Stored in Redis and compared before any database write.
- **last_checked_at**: A timestamp field on the `products` table recording the last time the Collector verified a product, even if no change was detected.
- **RawPayload**: Bronze-layer storage model (`models/raw_payload.py`) that persists every API response verbatim.
- **Tenant**: The operator running the data lake; the Collector acts on behalf of the Tenant.

---

## Requirements

### Requirement 1: Existence and Change Detection Before Collection

**User Story:** As a Tenant, I want the Collector to verify whether a product already exists and whether its state has changed before writing any data, so that the system never stores redundant data or generates noise events.

#### Acceptance Criteria

1. WHEN the Collector is about to process a product, THE Collector SHALL query the CacheManager for the product's StateHash before issuing any database or API write operation.
2. IF the CacheManager returns no StateHash for a product, THEN THE Collector SHALL treat the product as new, proceed with full ingestion via IngestionService, and store the resulting StateHash in the CacheManager after successful ingestion.
3. IF the StateHash computed from the current API response matches the stored StateHash, THEN THE Collector SHALL update only the `last_checked_at` timestamp of the product, increment the `skipped` counter on the CollectionJob, and log the outcome as `skipped` in the CollectionLog.
4. IF the StateHash computed from the current API response differs from the stored StateHash, THEN THE Collector SHALL pass the full payload to IngestionService for event generation, and update the StateHash in the CacheManager only after IngestionService returns successfully.
5. IF IngestionService generates zero events for a payload, THEN THE Collector SHALL update `last_checked_at`, increment the `skipped` counter on the CollectionJob, and log the outcome as `skipped` in the CollectionLog.

---

### Requirement 2: Collection Modes

**User Story:** As a Tenant, I want to trigger collection in four different modes, so that I can choose between a targeted refresh or a comprehensive sweep depending on operational needs.

#### Acceptance Criteria

1. WHEN CollectionMode is `by_product`, THE Collector SHALL accept a non-empty list of one or more Mercado Livre item ID strings and collect only those specific products.
2. WHEN CollectionMode is `by_category`, THE Collector SHALL paginate through all products returned by the Mercado Livre search API for the specified category IDs and collect each product.
3. WHEN CollectionMode is `incremental`, THE Collector SHALL select products whose `last_checked_at` is either NULL or older than `incremental_threshold_hours` hours before the job start time (configurable, default: 6 hours) and collect them.
4. WHEN CollectionMode is `full`, THE Collector SHALL iterate over every product tracked in the `products` dimension table and collect each one.
5. WHEN CollectionMode is `by_category` and a category returns paginated results, THE Collector SHALL follow all pagination cursors until all results are exhausted.
6. IF an unrecognized CollectionMode is provided, THEN THE Collector SHALL first record a CollectionJob with status `failed` in the database, and then raise a `ValueError` that propagates to the caller.
7. IF CollectionMode is `by_product` and the provided list of item IDs is empty, THEN THE Collector SHALL raise a `ValueError` without creating a CollectionJob record.

---

### Requirement 3: Retry and Resilience

**User Story:** As a Tenant, I want failed API requests to be automatically retried with exponential back-off, so that transient network errors and rate-limit responses do not cause data gaps.

#### Acceptance Criteria

1. WHEN an API request fails with a transient error (HTTP 429, HTTP 5xx, or network timeout), THE Collector SHALL retry the request up to a configurable `max_retries` number of times (valid range: 1–10, default: 5).
2. WHEN retrying, THE Collector SHALL wait exactly `min(retry_backoff_base ** attempt, retry_max_wait)` seconds before the next attempt, where `attempt` is the ordinal of the retry starting at 1, `retry_backoff_base` is configurable (valid range: 1–10, default: 2), and `retry_max_wait` is configurable (valid range: 1–300 s, default: 60 s). No additional jitter or randomness is applied.
3. WHEN a HTTP 429 response includes a `Retry-After` header whose value is an integer string, THE Collector SHALL wait exactly that many seconds before retrying, overriding the computed back-off delay.
4. WHEN a HTTP 429 response includes a `Retry-After` header whose value is an HTTP-date string, THE Collector SHALL compute the number of seconds until that date and wait that duration before retrying, overriding the computed back-off delay.
5. IF all retry attempts are exhausted for a product, THEN THE Collector SHALL record the product's CollectionLog with status `error`, increment the `errors` counter on the CollectionJob, and continue processing remaining items.
6. WHEN an API request fails with HTTP 404, THE Collector SHALL not retry, SHALL record the CollectionLog as `error` with the message "Product not found (404)", and SHALL continue with the next item.

---

### Requirement 4: Rate Control and Parallelism

**User Story:** As a Tenant, I want the Collector to respect API rate limits and process items concurrently within safe bounds, so that collection is fast but does not trigger API bans or overload the database.

#### Acceptance Criteria

1. THE Collector SHALL wait at least `api_rate_limit_delay` seconds (configurable, default: 0.5 s) before each outbound API call within a single worker.
2. THE Collector SHALL process items in parallel using a configurable `max_concurrency` number of concurrent workers (valid range: 1–50, default: 5).
3. WHILE `max_concurrency` workers are all active, THE Collector SHALL hold additional items in a queue and not dispatch new requests until a worker slot becomes available.
4. THE RateLimiter SHALL enforce that at most `max_concurrency` API requests are in-flight simultaneously across all workers, using a shared concurrency gate available to all workers.
5. WHEN a HTTP 429 response is received by any worker, THE RateLimiter SHALL block the dispatch of any new API requests for 5 seconds (or the duration from the `Retry-After` header if present) before resuming, without aborting already in-flight requests.

---

### Requirement 5: Redis Cache Layer

**User Story:** As a Tenant, I want API responses and product state hashes to be cached in Redis, so that repeated lookups of unchanged products do not generate unnecessary API calls.

#### Acceptance Criteria

1. THE CacheManager SHALL store the StateHash of each product under the key `collector:hash:{product_id}` with a configurable TTL of `cache_hash_ttl_seconds` (default: 3600 s).
2. THE CacheManager SHALL store the full serialised API response for each product under the key `collector:payload:{product_id}` with a configurable TTL of `cache_payload_ttl_seconds` (default: 300 s).
3. WHEN the Collector needs a product's current payload, IF a valid cache entry exists (key is present and its value can be successfully deserialized as JSON), THEN THE Collector SHALL use the cached payload and SHALL NOT issue a new API request.
4. IF a cache entry exists but its value cannot be successfully deserialized as JSON, THEN THE Collector SHALL discard the cached entry, issue a new API request, and log a WARNING that a corrupted cache entry was discarded for that product ID.
5. WHEN the Collector writes a new StateHash, THE CacheManager SHALL atomically replace the previous hash entry for that product using a Redis SET with EX (expiry) option.
6. IF a Redis connection error occurs during a read, THEN THE Collector SHALL fall back to issuing a direct API request and log a WARNING; it SHALL NOT abort the collection run.
7. IF a Redis connection error occurs during a write, THEN THE Collector SHALL log a WARNING and continue without storing the value; it SHALL NOT abort the collection run.

---

### Requirement 6: Metrics Registration

**User Story:** As a Tenant, I want detailed metrics recorded for each collection run, so that I can monitor performance, detect degradation, and audit data quality over time.

#### Acceptance Criteria

1. THE MetricsCollector SHALL record the following counters for each CollectionJob: total API requests made, total products processed, total events generated, total errors, and total detected changes (products where StateHash differed).
2. THE MetricsCollector SHALL record elapsed wall-clock time as a whole number of seconds from job start to job finish and store it in the `duration_seconds` field of the CollectionJob.
3. IF the CollectionMode is `by_category`, THEN THE MetricsCollector SHALL compute the average seconds per paginated API response (total page fetch time divided by number of pages) and store the result under the key `avg_seconds_per_page` in the `extra_metrics` JSONB field of the CollectionJob.
4. THE MetricsCollector SHALL sample resident memory usage (RSS in MB, rounded to two decimal places) at job start and at job finish, and store both values in the `extra_metrics` JSONB field under the keys `memory_rss_start_mb` and `memory_rss_end_mb` respectively.
5. WHEN a CollectionJob finishes with status `done` or `partial`, THE MetricsCollector SHALL persist the counters from criterion 1, the `duration_seconds` value from criterion 2, and the `extra_metrics` values from criteria 3 and 4 to the `collection_jobs` record before the status field transitions to `done` or `partial`.
6. WHEN a CollectionJob transitions to status `failed`, THE MetricsCollector SHALL attempt to persist `processed`, `errors`, `skipped`, and `events_generated` counters to the `collection_jobs` record; IF that persistence fails, THE MetricsCollector SHALL leave those fields at their current database values and SHALL NOT retry.
7. THE CollectionJob model SHALL include a nullable JSONB field named `extra_metrics` to store per-run auxiliary metrics (average page time, memory samples, and any future additions) that do not have dedicated columns.

---

### Requirement 7: Structured Logging

**User Story:** As a Tenant, I want every collection action to be logged with structured context, so that I can trace individual product outcomes, diagnose failures, and audit the system's behaviour.

#### Acceptance Criteria

1. WHEN a CollectionJob starts, THE Collector SHALL emit a log entry at INFO level with key-value fields bound via Loguru's `bind()`: `job_id`, `collection_mode`, and `total_items`.
2. WHEN a product is processed and no change is detected, THE Collector SHALL emit a log entry at DEBUG level with bound fields: `job_id`, `product_id` (the Mercado Livre item ID string), and the message text "no change detected".
3. WHEN a product is processed and a change is detected, THE Collector SHALL emit a log entry at INFO level with bound fields: `job_id`, `product_id`, and `events_generated` (integer count of events written).
4. WHEN a product processing fails due to an HTTP error, THE Collector SHALL emit a log entry at ERROR level with bound fields: `job_id`, `product_id`, `http_status` (integer HTTP status code), and `error` (exception message string).
5. WHEN a product processing fails due to a non-HTTP exception, THE Collector SHALL emit a log entry at ERROR level with bound fields: `job_id`, `product_id`, and `error` (exception message string), without an `http_status` field.
6. WHEN a CollectionJob finishes, THE Collector SHALL emit a log entry at INFO level with bound fields: `job_id`, `duration_seconds`, `processed`, `changes`, `errors`, and `status`.
7. THE Collector SHALL use the existing Loguru-based logger from `utils/logger.py` for all log output and SHALL NOT import or configure any other logging library.

---

### Requirement 8: CollectionJob Lifecycle Management

**User Story:** As a Tenant, I want each collection run to be tracked as a CollectionJob record with accurate status transitions, so that I can query historical runs and understand the health of the pipeline.

#### Acceptance Criteria

1. WHEN a new collection run is initiated, THE Collector SHALL create a CollectionJob record with status `pending` and persist it to the database before any item processing begins.
2. WHEN item processing begins, THE Collector SHALL update the CollectionJob status to `running` and set `started_at` to the current UTC timestamp.
3. WHEN all items complete and the `errors` counter on the CollectionJob equals 0, THE Collector SHALL update the CollectionJob status to `done` and set `finished_at` to the current UTC timestamp.
4. WHEN all items complete and the `errors` counter on the CollectionJob is greater than or equal to 1, THE Collector SHALL update the CollectionJob status to `partial` and set `finished_at` to the current UTC timestamp.
5. IF an unhandled exception terminates the job before all items complete, THEN THE Collector SHALL update the CollectionJob status to `failed`, set `finished_at` to the current UTC timestamp, and attempt to persist the counters `processed`, `errors`, `skipped`, and `events_generated` as they stand at the time of failure.
6. THE Collector SHALL write one CollectionLog record per processed item, containing: `entity_type` (one of: `product`, `seller`, `review`, `question`, `ranking`), `entity_id` (the Mercado Livre item ID string), `status` (one of: `ok`, `skipped`, `error`), `events_generated` (integer), and `error_message`.
7. IF a CollectionLog record has status `error`, THEN the `error_message` field SHALL be a non-empty string describing the failure; IF a CollectionLog record has status `ok` or `skipped`, THEN the `error_message` field SHALL be NULL.

---

### Requirement 9: Scheduler Integration

**User Story:** As a Tenant, I want the intelligent collector to integrate with the existing APScheduler-based scheduler, so that full and incremental collection runs execute automatically on a configurable schedule.

#### Acceptance Criteria

1. THE Scheduler SHALL register a periodic job for `full` collection with an interval configurable via the setting `SCHEDULER_FULL_INTERVAL_HOURS` (default: 24 hours).
2. THE Scheduler SHALL register a periodic job for `incremental` collection with an interval configurable via the setting `SCHEDULER_INCREMENTAL_INTERVAL_HOURS` (default: 6 hours).
3. IF a CollectionJob record with the same `job_type` and status `running` already exists in the database at trigger time, THEN THE Scheduler SHALL skip creating a new run and log a WARNING with the existing job's ID.
4. WHEN a scheduled job completes, THE Scheduler SHALL log an INFO entry containing the CollectionJob's `id`, `status`, `processed`, `errors`, and `duration_seconds` fields.
5. THE Scheduler SHALL use the existing `AsyncIOScheduler` from APScheduler and SHALL NOT introduce a new scheduling library.

---

### Requirement 10: Category Pagination and Discovery

**User Story:** As a Tenant, I want the Collector to discover and paginate through all products within a Mercado Livre category, so that no products are missed during a category-scoped collection.

#### Acceptance Criteria

1. WHEN performing `by_category` collection, THE Collector SHALL use the Mercado Livre search endpoint (`/sites/MLB/search?category={id}&offset={n}&limit=50`) to retrieve products in pages of up to 50 items.
2. WHEN a search response contains a `paging.total` value, THE Collector SHALL iterate pages by incrementing the offset by 50 until the offset reaches `min(paging.total, 1000)`, at which point pagination stops.
3. IF `paging.total` exceeds 1000, THEN THE Collector SHALL log an INFO entry noting that the category result set was truncated to the first 1000 items due to the API cap, and SHALL proceed with only those 1000 items.
4. WHEN a search response contains zero results for a category, THE Collector SHALL log an INFO entry for that category and set the `total_items` field on the CollectionJob to 0 for that category.
5. WHEN the same product ID appears in multiple category pages within the same CollectionJob run, THE Collector SHALL deduplicate product IDs before passing them to the ingestion pipeline, processing each unique product ID only once per run.
6. WHEN a `by_category` run completes, THE Collector SHALL store the average seconds per paginated API response under the key `avg_seconds_per_page` in the `extra_metrics` JSONB field of the CollectionJob.
