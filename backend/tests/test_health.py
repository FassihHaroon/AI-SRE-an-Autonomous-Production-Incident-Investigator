import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ai_sre.api.deps import get_health_checks
from ai_sre.services.health import HealthCheck


async def _ok() -> None:
    return None


async def _boom() -> None:
    raise ConnectionRefusedError("connect to 10.0.0.5:5432 user=secret failed")


async def _slow() -> None:
    await asyncio.sleep(5)


def _use_checks(app: FastAPI, checks: dict[str, HealthCheck]) -> None:
    app.dependency_overrides[get_health_checks] = lambda: checks


def test_liveness_is_ok_without_dependencies(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_ok_when_all_dependencies_ok(app: FastAPI, client: TestClient) -> None:
    _use_checks(app, {"postgres": _ok, "redis": _ok})
    response = client.get("/readyz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert {c["name"] for c in body["components"]} == {"postgres", "redis"}


def test_readiness_503_and_no_error_message_leak(app: FastAPI, client: TestClient) -> None:
    _use_checks(app, {"postgres": _boom, "redis": _ok})
    response = client.get("/readyz")
    assert response.status_code == 503
    postgres = next(c for c in response.json()["components"] if c["name"] == "postgres")
    assert postgres["status"] == "error"
    assert postgres["detail"] == "ConnectionRefusedError"
    assert "secret" not in response.text


def test_readiness_times_out_slow_dependency(app: FastAPI, client: TestClient) -> None:
    _use_checks(app, {"redis": _slow})
    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["components"][0]["detail"] == "TimeoutError"


def test_readiness_with_real_but_unreachable_dependencies(client: TestClient) -> None:
    response = client.get("/readyz")
    assert response.status_code == 503
    assert all(c["status"] == "error" for c in response.json()["components"])


def test_system_status_always_200_and_reports_llm(app: FastAPI, client: TestClient) -> None:
    _use_checks(app, {"postgres": _boom})
    response = client.get("/api/v1/system/status")
    assert response.status_code == 200
    body = response.json()
    assert body["readiness"]["status"] == "not_ready"
    assert body["environment"] == "test"
    assert body["llm"] == {
        "provider": "gemini",
        "model": "gemini-3.5-flash-lite",
        "configured": False,
    }


def test_request_id_is_generated_and_echoed(client: TestClient) -> None:
    generated = client.get("/healthz").headers["x-request-id"]
    assert len(generated) == 32

    echoed = client.get("/healthz", headers={"X-Request-ID": "abc-123"})
    assert echoed.headers["x-request-id"] == "abc-123"


def test_malformed_request_id_is_replaced(client: TestClient) -> None:
    response = client.get("/healthz", headers={"X-Request-ID": "bad id\nwith newline"})
    assert response.headers["x-request-id"] != "bad id\nwith newline"


def test_unknown_route_returns_problem_json(client: TestClient) -> None:
    response = client.get("/nope")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["status"] == 404
