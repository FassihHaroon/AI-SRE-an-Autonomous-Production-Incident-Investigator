"""Deterministic scenario worlds: injected faults → consistent telemetry → MCP-shaped tool results.

A scenario declares *what is wrong* (faults of the same types the simulator injects, plus
deployments and optional overrides), not tool outputs. :class:`Telemetry` derives what each
fault would make visible: metric anomalies, error signatures, pool and database state,
dependency health, container pressure. :class:`WorldGateway` answers every read-only tool the
agent may call from that model, with the payload shapes of the real MCP servers.

Why not the live simulator? Live runs take minutes per scenario and depend on timing, so they
cannot be compared run to run. Worlds are reproducible, fast and can express situations the
simulator cannot (missing telemetry, several faults at once, conflicting signals). The same
fault definitions can be replayed on the live simulator with ``--mode live`` (see
``evaluation/run``) to check that the worlds stay realistic.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from ai_sre.agent.models import Evidence
from ai_sre.observability.tracing import finish_tool_span, tool_span

APP = ("api-gateway", "orders-service", "fulfilment-worker")

HEALTHY: dict[str, dict[str, float]] = {
    "api-gateway": {
        "request_rate": 10.0,
        "error_rate": 0.0,
        "latency_p50": 0.004,
        "latency_p95": 0.1,
        "latency_p99": 0.22,
        "cpu": 0.05,
        "memory_bytes": 74e6,
        "db_pool_utilization": 0.0,
        "db_pool_pending": 0.0,
        "db_query_p95": 0.005,
        "redis_error_rate": 0.0,
        "upstream_error_rate": 0.0,
    },
    "orders-service": {
        "request_rate": 2.9,
        "error_rate": 0.0,
        "latency_p50": 0.004,
        "latency_p95": 0.2,
        "latency_p99": 0.24,
        "cpu": 0.025,
        "memory_bytes": 75e6,
        "db_pool_utilization": 0.1,
        "db_pool_pending": 0.0,
        "db_query_p95": 0.005,
        "redis_error_rate": 0.0,
        "upstream_error_rate": 0.0,
    },
    "fulfilment-worker": {
        "cpu": 0.02,
        "memory_bytes": 60e6,
        "db_pool_utilization": 0.05,
        "db_pool_pending": 0.0,
        "db_query_p95": 0.004,
        "redis_error_rate": 0.0,
        "queue_depth": 0.0,
        "worker_failure_rate": 0.0,
    },
}
UNITS = {
    "request_rate": "req/s",
    "error_rate": "ratio",
    "latency_p50": "s",
    "latency_p95": "s",
    "latency_p99": "s",
    "cpu": "cores",
    "memory_bytes": "bytes",
    "db_pool_utilization": "ratio",
    "db_pool_pending": "callers",
    "db_query_p95": "s",
    "redis_error_rate": "errors/s",
    "upstream_error_rate": "errors/s",
    "queue_depth": "jobs",
    "worker_failure_rate": "failures/s",
}
BASE_VERSIONS = {"api-gateway": "3.8.0", "orders-service": "2.3.1", "fulfilment-worker": "1.6.2"}
POOL_MAX = {"api-gateway": 10, "orders-service": 8, "fulfilment-worker": 4}
DEPENDENCIES = {
    "orders-service": ("service", ["api-gateway"]),
    "postgres": ("database", ["api-gateway", "orders-service", "fulfilment-worker"]),
    "redis": ("cache", ["api-gateway", "orders-service", "fulfilment-worker"]),
    "payments-provider": ("third_party", ["orders-service"]),
    "fulfilment-queue": ("queue", ["orders-service", "fulfilment-worker"]),
}

# Knowledge base entries the worlds can cite (ids/titles from knowledge/incidents/).
KB_INCIDENTS: dict[str, tuple[str, str, list[str]]] = {
    "INC-2026-031": (
        "Order history N+1 query after orders-service 2.1.0 saturated the DB pool",
        "bad_deploy",
        ["orders-service"],
    ),
    "INC-2026-044": (
        "Redis node unreachable: carts down, orders charged but not queued",
        "cache_failure",
        ["api-gateway", "orders-service", "fulfilment-worker"],
    ),
    "INC-2026-052": (
        "Fulfilment backlog after shipping-label client upgrade",
        "bad_deploy",
        ["fulfilment-worker"],
    ),
    "INC-2026-058": (
        "Payments provider degradation caused order placement failures",
        "dependency_failure",
        ["orders-service"],
    ),
    "INC-2026-063": (
        "Feature flag leaked database connections until the pool was exhausted",
        "config_change",
        ["orders-service"],
    ),
    "INC-2026-071": (
        "api-gateway memory leak from an unbounded in-process cache (OOM restart loop)",
        "bad_deploy",
        ["api-gateway"],
    ),
    "INC-2026-077": (
        "Failed canary of api-gateway left broken replicas serving 25% of traffic",
        "failed_deploy",
        ["api-gateway"],
    ),
    "INC-2026-085": (
        "Network latency to the database host slowed every query",
        "infrastructure",
        ["api-gateway", "orders-service"],
    ),
    "INC-2026-019": (
        "Black Friday traffic surge exhausted worker capacity",
        "capacity",
        ["fulfilment-worker"],
    ),
    "INC-2025-112": (
        "Checkout slow after orders-service index migration",
        "database_degradation",
        ["orders-service"],
    ),
}
RUNBOOKS = {
    "rb-rollback-procedure": "Procedure: rolling back a deployment > Steps",
    "rb-db-pool-exhaustion": "Runbook: database connection pool exhaustion > Remediation",
    "rb-db-network-latency": "Runbook: database network latency > Diagnosis",
    "rb-redis-outage": "Runbook: Redis (shop-cache) unavailable > Remediation",
    "rb-payments-provider": "Runbook: payments provider degradation > Remediation",
    "rb-worker-backlog": "Runbook: fulfilment worker backlog > Remediation",
    "rb-memory-leak": "Runbook: memory leak and OOM restarts > Remediation",
}
# Default knowledge hits per fault type: (similar incident, runbook).
FAULT_KNOWLEDGE = {
    "slow_db_queries": ("INC-2026-031", "rb-db-pool-exhaustion"),
    "db_pool_exhaustion": ("INC-2026-063", "rb-db-pool-exhaustion"),
    "db_network_latency": ("INC-2026-085", "rb-db-network-latency"),
    "redis_failure": ("INC-2026-044", "rb-redis-outage"),
    "dependency_errors": ("INC-2026-058", "rb-payments-provider"),
    "dependency_outage": ("INC-2026-058", "rb-payments-provider"),
    "worker_backlog": ("INC-2026-052", "rb-worker-backlog"),
    "failed_deployment": ("INC-2026-077", "rb-rollback-procedure"),
    "memory_pressure": ("INC-2026-071", "rb-memory-leak"),
    "traffic_surge": ("INC-2026-019", None),
}


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


@dataclass
class Rise:
    service: str
    signal: str
    onset: datetime
    baseline: float
    current: float


@dataclass
class Telemetry:
    """What the injected faults make observable, as of ``now``."""

    now: datetime
    versions: dict[str, str] = field(default_factory=lambda: dict(BASE_VERSIONS))
    signals: dict[str, dict[str, float]] = field(
        default_factory=lambda: {s: dict(v) for s, v in HEALTHY.items()}
    )
    anomalies: list[dict[str, Any]] = field(default_factory=list)
    rises: list[Rise] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    deps: dict[str, dict[str, Any]] = field(default_factory=dict)
    services: dict[str, dict[str, Any]] = field(default_factory=dict)
    pools: dict[str, dict[str, Any]] = field(default_factory=dict)
    idle_in_transaction: int = 0
    sessions: list[dict[str, Any]] = field(default_factory=list)
    slow_queries: list[dict[str, Any]] = field(default_factory=list)
    client_latency: list[dict[str, Any]] = field(default_factory=list)
    resources: dict[str, dict[str, Any]] = field(default_factory=dict)
    similar: list[str] = field(default_factory=list)
    runbooks: list[str] = field(default_factory=list)

    def ago(self, minutes: float) -> datetime:
        return self.now - timedelta(minutes=minutes)

    # ------------------------------------------------------------------ primitives
    def anomaly(self, svc: str, signal: str, value: float, onset_min: float) -> None:
        base = HEALTHY[svc].get(signal, 0.0)
        self.signals[svc][signal] = value
        onset = self.ago(onset_min)
        change = round((value - base) / base * 100, 1) if base else None
        self.anomalies.append(
            {
                "service": svc,
                "signal": signal,
                "unit": UNITS.get(signal, ""),
                "baseline_avg": base,
                "recent_avg": value,
                "recent_max": value * 1.3,
                "change_pct": change,
                "z_score": 25.0 if base else None,
                "onset": _iso(onset),
            }
        )
        self.rises.append(Rise(svc, signal, onset, base, value))

    def error(
        self,
        svc: str,
        template: str,
        count: int,
        onset_min: float,
        *,
        error_type: str | None = None,
        example: str | None = None,
        version: str | None = None,
    ) -> None:
        self.errors.append(
            {
                "service": svc,
                "template": template,
                "error_type": error_type,
                "count": count,
                "first_seen": _iso(self.ago(onset_min)),
                "last_seen": _iso(self.ago(0.1)),
                "example": example or template.replace("<n>", "7"),
                "version": version,
            }
        )

    def degrade(self, svc: str, *alerts: str, health: str = "degraded", restarts: int = 0) -> None:
        cur = self.services.setdefault(svc, {"health": "healthy", "alerts": [], "restarts": 0})
        if health == "down" or cur["health"] == "healthy":
            cur["health"] = health
        cur["alerts"] = sorted({*cur["alerts"], *alerts})
        cur["restarts"] = max(cur["restarts"], restarts)

    def dep(self, name: str, health: str, *evidence: str) -> None:
        cur = self.deps.get(name)
        if cur and cur["health"] == "down":
            cur["evidence"] += list(evidence)
            return
        self.deps[name] = {"health": health, "evidence": list(evidence)}

    def know(self, fault_type: str) -> None:
        inc, rb = FAULT_KNOWLEDGE.get(fault_type, (None, None))
        if inc and inc not in self.similar:
            self.similar.append(inc)
        if rb and rb not in self.runbooks:
            self.runbooks.append(rb)

    # ------------------------------------------------------------------ fault effects
    def apply(self, fault: dict[str, Any]) -> None:
        kind = fault["type"]
        svc = fault.get("target")
        p = fault.get("params", {})
        onset = float(fault.get("onset_minutes_ago", 10))
        getattr(self, f"_fault_{kind}")(svc, p, onset)
        if fault.get("knowledge", True):
            self.know(kind)

    def _passthrough(self, onset: float, route: str = "GET /orders/<n>", code: str = "503") -> None:
        self.error(
            "api-gateway",
            f"orders-service returned HTTP <n> for {route}",
            150,
            onset,
            error_type="UpstreamError",
            example=f"orders-service returned HTTP {code} for {route.replace('<n>', '70904')}",
        )
        self.anomaly("api-gateway", "error_rate", 0.06, onset - 0.3)
        self.degrade("api-gateway", "HighErrorRate")
        self.dep("orders-service", "degraded", "gateway->orders error ratio 30%")

    def _pool_saturated(self, svc: str, idle_tx: int = 0) -> None:
        cap = POOL_MAX[svc]
        sessions = {"active": cap - idle_tx} if idle_tx < cap else {}
        if idle_tx:
            sessions["idle in transaction"] = idle_tx
        self.pools[svc] = {
            "in_use": cap,
            "max_connections": cap,
            "utilization": 1.0,
            "pending_acquires": 9,
            "acquire_timeouts_per_s": 0.3,
            "server_sessions": sessions,
        }
        self.signals[svc]["db_pool_utilization"] = 1.0
        self.signals[svc]["db_pool_pending"] = 9.0

    def _pool_timeouts(self, svc: str, onset: float) -> None:
        cap = POOL_MAX[svc]
        self.error(
            svc,
            "timed out after <n>.0s waiting for a database connection (pool size=<n>, in use=<n>, waiting=<n>)",
            200,
            onset,
            error_type="PoolTimeoutError",
            example=f"timed out after 2.0s waiting for a database connection (pool size={cap}, in use={cap}, waiting=9)",
            version=self.versions.get(svc),
        )

    def _fault_slow_db_queries(self, svc: str, p: dict[str, Any], onset: float) -> None:
        query = p.get("query", "SELECT * FROM order_items WHERE order_id::text = $1")
        self.anomaly(svc, "latency_p99", 6.0, onset)
        if svc in ("api-gateway", "orders-service"):
            self.anomaly(svc, "error_rate", 0.3, onset - 0.5)
        self._pool_saturated(svc)
        self._pool_timeouts(svc, onset - 0.5)
        self.slow_queries.append({"query": query, "mean_ms": 480.0, "calls": 900})
        self.client_latency.append(
            {
                "service": svc,
                "query": p.get("name", "get_order_items_for_history"),
                "p95_s": 1.4,
                "calls_per_s": 20,
            }
        )
        self.sessions.append(
            {"application": svc, "state": "active", "count": POOL_MAX[svc], "longest_query_s": 1.4}
        )
        self.degrade(svc, "DBPoolSaturated", "HighLatencyP99")
        self.dep("postgres", "degraded", "pool acquire timeouts 0.30/s", "client query p95 1.4s")
        if svc == "orders-service":
            self._passthrough(onset - 0.5)

    def _fault_db_pool_exhaustion(self, svc: str, p: dict[str, Any], onset: float) -> None:
        cap = POOL_MAX[svc]
        self.anomaly(svc, "latency_p99", 2.2, onset)
        if svc in ("api-gateway", "orders-service"):
            self.anomaly(svc, "error_rate", 0.25, onset - 0.5)
        self._pool_saturated(svc, idle_tx=cap)
        self._pool_timeouts(svc, onset - 0.5)
        self.idle_in_transaction += cap
        self.sessions.append(
            {
                "application": svc,
                "state": "idle in transaction",
                "count": cap,
                "longest_query_s": 0.0,
            }
        )
        self.degrade(svc, "DBPoolSaturated")
        self.dep("postgres", "degraded", f"{cap} sessions idle in transaction")
        if svc == "orders-service":
            self._passthrough(onset - 0.5)

    def _fault_db_network_latency(self, svc: str | None, p: dict[str, Any], onset: float) -> None:
        extra = float(p.get("latency_ms", 300)) / 1000
        queries = {
            "api-gateway": ["catalog_list", "cart_load", "session_lookup"],
            "orders-service": ["get_order", "insert_order", "get_order_items_for_history"],
            "fulfilment-worker": ["claim_job", "mark_shipped"],
        }
        for s, qs in queries.items():
            for q in qs:
                self.client_latency.append(
                    {
                        "service": s,
                        "query": q,
                        "p95_s": round(0.004 + extra * 3, 3),
                        "calls_per_s": 5,
                    }
                )
            self.signals[s]["db_query_p95"] = round(0.004 + extra * 3, 3)
        self.anomaly("api-gateway", "latency_p99", round(0.22 + extra * 4, 3), onset)
        self.anomaly("orders-service", "latency_p99", round(0.24 + extra * 5, 3), onset)
        self.sessions.append(
            {
                "application": "orders-service",
                "state": "active",
                "count": 2,
                "longest_query_s": 0.01,
            }
        )
        self.degrade("api-gateway", "HighLatencyP99")
        self.degrade("orders-service", "HighLatencyP99")
        self.dep(
            "postgres",
            "degraded",
            "pg_up=1.0",
            f"client query p95 {0.004 + extra * 3:.3f}s",
            "server CPU 4%",
        )

    def _fault_redis_failure(self, svc: str | None, p: dict[str, Any], onset: float) -> None:
        down = p.get("mode", "timeout") == "timeout"
        rate = 4.2 if down else 0.8
        for s in APP:
            self.anomaly(s, "redis_error_rate", rate, onset)
        self.dep(
            "redis",
            "down" if down else "degraded",
            "redis_up=0" if down else "redis_up=1",
            f"client errors {rate:.2f}/s",
        )
        self.warnings.append(
            {
                "service": "api-gateway",
                "message": "catalog cache unavailable, reading from database",
                "count": 400,
            }
        )
        self.error(
            "api-gateway",
            "GET /api/cart/<n> 503",
            300 if down else 60,
            onset,
            error_type="CacheUnavailableError",
            example="GET /api/cart/1210 503: shop-cache timeout after 0.5s",
        )
        self.error(
            "orders-service",
            "order <n> charged but not queued: fulfilment queue unavailable",
            120 if down else 20,
            onset,
            error_type="CacheUnavailableError",
            example="order 91821 charged but not queued: shop-cache timeout",
        )
        self.error(
            "fulfilment-worker",
            "cannot read from fulfilment queue",
            90 if down else 15,
            onset,
            error_type="CacheUnavailableError",
            example="cannot read from fulfilment queue: shop-cache timeout",
        )
        self.anomaly("api-gateway", "error_rate", 0.15 if down else 0.03, onset - 0.2)
        self.anomaly("orders-service", "error_rate", 0.2 if down else 0.04, onset - 0.2)
        for s in APP:
            self.degrade(s, "RedisErrors")

    def _payments(self, onset: float, ratio: float, health: str, evidence: str) -> None:
        self.dep("payments-provider", health, evidence, "p50 latency 2.400s")
        self.anomaly("orders-service", "upstream_error_rate", round(ratio * 2.5, 2), onset)
        self.anomaly("orders-service", "error_rate", ratio, onset - 0.2)
        self.error(
            "orders-service",
            "payments-provider returned HTTP <n> (charge)",
            int(400 * ratio),
            onset,
            error_type="UpstreamError",
            example="payments-provider returned HTTP 503 (charge order 88123)",
        )
        self._passthrough(onset - 0.3, route="POST /orders", code="502")
        self.degrade("orders-service", "PaymentsErrors")

    def _fault_dependency_errors(self, svc: str | None, p: dict[str, Any], onset: float) -> None:
        ratio = float(p.get("error_rate", 0.5))
        self._payments(onset, round(ratio * 0.7, 2), "degraded", f"error ratio {ratio * 100:.0f}%")

    def _fault_dependency_outage(self, svc: str | None, p: dict[str, Any], onset: float) -> None:
        self._payments(onset, 0.9, "down", "connection refused")

    def _fault_worker_backlog(self, svc: str | None, p: dict[str, Any], onset: float) -> None:
        failure = float(p.get("failure_rate", 0.5))
        self.anomaly("fulfilment-worker", "queue_depth", 350.0, onset)
        self.anomaly("fulfilment-worker", "worker_failure_rate", round(failure * 0.8, 2), onset)
        self.error(
            "fulfilment-worker",
            "shipping label request timed out after <n>s",
            int(300 * failure),
            onset,
            error_type="LabelServiceTimeout",
            example="shipping label request timed out after 2s (job 4411)",
            version=self.versions.get("fulfilment-worker"),
        )
        self.dep("fulfilment-queue", "degraded", "depth 350.0", "oldest job age 900.0s")
        self.degrade("fulfilment-worker", "QueueBacklog")

    def _fault_failed_deployment(self, svc: str, p: dict[str, Any], onset: float) -> None:
        version = p["version"]
        self.anomaly(svc, "error_rate", 0.25, onset)
        self.error(
            svc,
            "invalid configuration value for <n>: canary replica cannot serve requests",
            320,
            onset,
            error_type="ConfigError",
            example="invalid configuration value for upstream_timeout: canary replica cannot serve requests",
            version=version,
        )
        self.degrade(svc, "HighErrorRate")

    def _fault_memory_pressure(self, svc: str, p: dict[str, Any], onset: float) -> None:
        growth = float(p.get("mb_per_second", 0.7)) * 60
        self.resources[svc] = {
            "memory_growth_mib_per_min": growth,
            "memory_utilization": 0.93,
            "oom_risk": True,
            "restarts": int(p.get("restarts", 3)),
        }
        self.anomaly(svc, "memory_bytes", 240e6, onset)
        self.anomaly(svc, "latency_p99", 0.7, onset - 2)
        self.degrade(svc, "MemoryHigh", restarts=int(p.get("restarts", 3)))

    def _fault_http_errors(self, svc: str, p: dict[str, Any], onset: float) -> None:
        rate = float(p.get("rate", 0.1))
        self.anomaly(svc, "error_rate", rate, onset)
        self.error(
            svc, "GET <n> 500 Internal Server Error", int(1000 * rate), onset, error_type=None
        )
        self.degrade(svc, "HighErrorRate")

    def _fault_api_latency(self, svc: str, p: dict[str, Any], onset: float) -> None:
        delay = float(p.get("delay_ms", 400)) / 1000
        self.anomaly(svc, "latency_p99", round(HEALTHY[svc]["latency_p99"] + delay, 3), onset)
        self.anomaly(svc, "latency_p95", round(HEALTHY[svc]["latency_p95"] + delay * 0.8, 3), onset)

    def _fault_traffic_surge(self, svc: str | None, p: dict[str, Any], onset: float) -> None:
        m = float(p.get("multiplier", 3))
        self.anomaly("api-gateway", "request_rate", 10.0 * m, onset)
        self.anomaly("orders-service", "request_rate", round(2.9 * m, 2), onset)
        self.anomaly("api-gateway", "latency_p99", 0.9, onset - 1)
        self.signals["api-gateway"]["cpu"] = 0.6
        self.signals["orders-service"]["db_pool_utilization"] = 0.85
        self.degrade("api-gateway", "HighLatencyP99")


@dataclass
class Unavailable:
    tools: set[str]
    flaky: set[str]


class WorldGateway:
    """Duck-types :class:`ai_sre.agent.gateway.ToolGateway` over a scenario world.

    Mirrors the real gateway's semantics: transient failures are retried once, every call is an
    :class:`Evidence` record, and every call produces an OpenTelemetry tool span.
    """

    def __init__(self, world: dict[str, Any], *, disabled_tools: set[str] | None = None) -> None:
        self.now = datetime.now(UTC)
        self.world = world
        self.t = build_telemetry(world, self.now)
        self.unavailable = Unavailable(
            set(world.get("unavailable", [])) | (disabled_tools or set()),
            set(world.get("flaky", [])),
        )
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self._counter = 0
        self._flaky_seen: set[str] = set()

    # ToolGateway interface ----------------------------------------------------
    def next_id(self) -> str:
        self._counter += 1
        return f"ev-{self._counter:03d}"

    def resume_numbering(self, existing: int) -> None:
        self._counter = max(self._counter, existing)

    async def registry(self) -> dict[str, Any] | None:
        names = [
            "logs",
            "metrics",
            "deployments",
            "database",
            "infrastructure",
            "incidents",
            "knowledge",
        ]
        servers = [{"name": n, "enabled": True} for n in names]
        # Evaluations score proposals; they never execute remediation.
        return {"servers": [*servers, {"name": "remediation", "enabled": False}]}

    async def call(self, server: str, tool: str, args: dict[str, Any], round_: int) -> Evidence:
        key = f"{server}.{tool}"
        self.calls.append((server, tool, args))
        started = time.perf_counter()
        with tool_span(server, tool, args, round_) as span:
            attempts = 1
            code: str | None = None
            data: dict[str, Any] | None = None
            if key in self.unavailable.tools or f"{server}.*" in self.unavailable.tools:
                attempts, code = 2, "backend_unavailable"  # transient: retried once, still down
            else:
                if key in self.unavailable.flaky and key not in self._flaky_seen:
                    self._flaky_seen.add(key)
                    attempts = 2  # first attempt timed out, the retry succeeded
                handler = getattr(self, f"_{tool}", None)
                if handler is None:
                    code = "not_found"
                else:
                    try:
                        data = {"tool": tool, **handler(args)}
                    except KeyError as exc:
                        code, data = "not_found", None
                        args = {**args, "_error": str(exc)}
            ev = Evidence(
                id=self.next_id(),
                round=round_,
                server=server,
                tool=tool,
                arguments=args,
                ok=code is None,
                summary=str((data or {}).get("summary", ""))
                if code is None
                else f"FAILED ({code}): {tool} unavailable",
                data=data,
                error_code=code,
                error_message=None if code is None else f"{key}: {code}",
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
                at=datetime.now(UTC),
            )
            finish_tool_span(
                span,
                evidence_id=ev.id,
                ok=ev.ok,
                attempts=attempts,
                error_code=code,
                result_bytes=len(json.dumps(data, default=str)) if data else 0,
            )
            return ev

    async def call_remediation(self, tool: str, args: dict[str, Any], round_: int) -> Evidence:
        raise RuntimeError("evaluation worlds never execute remediation")

    # Tool handlers (payload shapes of ai_sre.mcp_servers) ---------------------
    def _detect_metric_anomalies(self, a: dict[str, Any]) -> dict[str, Any]:
        services = set(a.get("services") or APP)
        since = self.now - timedelta(minutes=float(a.get("since_minutes", 30)))
        found = [
            x
            for x in self.t.anomalies
            if x["service"] in services
            and datetime.fromisoformat(x["onset"].replace("Z", "+00:00")) >= since
        ]
        desc = "; ".join(f"{x['service']} {x['signal']} {x['change_pct']}%" for x in found[:4])
        return {
            "summary": f"{len(found)} anomalies in {len(services) * 6} signals: {desc}",
            "anomalies": found,
        }

    def _deployments(self) -> list[dict[str, Any]]:
        out = []
        for d in self.world.get("deployments", []):
            started = self.t.ago(float(d["minutes_ago"]))
            out.append(
                {
                    "id": d["id"],
                    "service": d["service"],
                    "kind": d.get("kind", "deploy"),
                    "version": d["version"],
                    "previous_version": d.get("previous_version"),
                    "status": d.get("status", "succeeded"),
                    "started_at": _iso(started),
                    "finished_at": _iso(started + timedelta(seconds=d.get("duration_s", 30))),
                    "changes_summary": d.get("changes_summary", "Routine release"),
                    "author": d.get("author", "ci-bot"),
                    "commit_sha": d.get("commit_sha", "0" * 12),
                    "failure_reason": d.get("failure_reason"),
                }
            )
        return sorted(out, key=lambda r: r["started_at"], reverse=True)

    def _list_recent_deployments(self, a: dict[str, Any]) -> dict[str, Any]:
        since = _iso(self.now - timedelta(hours=float(a.get("since_hours", 2))))
        rows = [
            d
            for d in self._deployments()
            if d["started_at"] >= since and (not a.get("service") or d["service"] == a["service"])
        ]
        rows = rows[: int(a.get("limit", 20))]
        latest = (
            f"; latest {rows[0]['id']} {rows[0]['service']} {rows[0]['version']} ({rows[0]['status']})"
            if rows
            else ""
        )
        return {
            "summary": f"{len(rows)} deployments in the last {a.get('since_hours', 2)} h{latest}",
            "count": len(rows),
            "deployments": rows,
        }

    def _get_deployment_details(self, a: dict[str, Any]) -> dict[str, Any]:
        dep = next((d for d in self._deployments() if d["id"] == a["deployment_id"]), None)
        if dep is None:
            raise KeyError(a["deployment_id"])
        return {
            "summary": f"{dep['id']}: {dep['service']} {dep['previous_version']} -> {dep['version']} ({dep['kind']}, {dep['status']})",
            "deployment": dep,
        }

    def _aggregate_errors(self, a: dict[str, Any]) -> dict[str, Any]:
        sigs = [s for s in self.t.errors if not a.get("service") or s["service"] == a["service"]]
        sigs = sorted(sigs, key=lambda s: s["count"], reverse=True)[: int(a.get("top_k", 10))]
        by_service: dict[str, int] = {}
        for s in self.t.errors:
            by_service[s["service"]] = by_service.get(s["service"], 0) + s["count"]
        total = sum(by_service.values())
        top = f"; top: '{sigs[0]['template']}' x{sigs[0]['count']}" if sigs else ""
        return {
            "summary": f"{total} errors in {a.get('since_minutes', 30)} min across {len(by_service)} services{top}",
            "total_errors": total,
            "by_service": by_service,
            "signatures": [{k: v for k, v in s.items() if k != "version"} for s in sigs],
        }

    def _entries(
        self, service: str | None, limit: int, contains: str | None = None, level: str | None = None
    ) -> list[dict[str, Any]]:
        lines: list[dict[str, Any]] = []
        rows = [(s, "ERROR") for s in self.t.errors] + [
            (
                {
                    **w,
                    "template": w["message"],
                    "example": w["message"],
                    "error_type": None,
                    "version": None,
                    "first_seen": _iso(self.t.ago(5)),
                },
                "WARNING",
            )
            for w in self.t.warnings
        ]
        for s, sev in rows:
            if service and s["service"] != service:
                continue
            if (
                level
                and sev != level.upper()
                and not (level.upper() == "WARNING" and sev == "ERROR")
            ):
                continue
            if contains and contains.lower() not in (s["example"] + s["template"]).lower():
                continue
            for i in range(min(5, s.get("count", 1))):
                lines.append(
                    {
                        "timestamp": _iso(self.t.ago(i * 0.2)),
                        "service": s["service"],
                        "severity": sev,
                        "message": s["example"],
                        "endpoint": None,
                        "version": s.get("version") or self.t.versions.get(s["service"]),
                        "trace_id": hashlib.sha256(f"{s['template']}{i}".encode()).hexdigest()[:16],
                        "error_type": s.get("error_type"),
                        "error_message": None,
                    }
                )
        return lines[:limit]

    def _get_error_logs(self, a: dict[str, Any]) -> dict[str, Any]:
        entries = self._entries(a.get("service"), int(a.get("limit", 30)))
        return {
            "summary": f"{len(entries)} error lines in the last {a.get('since_minutes', 30)} min for {a.get('service') or 'all services'}",
            "entries": entries,
        }

    def _search_logs(self, a: dict[str, Any]) -> dict[str, Any]:
        entries = self._entries(
            a.get("service"), int(a.get("limit", 20)), a.get("contains"), a.get("level")
        )
        return {
            "summary": f"{len(entries)} log lines matching {a.get('contains')!r}",
            "entries": entries,
        }

    def _get_logs_by_trace_id(self, a: dict[str, Any]) -> dict[str, Any]:
        return {"summary": "0 log lines for this trace", "entries": []}

    def _get_dependency_status(self, a: dict[str, Any]) -> dict[str, Any]:
        defaults = {
            "orders-service": ["gateway->orders error ratio 0.0% (2.91 req/s)"],
            "postgres": ["pg_up=1.0", "pool acquire timeouts 0.00/s", "client query p95 0.005s"],
            "redis": ["redis_up=1.0", "client errors 0.00/s"],
            "payments-provider": ["error ratio 0.0%", "p50 latency 0.090s"],
            "fulfilment-queue": ["depth 0.0", "oldest job age 0.0s"],
        }
        deps = []
        for name, (kind, used_by) in DEPENDENCIES.items():
            st = self.t.deps.get(name, {"health": "healthy", "evidence": defaults[name]})
            deps.append(
                {
                    "name": name,
                    "kind": kind,
                    "used_by": used_by,
                    "health": st["health"],
                    "evidence": st["evidence"],
                }
            )
        bad = [d["name"] + "=" + d["health"] for d in deps if d["health"] != "healthy"]
        return {"summary": "; ".join(bad) or "all dependencies healthy", "dependencies": deps}

    def _get_service_status(self, a: dict[str, Any]) -> dict[str, Any]:
        names = [a["service"]] if a.get("service") else [*APP, "deployer", "postgres", "redis"]
        out = []
        for n in names:
            st = self.t.services.get(n, {"health": "healthy", "alerts": [], "restarts": 0})
            if n == "redis" and self.t.deps.get("redis", {}).get("health") == "down":
                st = {"health": "down", "alerts": ["RedisDown"], "restarts": 0}
            out.append(
                {
                    "service": n,
                    "up": st["health"] != "down",
                    "version": self.t.versions.get(n),
                    "uptime_s": 8600.0,
                    "restarts_last_hour": st["restarts"],
                    "firing_alerts": st["alerts"],
                    "health": st["health"],
                }
            )
        bad = [f"{s['service']}={s['health']}" for s in out if s["health"] != "healthy"]
        return {"summary": "; ".join(bad) or "all services healthy", "services": out}

    def _get_connection_pool_status(self, a: dict[str, Any]) -> dict[str, Any]:
        pools = []
        for s in APP:
            if a.get("service") and a["service"] != s:
                continue
            pools.append(
                {
                    "service": s,
                    **self.t.pools.get(
                        s,
                        {
                            "in_use": 0,
                            "max_connections": POOL_MAX[s],
                            "utilization": 0.0,
                            "pending_acquires": 0,
                            "acquire_timeouts_per_s": 0.0,
                            "server_sessions": {},
                        },
                    ),
                }
            )
        desc = ", ".join(f"{p['service']} {p['in_use']}/{p['max_connections']}" for p in pools)
        return {"summary": f"pools in use: {desc}", "pools": pools}

    def _get_database_health(self, a: dict[str, Any]) -> dict[str, Any]:
        sessions = self.t.sessions or [
            {
                "application": "orders-service",
                "state": "active",
                "count": 1,
                "longest_query_s": 0.01,
            }
        ]
        return {
            "summary": f"{sum(s['count'] for s in sessions)} connections, {self.t.idle_in_transaction} idle in transaction",
            "idle_in_transaction": self.t.idle_in_transaction,
            "sessions": sessions,
            "locks_waiting": 0,
        }

    def _get_slow_queries(self, a: dict[str, Any]) -> dict[str, Any]:
        healthy = [{"query": "SELECT * FROM orders WHERE id = $1", "mean_ms": 0.8, "calls": 5000}]
        queries = sorted(self.t.slow_queries + healthy, key=lambda q: q["mean_ms"], reverse=True)
        return {
            "summary": f"{len(queries)} statements by mean_time; top: {queries[0]['query'][:60]!r} mean {queries[0]['mean_ms']} ms",
            "queries": queries[: int(a.get("top_k", 5))],
        }

    def _get_database_metrics(self, a: dict[str, Any]) -> dict[str, Any]:
        rows = self.t.client_latency or [
            {"service": "orders-service", "query": "get_order", "p95_s": 0.004, "calls_per_s": 5}
        ]
        slow = sorted(rows, key=lambda r: r["p95_s"], reverse=True)
        return {
            "summary": f"slowest {slow[0]['service']}/{slow[0]['query']} p95 {slow[0]['p95_s']}s",
            "client_query_latency": slow,
        }

    def _get_service_metrics(self, a: dict[str, Any]) -> dict[str, Any]:
        svc = a["service"]
        if svc not in self.t.signals:
            raise KeyError(svc)
        sig = self.t.signals[svc]
        brief = ", ".join(
            f"{k}={v:.3g}"
            for k, v in sig.items()
            if k
            in ("request_rate", "error_rate", "latency_p99", "db_pool_utilization", "queue_depth")
        )
        return {
            "summary": f"{svc} {self.t.versions.get(svc)}: {brief}",
            "service": svc,
            "version": self.t.versions.get(svc),
            "signals": [
                {"signal": k, "unit": UNITS.get(k, ""), "value": v} for k, v in sig.items()
            ],
        }

    def _compare_metric_windows(self, a: dict[str, Any]) -> dict[str, Any]:
        svc, signal = a["service"], a["signal"]
        split = datetime.fromisoformat(
            str(a.get("split_at") or _iso(self.t.ago(10))).replace("Z", "+00:00")
        )
        base = HEALTHY.get(svc, {}).get(signal, 0.0)
        rise = next((r for r in self.t.rises if r.service == svc and r.signal == signal), None)
        if rise and rise.onset >= split - timedelta(minutes=1):
            before, after, direction = rise.baseline, rise.current, "up"
        elif rise:  # already degraded before the split: no change at the split
            before, after, direction = rise.current, rise.current, "flat"
        else:
            before, after, direction = base, base, "flat"
        change = round((after - before) / before * 100, 1) if before else None
        return {
            "summary": f"{signal} on {svc}: baseline avg {before} -> current avg {after} ({direction})",
            "service": svc,
            "signal": signal,
            "baseline": {
                "start": _iso(split - timedelta(minutes=15)),
                "end": _iso(split),
                "stats": {"avg": before, "max": before},
            },
            "current": {
                "start": _iso(split),
                "end": _iso(self.now),
                "stats": {"avg": after, "max": after},
            },
            "direction": direction,
            "change_pct": change,
        }

    def _get_resource_usage(self, a: dict[str, Any]) -> dict[str, Any]:
        usage = []
        for s in APP:
            if a.get("service") and a["service"] != s:
                continue
            r = self.t.resources.get(s, {})
            usage.append(
                {
                    "service": s,
                    "cpu_cores": self.t.signals[s]["cpu"],
                    "memory_bytes": self.t.signals[s]["memory_bytes"],
                    "memory_growth_bytes_per_min": r.get("memory_growth_mib_per_min", 0.0) * 2**20,
                }
            )
        return {
            "summary": "; ".join(
                f"{u['service']} mem growth {u['memory_growth_bytes_per_min'] / 2**20:.0f} MiB/min"
                for u in usage
            ),
            "usage": usage,
        }

    def _get_container_health(self, a: dict[str, Any]) -> dict[str, Any]:
        out = []
        for s in APP:
            if a.get("service") and a["service"] != s:
                continue
            r = self.t.resources.get(s, {})
            out.append(
                {
                    "service": s,
                    "up": True,
                    "memory_utilization": r.get("memory_utilization", 0.28),
                    "oom_risk": r.get("oom_risk", False),
                    "restarts_last_hour": r.get("restarts", 0),
                }
            )
        return {"summary": f"{len(out)} containers", "containers": out}

    def _hits(self) -> list[dict[str, Any]]:
        hits: list[dict[str, Any]] = []
        for i, inc in enumerate(self.t.similar):
            title, category, services = KB_INCIDENTS[inc]
            hits.append(
                {
                    "citation": {
                        "document_id": inc,
                        "source": f"incidents/{inc}.md",
                        "section": f"{inc}: {title} > What happened",
                        "incident_id": inc,
                        "title": title,
                    },
                    "doc_type": "incident",
                    "category": category,
                    "services": services,
                    "score": round(0.033 - i * 0.004, 4),
                    "similarity": round(0.82 - i * 0.07, 2),
                    "superseded_by": None,
                }
            )
        return hits

    def _find_similar_incidents(self, a: dict[str, Any]) -> dict[str, Any]:
        hits = self._hits()
        confident = bool(hits) and bool(self.world.get("knowledge_confident", True))
        return {
            "summary": f"{len(hits)} similar incidents (confident={confident})"
            + (f"; best: {hits[0]['citation']['incident_id']}" if hits else ""),
            "confident": confident,
            "hits": hits,
        }

    def _search_incident_history(self, a: dict[str, Any]) -> dict[str, Any]:
        hits = [
            {
                "incident": {
                    "id": h["citation"]["incident_id"],
                    "category": h["category"],
                    "services": h["services"],
                    "title": h["citation"]["title"],
                },
                "score": 9.0 - i,
            }
            for i, h in enumerate(self._hits())
        ]
        return {"summary": f"{len(hits)} past incidents", "hits": hits}

    def _search_knowledge(self, a: dict[str, Any]) -> dict[str, Any]:
        hits: list[dict[str, Any]] = [
            {
                "citation": {
                    "document_id": rb,
                    "source": f"runbooks/{rb}.md",
                    "section": RUNBOOKS[rb],
                    "incident_id": None,
                    "title": RUNBOOKS[rb].split(" > ")[0],
                },
                "doc_type": "runbook",
                "category": None,
                "services": [],
                "score": 0.03,
                "similarity": 0.8,
                "superseded_by": None,
            }
            for rb in self.t.runbooks
        ]
        return {
            "summary": f"{len(hits)} results (confident={bool(hits)})",
            "confident": bool(hits),
            "hits": hits,
        }

    def _compare_with_incident(self, a: dict[str, Any]) -> dict[str, Any]:
        inc = a.get("incident_id") or a.get("document_id") or ""
        if inc not in KB_INCIDENTS:
            raise KeyError(inc)
        title, category, services = KB_INCIDENTS[inc]
        return {
            "summary": f"{inc}: shared services {services}",
            "incident": {
                "document_id": inc,
                "source": f"incidents/{inc}.md",
                "section": "(whole document)",
                "incident_id": inc,
                "title": title,
            },
            "category": category,
            "shared_services": services,
            "matches": [],
            "match_ratio": 0.5 if inc in self.t.similar[:1] else 0.2,
        }


def build_telemetry(world: dict[str, Any], now: datetime) -> Telemetry:
    t = Telemetry(now=now)
    for d in sorted(world.get("deployments", []), key=lambda d: -float(d["minutes_ago"])):
        if (
            d.get("status", "succeeded") == "succeeded"
            and d.get("kind", "deploy") != "config_change"
        ):
            t.versions[d["service"]] = d["version"]
    for fault in world.get("faults", []):
        t.apply(fault)
    o = world.get("overrides", {})
    for name, st in o.get("dependencies", {}).items():
        t.deps[name] = st
    for name, st in o.get("services", {}).items():
        t.services[name] = st
    for name, st in o.get("pools", {}).items():
        t.pools[name] = st
    for e in o.get("errors", []):
        t.error(
            e["service"],
            e["template"],
            e["count"],
            e.get("onset_minutes_ago", 10),
            error_type=e.get("error_type"),
        )
    for a in o.get("anomalies", []):
        t.anomaly(a["service"], a["signal"], a["value"], a.get("onset_minutes_ago", 10))
    if "similar_incidents" in o:
        t.similar = list(o["similar_incidents"])
    if "runbooks" in o:
        t.runbooks = list(o["runbooks"])
    return t
