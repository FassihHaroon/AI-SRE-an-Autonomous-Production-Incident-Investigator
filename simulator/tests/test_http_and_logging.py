"""Shared HTTP middleware (metrics labels, tracing, fault hooks) and the JSON log schema."""

from __future__ import annotations

import io
import json
import logging
from collections.abc import Iterator

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

from ai_sre_simulator.common import context
from ai_sre_simulator.common.faults import FaultType, RuntimeFault
from ai_sre_simulator.common.http import AppError, instrument_app
from ai_sre_simulator.common.logging import JsonFormatter, log, set_service_version
from ai_sre_simulator.common.runtime import Runtime, RuntimeState
from ai_sre_simulator.common.settings import Settings

REQUIRED_FIELDS = {
    "timestamp", "severity", "service", "environment", "simulated", "version",
    "trace_id", "span_id", "request_id", "endpoint", "message",
}  # fmt: skip


@pytest.fixture
def captured() -> Iterator[io.StringIO]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter("test-service", "simulated-prod"))
    root = logging.getLogger()
    root.addHandler(handler)
    old_level = root.level
    root.setLevel(logging.INFO)
    yield stream
    root.removeHandler(handler)
    root.setLevel(old_level)


def records(stream: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


def make_app(state: RuntimeState | None = None) -> tuple[FastAPI, Runtime]:
    runtime = Runtime(Settings.from_env("test-service"))
    if state is not None:
        runtime.state = state
    app = FastAPI()
    instrument_app(app, runtime)
    router = APIRouter(prefix="/api")

    @router.get("/items/{item_id}")
    async def item(item_id: int) -> dict[str, int]:
        if item_id == 404:
            raise AppError("item 404 not found", status_code=404, code="not_found")
        return {"item_id": item_id}

    app.include_router(router)
    return app, runtime


def test_log_line_has_required_fields_and_error_details(captured: io.StringIO) -> None:
    set_service_version("9.9.9")
    token = context.trace_id_var.set("a" * 32)
    try:
        try:
            raise ValueError("boom")
        except ValueError as exc:
            log(logging.getLogger("t"), logging.ERROR, "it failed", order_id=7, exc_info=exc)
    finally:
        context.trace_id_var.reset(token)
    (rec,) = records(captured)
    assert rec.keys() >= REQUIRED_FIELDS
    assert rec["severity"] == "ERROR"
    assert rec["simulated"] is True
    assert rec["version"] == "9.9.9"
    assert rec["trace_id"] == "a" * 32
    assert rec["order_id"] == 7
    error = rec["error"]
    assert isinstance(error, dict)
    assert error["type"] == "ValueError"
    assert error["message"] == "boom"
    assert "Traceback" in error["stack"]
    assert str(rec["timestamp"]).endswith("Z")


def test_route_label_is_the_template_and_trace_is_propagated(captured: io.StringIO) -> None:
    app, _ = make_app()
    trace = "0af7651916cd43dd8448eb211c80319c"
    with TestClient(app) as client:
        r = client.get("/api/items/123", headers={"traceparent": f"00-{trace}-b7ad6b7169203331-01"})
    assert r.status_code == 200
    assert r.headers["X-Simulated-Environment"] == "true"
    assert r.headers["traceresponse"].split("-")[1] == trace
    access = [x for x in records(captured) if x["logger"] == "http.access"][-1]
    assert access["endpoint"] == "GET /api/items/{item_id}"
    assert access["trace_id"] == trace
    assert access["status_code"] == 200
    sample = REGISTRY.get_sample_value(
        "http_server_requests_total",
        {"method": "GET", "route": "/api/items/{item_id}", "status": "200"},
    )
    assert sample is not None
    assert sample >= 1


def test_app_errors_are_logged_with_error_details(captured: io.StringIO) -> None:
    app, _ = make_app()
    with TestClient(app) as client:
        r = client.get("/api/items/404")
    assert r.status_code == 404
    assert r.json()["error"] == "not_found"
    warning = next(x for x in records(captured) if x["message"] == "item 404 not found")
    assert warning["severity"] == "WARNING"
    assert warning["error"] == {"type": "not_found", "message": "item 404 not found"}
    assert warning["endpoint"] == "GET /api/items/{item_id}"


def test_http_errors_fault_is_deterministic() -> None:
    fault = RuntimeFault(
        id="f-0001",
        type=FaultType.HTTP_ERRORS,
        params={"rate": 0.5, "status": 500, "route_prefix": "/api"},
        seed=11,
    )
    statuses = []
    for _ in range(2):
        from ai_sre_simulator.common.determinism import sampler

        sampler.reset()
        app, _ = make_app(RuntimeState(version="1.0.0", seed=11, faults=(fault,)))
        with TestClient(app) as client:
            statuses.append([client.get(f"/api/items/{i}").status_code for i in range(40)])
    assert statuses[0] == statuses[1]
    assert 5 < statuses[0].count(500) < 35


def test_canary_flag_fails_a_fraction_and_reports_the_canary_version(captured: io.StringIO) -> None:
    from ai_sre_simulator.common.determinism import sampler

    sampler.reset()
    canary = {"version": "2.0.0", "fraction": 0.5, "error": "ConfigError: bad value"}
    app, _ = make_app(RuntimeState(version="1.0.0", seed=3, flags={"canary": canary}))
    with TestClient(app) as client:
        responses = [client.get(f"/api/items/{i}") for i in range(30)]
    failed = [r for r in responses if r.status_code == 500]
    assert failed
    assert len(failed) < 30
    assert all(r.headers["X-Served-Version"] == "2.0.0" for r in failed)
    errors = [x for x in records(captured) if x["severity"] == "ERROR"]
    assert errors
    assert all(x["version"] == "2.0.0" for x in errors)
