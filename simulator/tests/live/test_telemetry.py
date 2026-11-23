"""Baseline telemetry contract of the simulated environment (no faults active)."""

from __future__ import annotations

import pytest

from ai_sre_simulator.testing import WINDOW, Sim

pytestmark = pytest.mark.sim

REQUIRED_METRICS = {
    # request counts, status codes, error rates (derived)
    "http_server_requests_total": ["api-gateway", "orders-service"],
    # request latency, per endpoint (route label)
    "http_server_request_duration_seconds_bucket": ["api-gateway", "orders-service"],
    # database query latency
    "db_query_duration_seconds_bucket": ["api-gateway", "orders-service", "fulfilment-worker"],
    # connection pool utilisation
    "db_pool_connections_in_use": ["api-gateway", "orders-service", "fulfilment-worker"],
    "db_pool_max_connections": ["api-gateway", "orders-service", "fulfilment-worker"],
    "db_pool_pending_acquires": ["api-gateway", "orders-service", "fulfilment-worker"],
    # redis latency
    "redis_command_duration_seconds_bucket": ["api-gateway", "orders-service", "fulfilment-worker"],
    # queue depth + worker results/failures
    "queue_depth": ["fulfilment-worker"],
    "worker_jobs_processed_total": ["fulfilment-worker"],
    # dependency calls
    "http_client_requests_total": ["api-gateway", "orders-service"],
    # CPU and memory
    "process_cpu_seconds_total": ["api-gateway", "orders-service", "fulfilment-worker"],
    "process_resident_memory_bytes": ["api-gateway", "orders-service", "fulfilment-worker"],
    # running version
    "app_build_info": ["api-gateway", "orders-service", "fulfilment-worker"],
    # infrastructure exporters
    "pg_stat_activity_count": ["postgres"],
    "redis_up": ["redis"],
}

LOG_FIELDS = (
    "timestamp",
    "severity",
    "service",
    "environment",
    "simulated",
    "version",
    "trace_id",
    "request_id",
    "endpoint",
    "message",
)


@pytest.mark.parametrize("metric", sorted(REQUIRED_METRICS))
def test_required_metric_is_exported(fresh: Sim, metric: str) -> None:
    rows = fresh.prom.vector(f"count by (service) ({metric})")
    services = {r["metric"].get("service") for r in rows}
    missing = set(REQUIRED_METRICS[metric]) - services
    assert not missing, f"{metric} missing for {missing}"


def test_traffic_flows_and_baseline_is_healthy(fresh: Sim) -> None:
    rps = fresh.prom.wait_for(
        f'sum(rate(http_server_requests_total{{service="api-gateway"}}[{WINDOW}]))',
        lambda v: v > 5,
        what="gateway traffic",
    )
    errors = fresh.prom.value(
        f'sum(rate(http_server_requests_total{{service="api-gateway",status=~"5.."}}[{WINDOW}]))'
    )
    assert rps > 5
    assert (errors or 0) / rps < 0.01, "baseline should be (almost) error free"
    assert fresh.prom.value("redis_up") == 1
    assert fresh.version("api-gateway") == "3.8.0"
    assert fresh.version("orders-service") == "2.3.1"
    assert fresh.version("fulfilment-worker") == "1.6.2"


def test_every_series_is_labelled_simulated(fresh: Sim) -> None:
    unlabelled = fresh.prom.vector('count by (job) ({job!="", environment!="simulated-prod"})')
    assert unlabelled == [], unlabelled


def test_route_labels_are_templates_not_raw_paths(fresh: Sim) -> None:
    rows = fresh.prom.vector("count by (service, route) (http_server_requests_total)")
    routes = {r["metric"]["route"] for r in rows}
    assert "/api/products/{product_id}" in routes
    assert not [r for r in routes if any(ch.isdigit() for ch in r)], routes


def test_structured_logs_have_required_fields(fresh: Sim) -> None:
    for service in ("api-gateway", "orders-service", "fulfilment-worker", "deployer"):
        lines = fresh.loki.wait_for_lines(f'{{service="{service}"}}', since_s=300)
        record = next(line for line in lines if "message" in line)
        missing = [f for f in LOG_FIELDS if f not in record]
        assert not missing, f"{service}: missing {missing} in {record}"
        assert record["environment"] == "simulated-prod"
        assert record["simulated"] is True


def test_trace_id_propagates_from_gateway_to_orders_and_worker(fresh: Sim) -> None:
    response = fresh.gateway.post(
        "/api/orders", json={"customer_id": 42, "items": [{"product_id": 7, "quantity": 1}]}
    )
    assert response.status_code == 201, response.text
    assert response.headers["X-Simulated-Environment"] == "true"
    trace_id = response.headers["traceresponse"].split("-")[1]
    query = f'{{environment="simulated-prod"}} |= "{trace_id}"'
    lines = fresh.loki.wait_for_lines(query, min_count=3, timeout=45)
    services = {line["service"] for line in lines if "service" in line}
    assert {"api-gateway", "orders-service", "fulfilment-worker"} <= services, services
    assert len({line["request_id"] for line in lines if line.get("request_id")}) == 1


def test_deployment_records_have_required_fields(fresh: Sim) -> None:
    deployments = fresh.deployments()
    assert [d["id"] for d in deployments] == [f"d-{n}" for n in range(406, 400, -1)]
    for d in deployments:
        for field in ("id", "service", "version", "started_at", "changes_summary", "status"):
            assert d[field], (field, d)
        assert "flags" not in d, "build internals must stay hidden"
        assert "runtime_flags" not in d


def test_hidden_components_are_not_observable(fresh: Sim) -> None:
    jobs = {r["metric"]["job"] for r in fresh.prom.vector("count by (job) (up)")}
    assert jobs == {
        "api-gateway",
        "orders-service",
        "fulfilment-worker",
        "deployer",
        "postgres",
        "redis",
    }
    lines = fresh.loki.lines('{environment="simulated-prod"}', since_s=300, limit=2000)
    services = {line["_labels"].get("service") for line in lines}
    assert services.isdisjoint({"chaos", "toxiproxy", "payments-provider", "loadgen", "seed"}), (
        services
    )
