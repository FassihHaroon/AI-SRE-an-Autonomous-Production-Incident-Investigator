"""MCP servers without external services: registry, schemas, validation, approvals, errors.

Calls go through the real MCP protocol (in-memory transport of the official SDK).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from mcp import Client

from ai_sre.mcp_servers.approvals import ApprovalError, ApprovalStore
from ai_sre.mcp_servers.core import ToolResult, cap_result
from ai_sre.mcp_servers.incidents import load_incidents
from ai_sre.mcp_servers.logs import build_query, template

REQUIRED_TOOLS = {
    "logs": {"search_logs", "get_error_logs", "aggregate_errors", "get_logs_by_trace_id"},
    "metrics": {"get_metric_timeseries", "get_service_metrics", "compare_metric_windows",
                "detect_metric_anomalies"},
    "deployments": {"list_recent_deployments", "get_deployment_details",
                    "compare_deployment_versions"},
    "database": {"get_database_health", "get_slow_queries", "get_connection_pool_status",
                 "get_database_metrics"},
    "infrastructure": {"get_service_status", "get_container_health", "get_resource_usage",
                       "get_dependency_status"},
    "incidents": {"list_historical_incidents", "get_incident_details", "search_incident_history"},
    "remediation": {"restart_service_simulation", "rollback_deployment_simulation",
                    "clear_queue_simulation", "scale_service_simulation"},
    "registry": {"list_available_tools"},
}  # fmt: skip

Entries = Callable[..., Any]  # returns conftest.Built (dict of servers + .approvals)


def error_of(result: Any) -> dict[str, Any]:
    assert result.is_error, result
    text = result.content[0].text
    payload: dict[str, Any] = json.loads(text[text.index("{") :])
    return payload


# --------------------------------------------------------------------------- registry / schemas
async def test_every_required_tool_exists_with_schema_and_annotations(
    entries_factory: Entries,
) -> None:
    entries = entries_factory()
    assert set(entries) == set(REQUIRED_TOOLS)
    for name, entry in entries.items():
        async with Client(entry.server) as client:
            tools = {t.name: t for t in (await client.list_tools()).tools}
        assert set(tools) == REQUIRED_TOOLS[name], name
        for tool in tools.values():
            assert len(tool.description or "") > 20, tool.name
            assert tool.input_schema["type"] == "object"
            assert tool.output_schema is not None, f"{tool.name} has no typed output"
            assert {"tool", "summary", "truncated"} <= set(tool.output_schema["properties"])
            assert tool.annotations is not None
            assert tool.annotations.read_only_hint is (name != "remediation"), tool.name


async def test_registry_tool_lists_all_servers(entries_factory: Entries) -> None:
    entries = entries_factory()
    async with Client(entries["registry"].server) as client:
        result = await client.call_tool("list_available_tools", {"include_schemas": True})
    data = result.structured_content
    assert data is not None
    assert data["tool_count"] == sum(len(v) for v in REQUIRED_TOOLS.values())
    remediation = next(s for s in data["servers"] if s["name"] == "remediation")
    assert remediation["read_only"] is False
    assert remediation["enabled"] is False
    assert remediation["auth"] == "remediation"
    assert all(t["input_schema"] for s in data["servers"] for t in s["tools"])


# --------------------------------------------------------------------------- validation / errors
@pytest.mark.parametrize(
    ("server", "tool", "args"),
    [
        ("logs", "get_logs_by_trace_id", {"trace_id": "not-a-trace"}),
        ("logs", "search_logs", {"limit": 5000}),
        ("logs", "search_logs", {"service": "mainframe"}),
        ("metrics", "get_metric_timeseries", {"promql": ""}),
        ("metrics", "detect_metric_anomalies", {"z_threshold": 0}),
        ("deployments", "get_deployment_details", {"deployment_id": "407"}),
        ("database", "get_slow_queries", {"order_by": "random"}),
        ("infrastructure", "get_resource_usage", {"window_minutes": 100000}),
        ("incidents", "get_incident_details", {"incident_id": "../etc/passwd"}),
        ("remediation", "scale_service_simulation",
         {"service": "fulfilment-worker", "replicas": 50, "reason": "more capacity"}),
    ],
)  # fmt: skip
async def test_invalid_arguments_are_rejected_before_execution(
    entries_factory: Entries, server: str, tool: str, args: dict[str, Any]
) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(500)

    entries = entries_factory(handler)
    async with Client(entries[server].server) as client:
        result = await client.call_tool(tool, args)
    assert result.is_error
    assert "validation error" in result.content[0].text
    assert calls == [], "the backend must not be called with invalid input"


async def test_backend_outage_is_reported_as_structured_error(entries_factory: Entries) -> None:
    entries = entries_factory(lambda _r: httpx.Response(502, text="bad gateway"))
    async with Client(entries["logs"].server) as client:
        result = await client.call_tool("get_error_logs", {})
    err = error_of(result)
    assert err["code"] == "backend_unavailable"
    assert "loki" in err["message"]


async def test_database_unreachable_is_backend_unavailable(entries_factory: Entries) -> None:
    entries = entries_factory()
    async with Client(entries["database"].server) as client:
        result = await client.call_tool("get_database_health", {})
    assert error_of(result)["code"] == "backend_unavailable"


async def test_slow_backend_hits_the_tool_timeout(entries_factory: Entries) -> None:
    import asyncio

    async def slow(_request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(3)
        return httpx.Response(200, json={})

    entries = entries_factory(slow, tool_timeout_s=0.5)  # type: ignore[arg-type]
    async with Client(entries["deployments"].server) as client:
        result = await client.call_tool("list_recent_deployments", {})
    assert error_of(result)["code"] == "timeout"


async def test_compare_split_at_must_be_in_the_past(entries_factory: Entries) -> None:
    entries = entries_factory()
    async with Client(entries["metrics"].server) as client:
        result = await client.call_tool(
            "compare_metric_windows",
            {
                "service": "orders-service",
                "signal": "error_rate",
                "split_at": "2099-01-01T00:00:00Z",
            },
        )
    assert error_of(result)["code"] == "invalid_argument"


async def test_signal_not_applicable_gives_hint(entries_factory: Entries) -> None:
    entries = entries_factory()
    async with Client(entries["metrics"].server) as client:
        result = await client.call_tool(
            "compare_metric_windows", {"service": "fulfilment-worker", "signal": "latency_p99"}
        )
    err = error_of(result)
    assert err["code"] == "invalid_argument"
    assert "api-gateway" in err["hint"]


# --------------------------------------------------------------------------- incidents (no backend)
async def test_incident_tools(entries_factory: Entries) -> None:
    entries = entries_factory()
    async with Client(entries["incidents"].server) as client:
        listing = (
            await client.call_tool("list_historical_incidents", {"limit": 3})
        ).structured_content
        assert listing is not None
        assert listing["total"] == 8
        assert len(listing["incidents"]) == 3
        assert listing["next_offset"] == 3
        hits = (await client.call_tool(
            "search_incident_history", {"query": "connection pool timeouts after deploy n+1"}
        )).structured_content  # fmt: skip
        assert hits is not None
        assert hits["hits"][0]["incident"]["id"] == "INC-2026-031"
        details = (
            await client.call_tool("get_incident_details", {"incident_id": "INC-2026-044"})
        ).structured_content
        assert details is not None
        assert "Redis" in details["what_happened"]
        missing = await client.call_tool("get_incident_details", {"incident_id": "INC-1999-001"})
        assert error_of(missing)["code"] == "not_found"


# --------------------------------------------------------------------------- remediation gating
async def test_remediation_disabled_by_default(entries_factory: Entries) -> None:
    calls: list[httpx.Request] = []
    entries = entries_factory(lambda r: (calls.append(r), httpx.Response(200, json={}))[1])
    async with Client(entries["remediation"].server) as client:
        result = await client.call_tool(
            "restart_service_simulation", {"service": "api-gateway", "reason": "memory leak"}
        )
    assert error_of(result)["code"] == "remediation_disabled"
    assert calls == []


async def test_remediation_requires_approval_then_executes_once(entries_factory: Entries) -> None:
    calls: list[httpx.Request] = []

    def orchestrator(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"service": "fulfilment-worker", "restart_generation": 1})

    entries = entries_factory(orchestrator, remediation_enabled=True)
    server = entries["remediation"].server
    args = {"service": "fulfilment-worker", "reason": "release leaked connections"}
    async with Client(server) as client:
        first = (await client.call_tool("restart_service_simulation", args)).structured_content
        assert first is not None
        assert first["status"] == "approval_required"
        assert calls == [], "nothing runs without approval"

        token = entries.approvals.approve(first["approval_request_id"], "alice")

        tampered = await client.call_tool(
            "restart_service_simulation",
            {**args, "service": "api-gateway", "approval_token": token},
        )
        assert error_of(tampered)["code"] == "approval_invalid"

        done = (
            await client.call_tool("restart_service_simulation", {**args, "approval_token": token})
        ).structured_content
        assert done is not None
        assert done["status"] == "executed"
        assert done["approved_by"] == "alice"
        assert len(calls) == 1
        assert calls[0].url.path == "/api/v1/actions/restart"

        reused = await client.call_tool(
            "restart_service_simulation", {**args, "approval_token": token}
        )
        assert "already used" in error_of(reused)["message"]
        assert len(calls) == 1


async def test_failed_action_is_never_reported_as_success(entries_factory: Entries) -> None:
    entries = entries_factory(
        lambda _r: httpx.Response(409, text="nothing to roll back"), remediation_enabled=True
    )
    server = entries["remediation"].server
    args = {"service": "orders-service", "reason": "bad deploy d-408"}
    async with Client(server) as client:
        request = (
            await client.call_tool("rollback_deployment_simulation", args)
        ).structured_content
        assert request is not None
        token = entries.approvals.approve(request["approval_request_id"], "bob")
        result = await client.call_tool(
            "rollback_deployment_simulation", {**args, "approval_token": token}
        )
    err = error_of(result)
    assert err["code"] == "action_failed"
    assert "FAILED" in err["message"]


# --------------------------------------------------------------------------- approvals unit
def test_approval_token_binds_action_params_and_expiry() -> None:
    store = ApprovalStore(b"secret", ttl_s=60)
    req = store.request("scale_service", {"service": "fulfilment-worker", "replicas": 2})
    with pytest.raises(ApprovalError, match="named human"):
        store.approve(req.id, "agent")
    token = store.approve(req.id, "carol")
    with pytest.raises(ApprovalError, match="parameters differ"):
        store.consume(token, "scale_service", {"service": "fulfilment-worker", "replicas": 9})
    with pytest.raises(ApprovalError, match="approves scale_service"):
        store.consume(token, "clear_queue", {"service": "fulfilment-worker", "replicas": 2})
    body, sig = token.rsplit(".", 1)
    with pytest.raises(ApprovalError, match="signature"):
        store.consume(f"{body}.{'0' * len(sig)}", "scale_service", req.params)
    with pytest.raises(ApprovalError, match="signature"):
        ApprovalStore(b"other", 60).consume(token, "scale_service", req.params)
    assert store.consume(token, "scale_service", req.params).status == "used"


def test_expired_approval_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    store = ApprovalStore(b"secret", ttl_s=30)
    req = store.request("clear_queue", {"queue": "fulfilment"})
    token = store.approve(req.id, "dave")
    real = time.time()
    monkeypatch.setattr(time, "time", lambda: real + 31)
    with pytest.raises(ApprovalError, match="expired"):
        store.consume(token, "clear_queue", req.params)


def test_rejected_request_cannot_be_approved() -> None:
    store = ApprovalStore(b"secret", ttl_s=60)
    req = store.request("restart_service", {"service": "api-gateway"})
    store.reject(req.id, "erin")
    with pytest.raises(ApprovalError, match="rejected"):
        store.approve(req.id, "erin")


# --------------------------------------------------------------------------- helpers
def test_log_templates_normalise_variable_parts() -> None:
    a = template("timed out after 2.0s waiting (pool size=8, in use=8, waiting=12)")
    b = template("timed out after 2.0s waiting (pool size=8, in use=8, waiting=3)")
    assert a == b
    assert template("trace 0af7651916cd43dd8448eb211c80319c") == "trace <hex>"


def test_logql_injection_is_refused() -> None:
    assert build_query("orders-service", "ERROR", "pool") == (
        '{environment="simulated-prod", service="orders-service", level=~"ERROR"} |= `pool`'
    )
    with pytest.raises(Exception, match="backticks"):
        build_query(None, None, "x` or 1")


def test_cap_result_trims_lists_and_flags_truncation() -> None:
    class Big(ToolResult):
        items: list[str]

    big = Big(summary="s", items=["x" * 100 for _ in range(500)])
    capped = cap_result(big, 4000)
    assert capped.truncated
    assert len(capped.model_dump_json()) <= 4000


def test_incident_corpus_parses(incidents_dir: Any) -> None:
    incidents = load_incidents(incidents_dir)
    assert len(incidents) == 8
    assert all(i.section("Resolution") for i in incidents.values())
