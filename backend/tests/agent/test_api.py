"""API for the frontend: investigations (start, SSE events, replay, approval, cancel, follow-up),
platform reads, incident history and settings. Runs the real graph with a fake tool gateway."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import AsyncExitStack
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from ai_sre.agent.graph import Deps, build_graph
from ai_sre.agent.reasoner import RuleBasedReasoner
from ai_sre.agent.runner import serializer
from ai_sre.config import Settings
from ai_sre.main import create_app
from ai_sre.mcp_servers.settings import McpSettings
from ai_sre.services.investigations import InvestigationService
from ai_sre.services.platform import PlatformService
from ai_sre.services.repository import MemoryRepository
from ai_sre.services.settings_store import AgentSettings, SettingsStore

Q = "Investigate why our orders API has been returning 500 errors and experiencing high latency for the last 20 minutes."


def _series(args: dict[str, Any]) -> dict[str, Any]:
    pts = [[f"2026-11-23T10:{m:02d}:00+00:00", float(m)] for m in range(0, 60)]
    return {
        "summary": "1 series",
        "series": [{"labels": {}, "stats": {"max": 59.0}, "points": pts}],
        "step_seconds": 60,
    }


@pytest.fixture
def client(gateway_factory: Callable[..., Any]) -> Iterator[tuple[TestClient, Any]]:
    gw = gateway_factory()
    gw.data["get_metric_timeseries"] = _series
    gw.data["get_container_health"] = lambda a: {
        "summary": "ok",
        "containers": [{"service": a["service"], "oom_risk": False}],
    }
    gw.data["search_logs"] = lambda a: {
        "summary": "1 line",
        "entries": [{"service": a.get("service"), "message": "ok"}],
    }

    async def runtime(app: FastAPI, settings: Settings, stack: AsyncExitStack) -> None:
        repo = MemoryRepository()
        graph = build_graph(Deps(gateway=gw, reasoner=RuleBasedReasoner())).compile(
            checkpointer=InMemorySaver(serde=serializer())
        )
        store = SettingsStore(repo)
        await store.save(AgentSettings(verification_wait_s=0))
        svc = InvestigationService(
            repo,
            {"rules": graph},
            mcp_url="http://mcp.test",
            admin_token="admin",
            default_limits=lambda: store.current.limits(),
        )

        async def mint(approval_request_id: str, approver: str) -> str:
            return f"token-for-{approval_request_id}-{approver}"

        svc.mint_token = mint  # type: ignore[method-assign]
        app.state.investigations, app.state.agent_settings = svc, store
        app.state.platform = PlatformService(gw)  # type: ignore[arg-type]
        app.state.mcp_settings, app.state.mcp_url = McpSettings(), "http://127.0.0.1:9"
        app.state.checkpointer_kind = app.state.repo_kind = "memory"

        async def no_kb() -> Any:
            raise RuntimeError("knowledge base not configured in this test")

        app.state.retriever = no_kb

    app = create_app(
        Settings(environment="test", llm_provider="gemini", gemini_api_key=None), runtime=runtime
    )
    with TestClient(app) as c:
        yield c, gw


def read_events(
    c: TestClient, inv_id: str, last_event_id: int | None = None
) -> list[dict[str, Any]]:
    headers = {"Last-Event-ID": str(last_event_id)} if last_event_id is not None else {}
    events, current = [], {}
    with c.stream(
        "GET", f"/api/v1/investigations/{inv_id}/events", headers=headers, timeout=60
    ) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        for line in r.iter_lines():
            if line.startswith("id: "):
                current["id"] = int(line[4:])
            elif line.startswith("event: "):
                current["event"] = line[7:]
            elif line.startswith("data: "):
                current["data"] = json.loads(line[6:])
            elif line == "" and current:
                events.append(current)
                if current.get("event") == "end":
                    break
                current = {}
    return events


def test_investigation_streams_real_progress_until_approval(client: tuple[TestClient, Any]) -> None:
    c, _ = client
    r = c.post("/api/v1/investigations", json={"question": Q})
    assert r.status_code == 202
    inv_id = r.json()["id"]
    events = read_events(c, inv_id)
    types = [e["event"] for e in events]
    for expected in (
        "node_started",
        "scope",
        "plan",
        "tool_call",
        "hypotheses",
        "root_cause",
        "proposals",
        "approval_required",
    ):
        assert expected in types, expected
    assert events[-1] == {"event": "end", "data": {"status": "awaiting_approval"}}
    ids = [e["id"] for e in events if "id" in e]
    assert ids == sorted(ids)
    assert len(ids) == len(set(ids))
    tool_calls = [e["data"]["data"] for e in events if e["event"] == "tool_call"]
    assert {"server", "tool", "arguments", "duration_ms", "ok"} <= tool_calls[0].keys()

    detail = c.get(f"/api/v1/investigations/{inv_id}").json()
    assert detail["investigation"]["status"] == "awaiting_approval"
    assert detail["state"]["plan"]["calls"]
    assert detail["state"]["hypotheses"][0]["id"] == "bad_deploy:orders-service:d-408"
    ev_id = detail["state"]["evidence"][0]["id"]
    call = c.get(f"/api/v1/investigations/{inv_id}/tool-calls/{ev_id}").json()
    assert call["id"] == ev_id
    assert "data" in call
    assert "arguments" in call
    assert c.get(f"/api/v1/investigations/{inv_id}/tool-calls/ev-999").status_code == 404

    # Replay after reconnect: only events after Last-Event-ID.
    replay = read_events(c, inv_id, last_event_id=ids[-3])
    assert [e["id"] for e in replay if "id" in e] == ids[-2:]

    listing = c.get("/api/v1/investigations").json()["investigations"]
    assert listing[0]["id"] == inv_id
    assert listing[0]["root_cause"]["trigger_ref"] == "d-408"


def test_approval_needs_confirmation_then_executes(client: tuple[TestClient, Any]) -> None:
    c, gw = client
    inv_id = c.post("/api/v1/investigations", json={"question": Q}).json()["id"]
    read_events(c, inv_id)
    body = {"proposal_id": "p1", "decision": "approve", "approver": "alice", "confirm": False}
    assert c.post(f"/api/v1/investigations/{inv_id}/approval", json=body).status_code == 422
    r = c.post(f"/api/v1/investigations/{inv_id}/approval", json={**body, "confirm": True})
    assert r.status_code == 200
    events = read_events(c, inv_id)
    assert any(e["event"] == "approval_decision" and e["data"]["data"]["approved"] for e in events)
    assert events[-1]["data"] == {"status": "completed"}
    detail = c.get(f"/api/v1/investigations/{inv_id}").json()
    report = detail["investigation"]["report"]
    assert report["actions_taken"][0]["status"] == "executed"
    executed = [
        x
        for x in gw.calls
        if x[0] == "remediation" and x[2].get("approval_token") == "token-for-apr-test-alice"
    ]
    assert len(executed) == 1
    # Deciding again is refused.
    assert (
        c.post(
            f"/api/v1/investigations/{inv_id}/approval", json={**body, "confirm": True}
        ).status_code
        == 409
    )


def test_rejection_and_follow_up(client: tuple[TestClient, Any]) -> None:
    c, gw = client
    inv_id = c.post("/api/v1/investigations", json={"question": Q}).json()["id"]
    read_events(c, inv_id)
    r = c.post(
        f"/api/v1/investigations/{inv_id}/approval",
        json={"proposal_id": "p1", "decision": "reject", "approver": "bob", "confirm": True},
    )
    assert r.status_code == 200
    read_events(c, inv_id)
    report = c.get(f"/api/v1/investigations/{inv_id}").json()["investigation"]["report"]
    assert report["actions_taken"][0]["status"] == "rejected"
    assert not [x for x in gw.calls if "approval_token" in x[2]]
    follow = c.post(
        f"/api/v1/investigations/{inv_id}/follow-ups",
        json={"question": "Which customers were affected?"},
    )
    assert follow.status_code == 202
    child = c.get(f"/api/v1/investigations/{follow.json()['id']}").json()["investigation"]
    assert child["parent_id"] == inv_id
    assert inv_id in child["question"]


def test_cancel_while_awaiting_approval(client: tuple[TestClient, Any]) -> None:
    c, _ = client
    inv_id = c.post("/api/v1/investigations", json={"question": Q}).json()["id"]
    read_events(c, inv_id)
    assert c.post(f"/api/v1/investigations/{inv_id}/cancel").status_code == 200
    read_events(c, inv_id)
    record = c.get(f"/api/v1/investigations/{inv_id}").json()["investigation"]
    assert record["status"] in ("completed", "cancelled")
    assert record["report"]["actions_taken"][0]["status"] == "rejected"


def test_unknown_investigation_and_validation(client: tuple[TestClient, Any]) -> None:
    c, _ = client
    assert c.get("/api/v1/investigations/inv-nope").status_code == 404
    assert c.get("/api/v1/investigations/inv-nope/events").status_code == 404
    assert c.post("/api/v1/investigations", json={"question": "hi"}).status_code == 422


def test_platform_overview_and_service_detail(client: tuple[TestClient, Any]) -> None:
    c, _ = client
    ov = c.get("/api/v1/overview").json()
    assert ov["services"][0]["service"] == "orders-service"
    assert {d["name"] for d in ov["dependencies"]} >= {"redis", "postgres"}
    assert ov["deployments"][0]["id"] == "d-408"
    assert set(ov["golden_signals"]) == {"api-gateway", "orders-service", "fulfilment-worker"}
    assert ov["unavailable"] == []
    detail = c.get("/api/v1/services/orders-service").json()
    assert detail["metrics"]["service"] == "orders-service"
    assert {s["signal"] for s in detail["series"]} == {
        "request_rate",
        "error_rate",
        "latency_p99",
        "memory_bytes",
    }
    assert detail["logs"][0]["message"] == "ok"
    assert c.get("/api/v1/services/mainframe").status_code == 404


def test_metrics_series_and_compare(client: tuple[TestClient, Any]) -> None:
    c, _ = client
    assert {s["signal"] for s in c.get("/api/v1/metrics/catalog").json()["signals"]} >= {
        "error_rate",
        "queue_depth",
    }
    r = c.get(
        "/api/v1/metrics/series",
        params={"services": ["orders-service"], "signal": "error_rate", "since_minutes": 30},
    )
    assert r.status_code == 200
    assert len(r.json()["series"][0]["points"]) == 60
    cmp = c.get(
        "/api/v1/metrics/series",
        params={
            "services": ["orders-service"],
            "signal": "error_rate",
            "since_minutes": 20,
            "compare_offset_minutes": 30,
        },
    ).json()
    s = cmp["series"][0]
    assert len(s["points"]) == 21  # last 20 minutes (inclusive)
    assert s["baseline"][0][1] == pytest.approx(s["points"][0][1] - 30), (
        "baseline shifted onto the current window"
    )
    bad = c.get(
        "/api/v1/metrics/series",
        params={"services": ["fulfilment-worker"], "signal": "latency_p99"},
    )
    assert bad.status_code == 422


def test_platform_errors_become_502_and_partial_overview(client: tuple[TestClient, Any]) -> None:
    c, gw = client
    gw.fail = {"get_dependency_status", "list_recent_deployments"}
    ov = c.get("/api/v1/overview").json()
    assert ov["dependencies"] is None
    assert set(ov["unavailable"]) == {"dependencies", "deployments"}
    r = c.get("/api/v1/deployments")
    assert r.status_code == 502
    assert r.headers["content-type"].startswith("application/problem+json")


def test_settings_view_and_update(client: tuple[TestClient, Any]) -> None:
    c, _ = client
    s = c.get("/api/v1/settings").json()
    assert s["environment"]["simulated"] is True
    assert s["llm"]["api_key_configured"] is False
    assert s["integrations"]["mcp"]["reachable"] is False
    assert (
        c.put("/api/v1/settings/agent", json={**s["agent"], "reasoner": "llm"}).status_code == 422
    )
    r = c.put("/api/v1/settings/agent", json={**s["agent"], "max_rounds": 2})
    assert r.status_code == 200
    assert c.get("/api/v1/settings").json()["agent"]["max_rounds"] == 2
    assert c.put("/api/v1/settings/agent", json={**s["agent"], "max_rounds": 99}).status_code == 422


def test_knowledge_unavailable_is_503(client: tuple[TestClient, Any]) -> None:
    c, _ = client
    r = c.get("/api/v1/knowledge/incidents")
    assert r.status_code == 503
