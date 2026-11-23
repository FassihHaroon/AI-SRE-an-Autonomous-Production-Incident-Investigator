"""Tracing: spans for nodes, tool calls and LLM calls; redaction; summary; trace and eval APIs."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ai_sre.agent.gateway import ToolGateway
from ai_sre.integrations.llm.base import LLMMessage, LLMRequest, LLMUsage
from ai_sre.integrations.llm.scripted import ScriptedProvider
from ai_sre.integrations.llm.traced import TracedLLMProvider
from ai_sre.observability.tracing import (
    collector,
    estimate_cost_usd,
    investigation_span,
    redact_text,
    sanitize_args,
    summarize,
)

from .test_api import Q, client, read_events  # noqa: F401 - client is a fixture


def test_redaction_removes_credentials_and_personal_data() -> None:
    text = (
        "token=abc123def456 Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.sflKxwRJSMeKKF2QT4fw "
        "key AIzaSyD-abcdefghijklmnopqrstuv mail ops@example.com card 4111 1111 1111 1111"
    )
    out = redact_text(text)
    for secret in ("abc123def456", "eyJhbGci", "AIzaSyD", "ops@example.com", "4111 1111"):
        assert secret not in out
    assert "[REDACTED" in out
    assert len(redact_text("x" * 1000)) == 300


def test_sanitize_args_drops_sensitive_keys_and_bounds_size() -> None:
    args = {
        "service": "orders-service",
        "approval_token": "secret-token",
        "nested": {"api_key": "k", "q": "find ops@example.com"},
        "items": list(range(50)),
    }
    out = sanitize_args(args)
    assert out["service"] == "orders-service"
    assert out["approval_token"] == "[REDACTED]"
    assert out["nested"]["api_key"] == "[REDACTED]"
    assert "ops@example.com" not in out["nested"]["q"]
    assert len(out["items"]) == 10


def test_cost_estimate_by_model_family(monkeypatch: pytest.MonkeyPatch) -> None:
    assert estimate_cost_usd("scripted", 1000, 1000) == 0.0
    assert estimate_cost_usd("gemini-3.5-flash-lite", 1_000_000, 0) == pytest.approx(0.10)
    monkeypatch.setenv("AI_SRE_LLM_PRICE_INPUT_PER_MTOK", "1")
    monkeypatch.setenv("AI_SRE_LLM_PRICE_OUTPUT_PER_MTOK", "2")
    assert estimate_cost_usd("anything", 1_000_000, 1_000_000) == pytest.approx(3.0)


class _FlakyClient:
    """MCP client double: first call fails with a transient error, second succeeds."""

    calls = 0

    async def call_tool(self, tool: str, args: dict[str, Any]) -> Any:
        _FlakyClient.calls += 1
        if _FlakyClient.calls == 1:
            text = json.dumps({"code": "timeout", "message": "backend timed out"})
            return SimpleNamespace(is_error=True, content=[SimpleNamespace(text=text)])
        return SimpleNamespace(is_error=False, structured_content={"summary": "ok", "rows": [1, 2]})


@asynccontextmanager
async def _connect(server: str) -> AsyncIterator[_FlakyClient]:
    yield _FlakyClient()


async def test_gateway_and_llm_calls_produce_spans_under_the_investigation() -> None:
    _FlakyClient.calls = 0
    gateway = ToolGateway(_connect, retries=1, backoff_s=0)
    llm = TracedLLMProvider(ScriptedProvider(['{"x": 1}'], model="gemini-3.5-flash-lite"))
    llm.inner._responses[0] = '{"x": 1}'  # type: ignore[attr-defined]

    async def fake_generate(request: LLMRequest) -> Any:
        return SimpleNamespace(
            text="{}",
            model="gemini-3.5-flash-lite",
            usage=LLMUsage(input_tokens=1200, output_tokens=300),
        )

    llm.inner.generate = fake_generate  # type: ignore[method-assign]

    with investigation_span(
        "inv-trace-1", "why is token=abc123 failing?", reasoner="rules"
    ) as span:
        ev = await gateway.call("metrics", "get_service_metrics", {"service": "orders-service"}, 1)
        await llm.generate(
            LLMRequest(messages=[LLMMessage(role="user", content="hi")], node="planning")
        )
        span.set_attribute("ai_sre.result.status", "completed")

    assert ev.ok
    spans = collector().drain("inv-trace-1")
    kinds = {s["kind"] for s in spans}
    assert kinds == {"investigation", "tool", "llm"}
    root = next(s for s in spans if s["kind"] == "investigation")
    tool = next(s for s in spans if s["kind"] == "tool")
    llm_span = next(s for s in spans if s["kind"] == "llm")
    assert tool["parent_id"] == root["span_id"] == llm_span["parent_id"]
    assert "abc123" not in root["attributes"]["ai_sre.query"]
    assert tool["attributes"]["ai_sre.tool.retries"] == 1
    assert tool["attributes"]["ai_sre.evidence.id"] == ev.id
    assert llm_span["attributes"]["gen_ai.usage.input_tokens"] == 1200
    assert "hi" not in json.dumps(llm_span["attributes"])  # prompt text is never recorded

    summary = summarize(spans)
    assert summary["tools"]["calls"] == 1
    assert summary["retries"]["tools"] == 1
    assert summary["llm"]["total_tokens"] == 1500
    assert summary["llm"]["cost_usd"] == pytest.approx(
        estimate_cost_usd("gemini-3.5-flash-lite", 1200, 300)
    )
    assert summary["result"]["status"] == "completed"


def test_investigation_trace_endpoint(client: tuple[TestClient, Any]) -> None:
    c, _ = client
    inv_id = c.post("/api/v1/investigations", json={"question": Q}).json()["id"]
    read_events(c, inv_id)  # until it pauses for approval
    trace = c.get(f"/api/v1/investigations/{inv_id}/trace").json()
    assert trace["question"] == Q
    names = {s["name"] for s in trace["spans"]}
    assert "investigation.run" in names
    assert {"node.intake", "node.planning", "node.tool_execution"} <= names
    nodes = trace["summary"]["nodes"]
    assert nodes["intake"]["executions"] == 1
    assert trace["summary"]["result"]["status"] == "awaiting_approval"
    assert c.get("/api/v1/investigations/nope/trace").status_code == 404


def test_evaluation_results_api(
    client: tuple[TestClient, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    c, _ = client
    monkeypatch.setenv("AI_SRE_EVAL_RESULTS_DIR", str(tmp_path))
    assert c.get("/api/v1/evaluations").json()["runs"] == []
    run = tmp_path / "20261123-120000"
    run.mkdir()
    (run / "results.json").write_text(json.dumps({"run_id": "20261123-120000", "configs": []}))
    (tmp_path / "index.json").write_text(json.dumps({"runs": [{"run_id": "20261123-120000"}]}))
    assert c.get("/api/v1/evaluations").json()["runs"][0]["run_id"] == "20261123-120000"
    assert c.get("/api/v1/evaluations/20261123-120000").json()["run_id"] == "20261123-120000"
    assert c.get("/api/v1/evaluations/..%2F..%2Fetc").status_code == 404
    assert c.get("/api/v1/evaluations/20990101-000000").status_code == 404
