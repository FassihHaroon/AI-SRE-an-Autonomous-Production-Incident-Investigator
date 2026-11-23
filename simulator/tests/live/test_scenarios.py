"""Each predefined scenario produces its documented signals, and clearing it recovers.

Signals are asserted only through Prometheus, Loki and the deployer API. Timelines are run
with ``speed`` > 1 where a scenario has delayed steps.
"""

from __future__ import annotations

import pytest

from ai_sre_simulator.testing import WINDOW, Sim

pytestmark = pytest.mark.sim


def rate(expr: str) -> str:
    return f"sum(rate({expr}[{WINDOW}]))"


def increase(expr: str, window: str = "1m") -> str:
    return f"sum(increase({expr}[{window}]))"


def p(q: float, metric: str, selector: str, by: str = "le") -> str:
    return f"histogram_quantile({q}, sum by ({by}) (rate({metric}_bucket{{{selector}}}[{WINDOW}])))"


def latest_deployments(sim: Sim, n: int) -> list[tuple[str, str, str, str]]:
    return [(d["id"], d["service"], d["version"], d["kind"]) for d in sim.deployments(n)]


# --------------------------------------------------------------------------------- A
def test_a_orders_n_plus_one_deploy(fresh: Sim) -> None:
    run = fresh.trigger("orders-n-plus-one-deploy", speed=20)
    fresh.wait_run_applied(run["id"])
    fresh.wait_version("orders-service", "2.4.0")

    assert latest_deployments(fresh, 2) == [
        ("d-408", "orders-service", "2.4.0", "deploy"),
        ("d-407", "api-gateway", "3.8.1", "deploy"),
    ]
    # The new per-order query is slow and the pool saturates.
    fresh.prom.wait_for(
        p(
            0.95,
            "db_query_duration_seconds",
            'service="orders-service",query="get_order_items_for_history"',
        ),
        lambda v: v > 0.05,
        what="slow get_order_items_for_history",
    )
    fresh.prom.wait_for(
        'db_pool_connections_in_use{service="orders-service"}',
        lambda v: v >= 8,
        what="orders pool saturated",
    )
    fresh.prom.wait_for(
        increase('db_pool_acquire_timeouts_total{service="orders-service"}'),
        lambda v: v > 0,
        what="pool acquire timeouts",
    )
    fresh.prom.wait_for(
        p(
            0.99,
            "http_server_request_duration_seconds",
            'service="orders-service",route="/customers/{customer_id}/orders"',
        ),
        lambda v: v > 1.0,
        what="order-history p99 > 1s",
    )
    fresh.prom.wait_for(
        rate('http_server_requests_total{service="api-gateway",status="502"}'),
        lambda v: v > 0,
        what="gateway 502s",
    )
    fresh.loki.wait_for_lines(
        '{service="orders-service"} |= "waiting for a database connection"', timeout=30
    )
    fresh.loki.wait_for_lines(
        '{service="postgres"} |= "order_items WHERE order_id::text"', timeout=30
    )

    fresh.clear()
    fresh.wait_version("orders-service", "2.3.1")
    fresh.wait_version("api-gateway", "3.8.0")
    assert latest_deployments(fresh, 2)[0][3] == "rollback"
    fresh.prom.wait_for(
        'db_pool_pending_acquires{service="orders-service"}',
        lambda v: v == 0,
        what="no pool waiters after rollback",
    )
    fresh.prom.wait_for(
        rate('db_query_duration_seconds_count{query="get_order_items_for_history"}'),
        lambda v: v == 0,
        what="N+1 query gone",
    )


# --------------------------------------------------------------------------------- B
def test_b_redis_outage(fresh: Sim) -> None:
    fresh.wait_run_applied(fresh.trigger("redis-outage")["id"])

    fresh.prom.wait_for("redis_up", lambda v: v == 0, what="redis_up == 0")
    for service in ("api-gateway", "orders-service"):
        fresh.prom.wait_for(
            rate(f'redis_command_errors_total{{service="{service}"}}'),
            lambda v: v > 0,
            what=f"redis errors on {service}",
        )
    fresh.prom.wait_for(
        rate(
            'http_server_requests_total{service="api-gateway",route="/api/cart/{customer_id}",status="503"}'
        ),
        lambda v: v > 0,
        what="cart 503s",
    )
    fresh.prom.wait_for(
        rate('cache_requests_total{result="error"}'), lambda v: v > 0, what="catalog cache errors"
    )
    fresh.prom.wait_for(
        p(
            0.5,
            "http_server_request_duration_seconds",
            'service="api-gateway",route="/api/products"',
        ),
        lambda v: v > 0.25,
        what="catalog slower (timeout + DB fallback)",
    )
    # Catalog still served (fallback), orders fail after payment.
    products = fresh.prom.value(
        rate('http_server_requests_total{service="api-gateway",route="/api/products",status="200"}')
    )
    assert (products or 0) > 0
    fresh.prom.wait_for(
        rate('orders_created_total{result="enqueue_failed"}'),
        lambda v: v > 0,
        what="orders fail to enqueue",
    )
    fresh.loki.wait_for_lines('{service="api-gateway"} |= "catalog cache unavailable"', timeout=30)
    fresh.loki.wait_for_lines(
        '{service="fulfilment-worker"} |= "cannot read from fulfilment queue"', timeout=30
    )
    assert latest_deployments(fresh, 1)[0][0] == "d-406", "no deployment involved"

    fresh.clear()
    fresh.prom.wait_for("redis_up", lambda v: v == 1, what="redis back")
    fresh.prom.wait_for(rate("redis_command_errors_total"), lambda v: v == 0, what="errors stop")


