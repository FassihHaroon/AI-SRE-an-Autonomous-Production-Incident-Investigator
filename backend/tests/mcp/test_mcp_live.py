"""Protocol-level tests: real MCP over Streamable HTTP against the running simulated environment.

Needs: docker compose -f docker-compose.sim.yml up -d --wait   (run: uv run poe test-mcp)

A real uvicorn server hosts the MCP app; the official ``mcp`` client connects over HTTP with
bearer tokens, lists tools, calls every tool and validates each result against the tool's
published ``outputSchema``.
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from collections.abc import Iterator
from typing import Any

import httpx
import httpx2
import jsonschema
import pytest
import uvicorn
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from pydantic import SecretStr

from ai_sre.config import REPO_ROOT
from ai_sre.mcp_servers.app import create_app
from ai_sre.mcp_servers.settings import McpSettings
from ai_sre_simulator.testing import CHAOS, DEPLOYER, GATEWAY, Loki, Prometheus, Sim

pytestmark = pytest.mark.sim

READ, REMEDIATE, ADMIN = "t-read", "t-remediate", "t-admin"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


@pytest.fixture(scope="module")
def base_url() -> Iterator[str]:
    try:
        httpx.get(f"{CHAOS}/healthz", timeout=3).raise_for_status()
    except httpx.HTTPError:
        pytest.skip("simulated environment not running")
    port = _free_port()
    settings = McpSettings(
        read_token=SecretStr(READ),
        remediation_token=SecretStr(REMEDIATE),
        admin_token=SecretStr(ADMIN),
        approval_secret=SecretStr("live-test-approval-secret"),
        incidents_dir=REPO_ROOT / "knowledge" / "incidents",
        remediation_enabled=True,
    )
    app = create_app(settings).asgi
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}"
    for _ in range(50):
        try:
            if httpx.get(f"{url}/healthz", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.2)
    yield url
    server.should_exit = True
    thread.join(timeout=10)


@pytest.fixture(scope="module")
def sim() -> Iterator[Sim]:
    clients = [httpx.Client(base_url=u, timeout=30) for u in (CHAOS, DEPLOYER, GATEWAY)]
    plain = httpx.Client(timeout=30)
    yield Sim(clients[0], clients[1], clients[2], Prometheus(plain), Loki(plain))
    for c in [*clients, plain]:
        c.close()


async def call(base: str, server: str, tool: str, args: dict[str, Any], token: str = READ) -> Any:
    http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=60)
    async with Client(streamable_http_client(f"{base}/mcp/{server}/", http_client=http)) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
        result = await client.call_tool(tool, args)
        return result, tools[tool]


def run(coro: Any) -> Any:
    return asyncio.run(coro)


# --------------------------------------------------------------------------- auth
def test_http_auth_scopes(base_url: str) -> None:
    init = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    headers = {"Accept": "application/json, text/event-stream"}
    assert httpx.post(f"{base_url}/mcp/logs/", json=init, headers=headers).status_code == 401
    wrong = {**headers, "Authorization": "Bearer nope"}
    assert httpx.post(f"{base_url}/mcp/logs/", json=init, headers=wrong).status_code == 403
    read = {**headers, "Authorization": f"Bearer {READ}"}
    assert httpx.post(f"{base_url}/mcp/remediation/", json=init, headers=read).status_code == 403
    assert httpx.get(f"{base_url}/approvals", headers=read).status_code == 403
    registry = httpx.get(f"{base_url}/registry", headers=read)
    assert registry.status_code == 200
    assert registry.json()["tool_count"] == 27


def test_tools_listed_over_http_on_every_server(base_url: str) -> None:
    async def go() -> dict[str, set[str]]:
        out = {}
        for server in (
            "registry",
            "logs",
            "metrics",
            "deployments",
            "database",
            "infrastructure",
            "incidents",
            "remediation",
        ):
            token = REMEDIATE if server == "remediation" else READ
            http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"})
            async with Client(
                streamable_http_client(f"{base_url}/mcp/{server}/", http_client=http)
            ) as c:
                assert c.server_info is not None
                out[server] = {t.name for t in (await c.list_tools()).tools}
        return out

    listed = run(go())
    assert sum(len(v) for v in listed.values()) == 27
    assert "rollback_deployment_simulation" in listed["remediation"]


# --------------------------------------------------------------------------- every read tool
def _trace_id() -> str:
    r = httpx.post(
        f"{GATEWAY}/api/orders",
        json={"customer_id": 9, "items": [{"product_id": 3, "quantity": 1}]},
    )
    assert r.status_code == 201, r.text
    return r.headers["traceresponse"].split("-")[1]


READ_CALLS: list[tuple[str, str, dict[str, Any]]] = [
    ("registry", "list_available_tools", {"include_schemas": True}),
    ("logs", "search_logs", {"service": "api-gateway", "since_minutes": 5, "limit": 5}),
    ("logs", "search_logs", {"contains": "order placed", "since_minutes": 10, "limit": 3}),
    ("logs", "get_error_logs", {"since_minutes": 60, "limit": 5}),
    ("logs", "aggregate_errors", {"since_minutes": 60, "top_k": 5}),
    ("logs", "get_logs_by_trace_id", {"trace_id": "__TRACE__"}),
    (
        "metrics",
        "get_metric_timeseries",
        {"promql": "sum by (service) (rate(http_server_requests_total[1m]))", "since_minutes": 10},
    ),
    ("metrics", "get_service_metrics", {"service": "orders-service"}),
    (
        "metrics",
        "compare_metric_windows",
        {
            "service": "api-gateway",
            "signal": "latency_p99",
            "window_minutes": 5,
            "baseline_offset_minutes": 15,
        },
    ),
    ("metrics", "detect_metric_anomalies", {"since_minutes": 5, "baseline_minutes": 20}),
    ("deployments", "list_recent_deployments", {"since_hours": 200}),
    ("deployments", "get_deployment_details", {"deployment_id": "d-406"}),
    (
        "deployments",
        "compare_deployment_versions",
        {"service": "orders-service", "from_version": "2.2.0", "to_version": "2.3.1"},
    ),
    ("database", "get_database_health", {}),
    ("database", "get_slow_queries", {"top_k": 5}),
    ("database", "get_connection_pool_status", {}),
    ("database", "get_database_metrics", {}),
    ("infrastructure", "get_service_status", {}),
    ("infrastructure", "get_container_health", {}),
    ("infrastructure", "get_resource_usage", {"window_minutes": 10}),
    ("infrastructure", "get_dependency_status", {}),
    ("incidents", "list_historical_incidents", {"limit": 5}),
    ("incidents", "get_incident_details", {"incident_id": "INC-2026-031"}),
    ("incidents", "search_incident_history", {"query": "redis timeouts carts failing"}),
]


@pytest.mark.parametrize(
    ("server", "tool", "args"), READ_CALLS, ids=[f"{s}.{t}" for s, t, _ in READ_CALLS]
)
def test_read_tool_returns_schema_valid_structured_json(
    base_url: str, server: str, tool: str, args: dict[str, Any]
) -> None:
    if args.get("trace_id") == "__TRACE__":
        args = {"trace_id": _trace_id(), "since_minutes": 10}
        time.sleep(4)  # let Alloy ship the lines
    result, spec = run(call(base_url, server, tool, args))
    assert not result.is_error, result.content[0].text
    data = result.structured_content
    jsonschema.validate(data, spec.output_schema)
    assert data["tool"] == tool
    assert data["summary"]
    assert len(str(data)) < 300_000


def test_tool_semantics_against_baseline(base_url: str, sim: Sim) -> None:
    sim.reset()
    deployments, _ = run(
        call(base_url, "deployments", "list_recent_deployments", {"since_hours": 200})
    )
    ids = [d["id"] for d in deployments.structured_content["deployments"]]
    assert ids[:2] == ["d-406", "d-405"]
    compare, _ = run(
        call(
            base_url,
            "deployments",
            "compare_deployment_versions",
            {"service": "orders-service", "from_version": "2.2.0", "to_version": "2.3.1"},
        )
    )
    assert [d["id"] for d in compare.structured_content["deployments_between"]] == [
        "d-405",
        "d-406",
    ]
    pools, _ = run(
        call(base_url, "database", "get_connection_pool_status", {"service": "orders-service"})
    )
    assert pools.structured_content["pools"][0]["max_connections"] == 8
    status, _ = run(
        call(base_url, "infrastructure", "get_service_status", {"service": "api-gateway"})
    )
    assert status.structured_content["services"][0]["version"] == "3.8.0"
    trace = _trace_id()
    time.sleep(4)
    logs, _ = run(
        call(base_url, "logs", "get_logs_by_trace_id", {"trace_id": trace, "since_minutes": 5})
    )
    assert {"api-gateway", "orders-service"} <= set(logs.structured_content["services"])
    missing, _ = run(
        call(base_url, "deployments", "get_deployment_details", {"deployment_id": "d-999"})
    )
    assert missing.is_error
    assert "not_found" in missing.content[0].text


def test_scenario_is_visible_through_tools(base_url: str, sim: Sim) -> None:
    sim.reset()
    sim.wait_run_applied(sim.trigger("db-connection-leak")["id"])
    sim.prom.wait_for(
        'db_pool_connections_in_use{service="orders-service"}',
        lambda v: v >= 8,
        timeout=150,
        what="pool exhausted",
    )
    sim.prom.wait_for(
        'sum(pg_stat_activity_count{datname="shop",state="idle in transaction"})',
        lambda v: v >= 3,
        timeout=120,
        what="leaked sessions visible in pg_stat_activity",
    )
    health, _ = run(call(base_url, "database", "get_database_health", {}))
    assert health.structured_content["idle_in_transaction"] >= 3
    errors, _ = run(
        call(
            base_url, "logs", "aggregate_errors", {"service": "orders-service", "since_minutes": 5}
        )
    )
    templates = [s["template"] for s in errors.structured_content["signatures"]]
    assert any("waiting for a database connection" in t for t in templates), templates
    deploys, _ = run(call(base_url, "deployments", "list_recent_deployments", {"since_hours": 1}))
    assert deploys.structured_content["deployments"][0]["kind"] == "config_change"
    sim.clear()


# --------------------------------------------------------------------------- remediation over HTTP
def _approve(base_url: str, request_id: str) -> str:
    r = httpx.post(
        f"{base_url}/approvals/{request_id}/approve",
        json={"approver": "test-oncall"},
        headers={"Authorization": f"Bearer {ADMIN}"},
    )
    assert r.status_code == 200, r.text
    token: str = r.json()["approval_token"]
    return token


def test_rollback_requires_human_approval_and_restores_version(base_url: str, sim: Sim) -> None:
    sim.reset()
    sim.wait_run_applied(sim.trigger("gateway-memory-leak")["id"])
    sim.wait_version("api-gateway", "3.8.2")
    args = {"service": "api-gateway", "reason": "memory leak since d-407 (3.8.2)"}

    first, _ = run(call(base_url, "remediation", "rollback_deployment_simulation", args, REMEDIATE))
    assert first.structured_content["status"] == "approval_required"
    time.sleep(3)
    assert sim.version("api-gateway") == "3.8.2", "nothing may change before approval"

    token = _approve(base_url, first.structured_content["approval_request_id"])
    done, _ = run(
        call(
            base_url,
            "remediation",
            "rollback_deployment_simulation",
            {**args, "approval_token": token},
            REMEDIATE,
        )
    )
    assert done.structured_content["status"] == "executed"
    assert done.structured_content["result"]["kind"] == "rollback"
    sim.wait_version("api-gateway", "3.8.0")

    again, _ = run(
        call(
            base_url,
            "remediation",
            "rollback_deployment_simulation",
            {**args, "approval_token": token},
            REMEDIATE,
        )
    )
    assert again.is_error
    assert "already used" in again.content[0].text
    sim.clear()


def test_restart_scale_and_clear_queue(base_url: str, sim: Sim) -> None:
    sim.reset()
    start_q = 'process_start_time_seconds{service="fulfilment-worker"}'
    before = sim.prom.value(start_q)

    def approved(tool: str, args: dict[str, Any]) -> dict[str, Any]:
        first, _ = run(call(base_url, "remediation", tool, args, REMEDIATE))
        token = _approve(base_url, first.structured_content["approval_request_id"])
        done, _ = run(
            call(base_url, "remediation", tool, {**args, "approval_token": token}, REMEDIATE)
        )
        assert not done.is_error, done.content[0].text
        result: dict[str, Any] = done.structured_content
        return result

    restart = approved(
        "restart_service_simulation", {"service": "fulfilment-worker", "reason": "test restart"}
    )
    assert restart["status"] == "executed"
    sim.prom.wait_for(
        start_q,
        lambda v: before is not None and v > before,
        timeout=90,
        what="worker process really restarted",
    )

    approved(
        "scale_service_simulation",
        {"service": "fulfilment-worker", "replicas": 2, "reason": "more consumers"},
    )
    sim.prom.wait_for(
        'worker_consumers{service="fulfilment-worker"}', lambda v: v == 4, what="4 consumers"
    )
    approved(
        "scale_service_simulation",
        {"service": "fulfilment-worker", "replicas": 1, "reason": "back to normal"},
    )

    cleared = approved(
        "clear_queue_simulation", {"queue": "fulfilment-dead-letter", "reason": "drop stale jobs"}
    )
    assert cleared["result"]["queue"] == "fulfilment-dead-letter"
    assert cleared["result"]["removed_jobs"] >= 0
