"""Prometheus metrics exported by the simulated services.

Every service exposes ``/metrics`` (the worker on port 9100). Prometheus adds the ``service``
and ``environment`` labels at scrape time, so the metrics here carry only their own
dimensions. The default registry also exports ``process_*`` (CPU seconds, resident memory,
open fds, start time) and ``python_gc_*``.

Label values are bounded: routes are templates (``/orders/{order_id}``), never raw paths.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)

# --- HTTP server
HTTP_REQUESTS = Counter(
    "http_server_requests_total", "HTTP requests handled", ["method", "route", "status"]
)
HTTP_DURATION = Histogram(
    "http_server_request_duration_seconds",
    "HTTP request latency by endpoint",
    ["method", "route"],
    buckets=LATENCY_BUCKETS,
)
HTTP_IN_FLIGHT = Gauge("http_server_requests_in_flight", "HTTP requests being handled")

# --- HTTP client (calls to other services / third parties)
CLIENT_REQUESTS = Counter(
    "http_client_requests_total",
    "Outbound HTTP calls; status is the HTTP code or 'error' for connection failures/timeouts",
    ["peer", "method", "status"],
)
CLIENT_DURATION = Histogram(
    "http_client_request_duration_seconds",
    "Outbound HTTP call latency",
    ["peer", "method"],
    buckets=LATENCY_BUCKETS,
)

# --- PostgreSQL (client side)
DB_QUERY_DURATION = Histogram(
    "db_query_duration_seconds", "Database query latency", ["query"], buckets=LATENCY_BUCKETS
)
DB_QUERY_ERRORS = Counter("db_query_errors_total", "Failed database queries", ["query", "error"])
DB_POOL_MAX = Gauge("db_pool_max_connections", "Configured connection pool size")
DB_POOL_IN_USE = Gauge("db_pool_connections_in_use", "Connections checked out of the pool")
DB_POOL_IDLE = Gauge("db_pool_connections_idle", "Open connections idle in the pool")
DB_POOL_PENDING = Gauge("db_pool_pending_acquires", "Callers waiting for a pool connection")
DB_POOL_ACQUIRE = Histogram(
    "db_pool_acquire_duration_seconds",
    "Time spent waiting for a pool connection",
    buckets=LATENCY_BUCKETS,
)
DB_POOL_TIMEOUTS = Counter(
    "db_pool_acquire_timeouts_total", "Connection acquisitions that timed out"
)

# --- Redis
REDIS_DURATION = Histogram(
    "redis_command_duration_seconds", "Redis command latency", ["command"], buckets=LATENCY_BUCKETS
)
REDIS_ERRORS = Counter("redis_command_errors_total", "Failed Redis commands", ["command", "error"])
CACHE_REQUESTS = Counter(
    "cache_requests_total", "Cache lookups by result (hit|miss|error)", ["cache", "result"]
)

# --- Queue / worker
QUEUE_DEPTH = Gauge("queue_depth", "Jobs waiting in the queue", ["queue"])
QUEUE_OLDEST_AGE = Gauge("queue_oldest_job_age_seconds", "Age of the oldest waiting job", ["queue"])
QUEUE_ENQUEUED = Counter("queue_jobs_enqueued_total", "Jobs enqueued", ["queue"])
WORKER_JOBS = Counter(
    "worker_jobs_processed_total",
    "Jobs processed by result (success|retried|dead_lettered)",
    ["queue", "result"],
)
WORKER_FAILURES = Counter(
    "worker_job_failures_total", "Job attempts that failed, by reason", ["queue", "reason"]
)
WORKER_DURATION = Histogram(
    "worker_job_duration_seconds", "Job processing time", ["queue"], buckets=LATENCY_BUCKETS
)
WORKER_BUSY = Gauge("worker_busy_consumers", "Consumers currently processing a job")

# --- Business / build
ORDERS = Counter("orders_created_total", "Order placement attempts by result", ["result"])
BUILD_INFO = Gauge("app_build_info", "Running build (value is always 1)", ["version"])