# --------------------------------------------------------------------------------- C
def test_c_worker_deploy_backlog(fresh: Sim) -> None:
    fresh.wait_run_applied(fresh.trigger("worker-deploy-backlog")["id"])
    fresh.wait_version("fulfilment-worker", "1.7.0")
    assert latest_deployments(fresh, 1) == [("d-407", "fulfilment-worker", "1.7.0", "deploy")]

    fresh.prom.wait_for(
        rate('worker_job_failures_total{reason="LabelServiceTimeoutError"}'),
        lambda v: v > 0,
        what="label service failures",
    )
    fresh.prom.wait_for(
        'queue_depth{queue="fulfilment"}', lambda v: v >= 20, timeout=120, what="queue accumulating"
    )
    fresh.prom.wait_for(
        'queue_oldest_job_age_seconds{queue="fulfilment"}',
        lambda v: v > 10,
        what="oldest job ageing",
    )
    gateway_5xx = fresh.prom.value(
        rate('http_server_requests_total{service="api-gateway",status=~"5.."}')
    )
    assert (gateway_5xx or 0) == 0, "the API stays healthy while fulfilment backs up"
    fresh.loki.wait_for_lines(
        '{service="fulfilment-worker", level="ERROR"} |= "labels.shipping.internal"', timeout=30
    )

    fresh.clear()
    fresh.wait_version("fulfilment-worker", "1.6.2")
    fresh.prom.wait_for(
        'queue_depth{queue="fulfilment"}',
        lambda v: v < 5,
        timeout=90,
        what="backlog drains after rollback",
    )


# --------------------------------------------------------------------------------- D
def test_d_payments_provider_errors(fresh: Sim) -> None:
    fresh.wait_run_applied(fresh.trigger("payments-provider-errors")["id"])

    fresh.prom.wait_for(
        rate(
            'http_client_requests_total{service="orders-service",peer="payments-provider",status="503"}'
        ),
        lambda v: v > 0,
        what="503s from payments provider",
    )
    fresh.prom.wait_for(
        p(0.5, "http_client_request_duration_seconds", 'peer="payments-provider"'),
        lambda v: v > 0.5,
        what="payments calls slower",
    )
    fresh.prom.wait_for(
        rate('orders_created_total{result="payment_failed"}'),
        lambda v: v > 0,
        what="payment failures",
    )
    fresh.prom.wait_for(
        rate('http_server_requests_total{service="api-gateway",route="/api/orders",status="502"}'),
        lambda v: v > 0,
        what="order placement 502s",
    )
    browse_5xx = fresh.prom.value(
        rate('http_server_requests_total{route="/api/products",status=~"5.."}')
    )
    assert (browse_5xx or 0) == 0, "only the payment path is affected"
    fresh.loki.wait_for_lines(
        '{service="orders-service"} |= "payments-provider returned HTTP 503"', timeout=30
    )
    assert latest_deployments(fresh, 1)[0][0] == "d-406", "no internal change"

    fresh.clear()
    fresh.prom.wait_for(
        rate('http_client_requests_total{peer="payments-provider",status="503"}'),
        lambda v: v == 0,
        what="provider recovered",
    )


