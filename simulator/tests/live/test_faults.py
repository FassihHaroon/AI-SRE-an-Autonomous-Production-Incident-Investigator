"""Every fault primitive changes the telemetry when injected and is fully reversible."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from ai_sre_simulator.testing import WINDOW, Sim

pytestmark = pytest.mark.sim


def rate(expr: str) -> str:
    return f"sum(rate({expr}[{WINDOW}]))"


def p50(metric: str, selector: str) -> str:
    return f"histogram_quantile(0.5, sum by (le) (rate({metric}_bucket{{{selector}}}[{WINDOW}])))"


@dataclass(frozen=True)
class Case:
    fault: str
    target: str
    params: dict[str, Any]
    signal: str  # PromQL
    active: float  # signal must exceed this while the fault is active
    recovered: float  # ...and drop below this after removal
    timeout: float = 90
    logs: list[str] = field(default_factory=list)  # LogQL that must match while active


CASES = [
    Case(
        "api_latency",
        "api-gateway",
        {"delay_ms": 400, "route_prefix": "/api/products"},
        p50("http_server_request_duration_seconds", 'service="api-gateway",route="/api/products"'),
        active=0.3,
        recovered=0.1,
    ),
    Case(
        "http_errors",
        "orders-service",
        {"rate": 0.5},
        rate('http_server_requests_total{service="orders-service",status="500"}'),
        active=0.5,
        recovered=0.01,
        logs=['{service="orders-service", level="ERROR"} |= "unhandled error"'],
    ),
    Case(
        "db_pool_exhaustion",
        "orders-service",
        {"leak_probability": 0.5},
        'max(db_pool_connections_in_use{service="orders-service"})',
        active=7.5,
        recovered=4,
    ),
    Case(
        "slow_db_queries",
        "orders-service",
        {"queries": ["get_customer_order_history"]},
        p50(
            "db_query_duration_seconds",
            'service="orders-service",query="get_customer_order_history"',
        ),
        active=0.03,
        recovered=0.02,
    ),
    Case(
        "db_network_latency",
        "shop-db",
        {"latency_ms": 150},
        p50("db_query_duration_seconds", 'service="orders-service",query="get_order"'),
        active=0.1,
        recovered=0.05,
    ),
    Case(
        "redis_failure",
        "shop-cache",
        {"mode": "down"},
        rate('redis_command_errors_total{service="api-gateway"}'),
        active=0.5,
        recovered=0.01,
    ),
    Case(
        "worker_backlog",
        "fulfilment-worker",
        {"delay_ms": 1500, "failure_rate": 0.8},
        rate('worker_job_failures_total{reason="FulfilmentStepError"}'),
        active=0.1,
        recovered=0.01,
    ),
    Case(
        "memory_pressure",
        "orders-service",
        {"mb_per_second": 5, "max_mb": 120},
        'process_resident_memory_bytes{service="orders-service"}',
        active=150e6,
        recovered=110e6,
    ),
    Case(
        "dependency_outage",
        "payments-provider",
        {"mode": "down"},
        rate('http_client_requests_total{peer="payments-provider",status="error"}'),
        active=0.2,
        recovered=0.01,
        logs=['{service="orders-service"} |= "could not reach payments-provider"'],
    ),
    Case(
        "dependency_errors",
        "payments-provider",
        {"error_rate": 0.6},
        rate('http_client_requests_total{peer="payments-provider",status="503"}'),
        active=0.2,
        recovered=0.01,
    ),
    Case(
        "traffic_surge",
        "loadgen",
        {"multiplier": 2.5},
        rate('http_server_requests_total{service="api-gateway"}'),
        active=18,
        recovered=13,
        timeout=120,
    ),
]


@pytest.mark.parametrize("case", CASES, ids=[c.fault for c in CASES])
def test_fault_is_observable_and_reversible(fresh: Sim, case: Case) -> None:
    fault = fresh.inject(case.fault, case.target, **case.params)
    try:
        fresh.prom.wait_for(
            case.signal,
            lambda v: v > case.active,
            timeout=case.timeout,
            what=f"{case.fault} active",
        )
        for query in case.logs:
            fresh.loki.wait_for_lines(query, timeout=30)
    finally:
        fresh.remove(fault["id"])
    assert fresh.chaos.get("/api/v1/faults").json() == []
    fresh.prom.wait_for(
        case.signal,
        lambda v: v < case.recovered,
        timeout=case.timeout,
        what=f"{case.fault} recovered",
    )


def test_failed_deployment_fault_records_failure_and_rollback(fresh: Sim) -> None:
    fault = fresh.inject("failed_deployment", "api-gateway", version="3.9.1", canary_fraction=0.5)
    deployment = fresh.wait_deployment(fault["params"]["deployment_id"], "failed")
    assert deployment["version"] == "3.9.1"
    fresh.prom.wait_for(
        rate('http_server_requests_total{service="api-gateway",status="500"}'),
        lambda v: v > 1,
        what="canary errors",
    )
    fresh.remove(fault["id"])
    rollback = fresh.deployments(1)[0]
    assert (rollback["kind"], rollback["version"]) == ("rollback", "3.8.0")
    fresh.prom.wait_for(
        rate('http_server_requests_total{service="api-gateway",status="500"}'),
        lambda v: v < 0.05,
        what="canary removed",
    )


def test_invalid_fault_requests_are_rejected_with_hints(sim: Sim) -> None:
    r = sim.chaos.post("/api/v1/faults", json={"type": "redis_failure", "target": "api-gateway"})
    assert r.status_code == 422
    assert "valid targets: shop-cache" in r.text
    r = sim.chaos.post(
        "/api/v1/faults",
        json={"type": "http_errors", "target": "api-gateway", "params": {"rate": 3}},
    )
    assert r.status_code == 422
    r = sim.chaos.post("/api/v1/scenarios/nope/trigger", json={})
    assert r.status_code == 404
    assert "orders-n-plus-one-deploy" in r.text
