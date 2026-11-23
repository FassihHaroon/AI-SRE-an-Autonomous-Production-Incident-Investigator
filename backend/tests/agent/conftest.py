"""Fixtures for graph tests: a fake tool gateway with canned, schema-shaped MCP results."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from ai_sre.agent.models import Evidence

NOW = datetime.now(UTC)


def iso(minutes_ago: float) -> str:
    return (NOW - timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z")


def scenario_a() -> dict[str, Callable[[dict[str, Any]], dict[str, Any]]]:
    """Tool outputs resembling scenario A (orders-service 2.4.0 N+1 deploy, gateway red herring)."""
    deploys = [
        {
            "id": "d-408",
            "service": "orders-service",
            "kind": "deploy",
            "version": "2.4.0",
            "previous_version": "2.3.1",
            "status": "succeeded",
            "started_at": iso(12.1),
            "finished_at": iso(12),
            "author": "m.okafor",
            "changes_summary": "Order history: include line items",
            "commit_sha": "abc",
            "failure_reason": None,
        },
        {
            "id": "d-407",
            "service": "api-gateway",
            "kind": "deploy",
            "version": "3.8.1",
            "previous_version": "3.8.0",
            "status": "succeeded",
            "started_at": iso(16.1),
            "finished_at": iso(16),
            "author": "a.rahman",
            "changes_summary": "Banner copy",
            "commit_sha": "def",
            "failure_reason": None,
        },
    ]
    return {
        "detect_metric_anomalies": lambda a: {
            "summary": "2 anomalies",
            "anomalies": [
                {
                    "service": "orders-service",
                    "signal": "latency_p99",
                    "unit": "s",
                    "baseline_avg": 0.3,
                    "recent_avg": 6.0,
                    "recent_max": 10.0,
                    "change_pct": 1900.0,
                    "z_score": 40.0,
                    "onset": iso(11),
                },
                {
                    "service": "api-gateway",
                    "signal": "error_rate",
                    "unit": "ratio",
                    "baseline_avg": 0.0,
                    "recent_avg": 0.06,
                    "recent_max": 0.1,
                    "change_pct": None,
                    "z_score": None,
                    "onset": iso(10.5),
                },
            ],
        },
        "list_recent_deployments": lambda a: {"summary": "2 deployments", "deployments": deploys},
        "get_deployment_details": lambda a: {
            "summary": a["deployment_id"],
            "deployment": next(d for d in deploys if d["id"] == a["deployment_id"]),
        },
        "aggregate_errors": lambda a: {
            "summary": "errors",
            "by_service": {"orders-service": 300, "api-gateway": 250},
            "signatures": [
                {
                    "service": "orders-service",
                    "template": "timed out after <n>.0s waiting for a database connection",
                    "error_type": "PoolTimeoutError",
                    "count": 200,
                    "first_seen": iso(10),
                    "last_seen": iso(0.1),
                    "example": "timed out after 2.0s waiting for a database connection (pool size=8, in use=8, waiting=9)",
                },
                {
                    "service": "api-gateway",
                    "template": "orders-service returned HTTP <n> for GET /orders/<n>",
                    "error_type": "UpstreamError",
                    "count": 150,
                    "first_seen": iso(10),
                    "last_seen": iso(0.1),
                    "example": "orders-service returned HTTP 503 ...",
                },
            ],
        },
        "get_dependency_status": lambda a: {
            "summary": "orders degraded",
            "dependencies": [
                {"name": "orders-service", "health": "degraded", "evidence": ["error ratio 30%"]},
                {
                    "name": "postgres",
                    "health": "degraded",
                    "evidence": ["pool acquire timeouts 0.3/s"],
                },
                {"name": "redis", "health": "healthy", "evidence": ["redis_up=1"]},
                {"name": "payments-provider", "health": "healthy", "evidence": ["error ratio 0%"]},
            ],
        },
        "get_service_status": lambda a: {
            "summary": "degraded",
            "services": [
                {
                    "service": "orders-service",
                    "health": "degraded",
                    "firing_alerts": ["DBPoolSaturated"],
                    "version": "2.4.0",
                    "restarts_last_hour": 0,
                }
            ],
        },
        "get_connection_pool_status": lambda a: {
            "summary": "pools",
            "pools": [
                {
                    "service": "orders-service",
                    "in_use": 8,
                    "max_connections": 8,
                    "utilization": 1.0,
                    "pending_acquires": 9,
                    "acquire_timeouts_per_s": 0.3,
                    "server_sessions": {"active": 8},
                },
                {
                    "service": "api-gateway",
                    "in_use": 0,
                    "max_connections": 10,
                    "utilization": 0.0,
                    "pending_acquires": 0,
                    "acquire_timeouts_per_s": 0,
                    "server_sessions": {},
                },
            ],
        },
        "search_incident_history": lambda a: {
            "summary": "1 hit",
            "hits": [
                {
                    "incident": {
                        "id": "INC-2026-031",
                        "category": "bad_deploy",
                        "services": ["orders-service"],
                        "title": "N+1 query",
                    },
                    "score": 9.0,
                }
            ],
        },
        "get_service_metrics": lambda a: {
            "summary": a["service"],
            "service": a["service"],
            "signals": [
                {
                    "signal": "error_rate",
                    "value": 0.3 if a["service"] == "orders-service" else 0.06,
                },
                {"signal": "latency_p99", "value": 10.0},
                {
                    "signal": "db_pool_utilization",
                    "value": 1.0 if a["service"] == "orders-service" else 0.0,
                },
            ],
        },
        "get_database_health": lambda a: {
            "summary": "ok",
            "idle_in_transaction": 0,
            "sessions": [
                {
                    "application": "orders-service",
                    "state": "active",
                    "count": 8,
                    "longest_query_s": 1.4,
                }
            ],
        },
        "get_slow_queries": lambda a: {
            "summary": "slow",
            "queries": [
                {
                    "query": "SELECT * FROM order_items WHERE order_id::text = $1",
                    "mean_ms": 480.0,
                    "calls": 900,
                }
            ],
        },
        "get_database_metrics": lambda a: {
            "summary": "db",
            "client_query_latency": [
                {
                    "service": "orders-service",
                    "query": "get_order_items_for_history",
                    "p95_s": 1.4,
                    "calls_per_s": 20,
                }
            ],
        },
        "get_error_logs": lambda a: {
            "summary": "errors",
            "entries": [
                {
                    "service": a.get("service") or "orders-service",
                    "version": "2.4.0",
                    "error_type": "PoolTimeoutError",
                }
            ],
        },
        "get_resource_usage": lambda a: {
            "summary": "ok",
            "usage": [{"service": a.get("service"), "memory_growth_bytes_per_min": 0}],
        },
    }


class FakeGateway:
    """Duck-types ToolGateway. Records calls; failing tools return failed evidence."""

    def __init__(
        self,
        data: dict[str, Callable[[dict[str, Any]], dict[str, Any]]],
        *,
        fail: set[str] | None = None,
        remediation: str = "ok",
        registry: bool = True,
    ) -> None:
        self.data = data
        self.fail = fail or set()
        self.remediation = remediation
        self.has_registry = registry
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.counter = 0

    def next_id(self) -> str:
        self.counter += 1
        return f"ev-{self.counter:03d}"

    def resume_numbering(self, existing: int) -> None:
        self.counter = max(self.counter, existing)

    async def registry(self) -> dict[str, Any] | None:
        if not self.has_registry:
            return None
        servers = ["logs", "metrics", "deployments", "database", "infrastructure", "incidents"]
        return {
            "servers": [{"name": n, "enabled": True} for n in servers]
            + [{"name": "remediation", "enabled": self.remediation != "disabled"}]
        }

    def _ev(
        self,
        server: str,
        tool: str,
        args: dict[str, Any],
        round_: int,
        data: dict[str, Any] | None,
        code: str | None = None,
    ) -> Evidence:
        return Evidence(
            id=self.next_id(),
            round=round_,
            server=server,
            tool=tool,
            arguments=args,
            ok=code is None,
            summary=(data or {}).get("summary", f"FAILED ({code})"),
            data=data,
            error_code=code,
            error_message=None if code is None else f"{tool} failed",
            duration_ms=1.0,
            at=datetime.now(UTC),
        )

    async def call(self, server: str, tool: str, args: dict[str, Any], round_: int) -> Evidence:
        self.calls.append((server, tool, args))
        if tool in self.fail or tool not in self.data:
            return self._ev(server, tool, args, round_, None, "backend_unavailable")
        return self._ev(server, tool, args, round_, {"tool": tool, **self.data[tool](args)})

    async def call_remediation(self, tool: str, args: dict[str, Any], round_: int) -> Evidence:
        self.calls.append(("remediation", tool, args))
        if self.remediation == "fail" and "approval_token" in args:
            return self._ev("remediation", tool, args, round_, None, "action_failed")
        if "approval_token" in args:
            return self._ev(
                "remediation",
                tool,
                args,
                round_,
                {"status": "executed", "summary": "done", "result": {"kind": "rollback"}},
            )
        return self._ev(
            "remediation",
            tool,
            args,
            round_,
            {
                "status": "approval_required",
                "summary": "approve",
                "approval_request_id": "apr-test",
            },
        )


@pytest.fixture
def gateway_factory() -> Callable[..., FakeGateway]:
    return lambda **kw: FakeGateway(scenario_a(), **kw)