# --------------------------------------------------------------------------------- E
def test_e_db_connection_leak(fresh: Sim) -> None:
    fresh.wait_run_applied(fresh.trigger("db-connection-leak")["id"])
    assert latest_deployments(fresh, 1) == [("d-407", "orders-service", "2.3.1", "config_change")]

    fresh.prom.wait_for(
        'sum(pg_stat_activity_count{datname="shop",state="idle in transaction"})',
        lambda v: v >= 3,
        timeout=120,
        what="idle-in-transaction connections",
    )
    fresh.prom.wait_for(
        'db_pool_connections_in_use{service="orders-service"}',
        lambda v: v >= 8,
        timeout=150,
        what="pool exhausted by leaks",
    )
    fresh.prom.wait_for(
        increase('db_pool_acquire_timeouts_total{service="orders-service"}'),
        lambda v: v > 0,
        what="acquire timeouts",
    )
    fresh.prom.wait_for(
        rate('http_server_requests_total{service="orders-service",route="/orders",status="503"}'),
        lambda v: v > 0,
        what="503 on order placement too (not one endpoint)",
    )
    # Not a slow-query problem: the queries themselves stay fast.
    order_query = fresh.prom.value(
        p(0.95, "db_query_duration_seconds", 'service="orders-service",query="get_order"')
    )
    assert order_query is None or order_query < 0.1, order_query
    fresh.loki.wait_for_lines(
        '{service="orders-service"} |= "waiting for a database connection"', timeout=30
    )

    fresh.clear()
    fresh.prom.wait_for(
        'sum(pg_stat_activity_count{datname="shop",state="idle in transaction"})',
        lambda v: v == 0,
        what="leaked connections returned",
    )
    fresh.prom.wait_for(
        'db_pool_pending_acquires{service="orders-service"}', lambda v: v == 0, what="no waiters"
    )


# --------------------------------------------------------------------------------- F
def test_f_gateway_failed_canary(fresh: Sim) -> None:
    fresh.wait_run_applied(fresh.trigger("gateway-failed-canary")["id"])

    share = fresh.prom.wait_for(
        f"{rate('http_server_requests_total{service="api-gateway",status="500"}')} / "
        f"{rate('http_server_requests_total{service="api-gateway"}')}",
        lambda v: 0.1 < v < 0.45,
        what="~25% of gateway requests fail",
    )
    assert share
    canary_errors = fresh.loki.wait_for_lines(
        '{service="api-gateway", level="ERROR"} | json | version="3.9.0"', timeout=30
    )
    assert any("ConfigError" in str(line.get("error")) for line in canary_errors)
    orders_5xx = fresh.prom.value(
        rate('http_server_requests_total{service="orders-service",status=~"5.."}')
    )
    assert (orders_5xx or 0) == 0, "downstream services are healthy"

    deployment = fresh.wait_deployment("d-407", "failed", timeout=60)
    assert (deployment["service"], deployment["version"]) == ("api-gateway", "3.9.0")
    assert "canary" in (deployment["failure_reason"] or "")

    fresh.clear()
    assert latest_deployments(fresh, 1)[0][3] == "rollback"
    fresh.prom.wait_for(
        rate('http_server_requests_total{service="api-gateway",status="500"}'),
        lambda v: v == 0,
        what="canary errors stop",
    )


# --------------------------------------------------------------------------------- G
def test_g_gateway_memory_leak(fresh: Sim) -> None:
    rss = 'process_resident_memory_bytes{service="api-gateway"}'
    before = fresh.prom.value(rss) or 0
    fresh.wait_run_applied(fresh.trigger("gateway-memory-leak")["id"])
    fresh.wait_version("api-gateway", "3.8.2")
    assert latest_deployments(fresh, 1) == [("d-407", "api-gateway", "3.8.2", "deploy")]

    grown = fresh.prom.wait_for(
        rss, lambda v: v > before + 40e6, timeout=90, what="gateway RSS +40 MB"
    )
    assert (fresh.prom.value(f"deriv({rss}[1m])") or 0) > 0, "memory still climbing"

    fresh.clear()
    fresh.wait_version("api-gateway", "3.8.0")
    fresh.prom.wait_for(rss, lambda v: v < grown - 30e6, what="memory released after rollback")


# --------------------------------------------------------------------------------- H
def test_h_db_network_latency(fresh: Sim) -> None:
    fresh.wait_run_applied(fresh.trigger("db-network-latency")["id"])

    for service, query in (
        ("orders-service", "get_order"),
        ("api-gateway", "list_products"),
        ("fulfilment-worker", "mark_order_fulfilled"),
    ):
        fresh.prom.wait_for(
            p(0.5, "db_query_duration_seconds", f'service="{service}",query="{query}"'),
            lambda v: v > 0.08,
            what=f"{service} {query} slower",
        )
    fresh.prom.wait_for(
        p(0.5, "http_server_request_duration_seconds", 'service="orders-service",route="/orders"'),
        lambda v: v > 0.5,
        what="order placement (5 queries) much slower",
    )
    errors = fresh.prom.value(rate('http_server_requests_total{status=~"5.."}'))
    assert (errors or 0) < 0.5, "latency, not errors"
    assert latest_deployments(fresh, 1)[0][0] == "d-406", "no deployment involved"

    fresh.clear()
    fresh.prom.wait_for(
        p(0.5, "db_query_duration_seconds", 'service="orders-service",query="get_order"'),
        lambda v: v < 0.05,
        what="DB latency back to normal",
    )
