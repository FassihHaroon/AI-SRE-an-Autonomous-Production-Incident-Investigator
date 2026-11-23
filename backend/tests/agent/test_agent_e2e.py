"""End-to-end: the investigation graph against the running simulated environment.

Needs: docker compose -f docker-compose.sim.yml up -d --wait, and the platform Postgres
(docker compose up -d postgres) for checkpoint persistence.  Run: uv run poe test-agent-e2e

Real MCP servers (uvicorn, Streamable HTTP, bearer tokens) are started in-process; the graph uses
the HTTP gateway. Approvals in these tests are given by an explicitly synthetic test approver
through the admin endpoint, exactly as a human would.
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from collections.abc import Awaitable, Iterator
from typing import Any

import httpx
import pytest
import uvicorn
from pydantic import SecretStr

from ai_sre.agent.models import Decision, Limits
from ai_sre.agent.reasoner import RuleBasedReasoner
from ai_sre.agent.runner import RunResult, investigator
from ai_sre.config import REPO_ROOT, get_settings
from ai_sre.mcp_servers.app import create_app
from ai_sre.mcp_servers.settings import McpSettings
from ai_sre_simulator.testing import CHAOS, DEPLOYER, GATEWAY, Loki, Prometheus, Sim

pytestmark = pytest.mark.sim

READ, REMEDIATE, ADMIN = "e2e-read", "e2e-remediate", "e2e-admin"
TEST_APPROVER = "integration-test-approver"


def run[T](coro: Awaitable[T]) -> T:
    # psycopg's async mode needs a selector loop on Windows.
    return asyncio.run(coro, loop_factory=asyncio.SelectorEventLoop)  # type: ignore[arg-type]


@pytest.fixture(scope="module")
def mcp_url() -> Iterator[str]:
    try:
        httpx.get(f"{CHAOS}/healthz", timeout=3).raise_for_status()
    except httpx.HTTPError:
        pytest.skip("simulated environment not running")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    settings = McpSettings(
        read_token=SecretStr(READ),
        remediation_token=SecretStr(REMEDIATE),
        admin_token=SecretStr(ADMIN),
        approval_secret=SecretStr("e2e-approval-secret"),
        incidents_dir=REPO_ROOT / "knowledge" / "incidents",
        remediation_enabled=True,
    )
    server = uvicorn.Server(
        uvicorn.Config(create_app(settings).asgi, host="127.0.0.1", port=port, log_level="warning")
    )
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
    sim_ = Sim(clients[0], clients[1], clients[2], Prometheus(plain), Loki(plain))
    sim_.clear()
    for c in [*clients, plain]:
        c.close()


def postgres_dsn() -> str | None:
    dsn = get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://")
    try:
        import psycopg

        psycopg.connect(dsn, connect_timeout=3).close()
    except Exception:
        return None
    return dsn


async def investigate(mcp_url: str, question: str, limits: Limits, dsn: str | None) -> RunResult:
    async with investigator(
        mcp_url=mcp_url,
        read_token=READ,
        remediation_token=REMEDIATE,
        reasoner=RuleBasedReasoner(),
        postgres_dsn=dsn,
    ) as inv:
        return await inv.start(question, limits=limits)


async def resume(
    mcp_url: str, inv_id: str, decisions: list[Decision], dsn: str | None
) -> RunResult:
    async with investigator(
        mcp_url=mcp_url,
        read_token=READ,
        remediation_token=REMEDIATE,
        reasoner=RuleBasedReasoner(),
        postgres_dsn=dsn,
    ) as inv:
        return await inv.resume(inv_id, decisions)


def start_scenario(sim: Sim, scenario: str, ready: tuple[str, Any]) -> None:
    sim.reset()
    # Start from a calm baseline, as a real incident would: no 5xx for 3 minutes and normal
    # latency, so the previous scenario cannot bleed into this one's anomaly baselines.
    sim.prom.wait_for(
        'sum(increase(http_server_requests_total{status=~"5.."}[3m])) or vector(0)',
        lambda v: v == 0,
        timeout=420,
        what="calm baseline before the incident (no 5xx for 3 min)",
    )
    sim.prom.wait_for(
        'max_over_time(histogram_quantile(0.99, sum by (le) (rate(http_server_request_duration_seconds_bucket{service="orders-service"}[1m])))[2m:15s])',
        lambda v: v < 0.6,
        timeout=300,
        what="normal orders latency for 2 min",
    )
    sim.wait_run_applied(sim.trigger(scenario, speed=20)["id"])
    expr, check = ready
    sim.prom.wait_for(expr, check, timeout=150, what=f"{scenario} symptoms")
    time.sleep(20)  # let the anomaly window fill


def test_scenario_a_investigate_approve_resume_rollback_verify(mcp_url: str, sim: Sim) -> None:
    dsn = postgres_dsn()
    if dsn is None:
        pytest.skip("platform Postgres not reachable (docker compose up -d postgres)")
    start_scenario(
        sim,
        "orders-n-plus-one-deploy",
        ('db_pool_connections_in_use{service="orders-service"}', lambda v: v >= 8),
    )
    question = "Investigate why our orders API has been returning 500 errors and experiencing high latency for the last 20 minutes."
    first = run(investigate(mcp_url, question, Limits(verification_wait_s=40), dsn))

    assert first.status == "awaiting_approval", first
    approval = first.approval
    assert approval is not None
    rc = approval["root_cause"]
    assert (rc["category"], rc["component"], rc["trigger_ref"]) == (
        "bad_deploy",
        "orders-service",
        "d-408",
    )
    assert rc["band"] in ("high", "medium")
    proposal = approval["proposals"][0]
    assert proposal["action"] == "rollback_deployment_simulation"
    time.sleep(3)
    assert sim.version("orders-service") == "2.4.0", "no change before approval"

    # A human approves through the MCP admin endpoint (the agent never holds this token).
    r = httpx.post(
        f"{mcp_url}/approvals/{proposal['approval_request_id']}/approve",
        json={"approver": TEST_APPROVER},
        headers={"Authorization": f"Bearer {ADMIN}"},
    )
    assert r.status_code == 200, r.text
    decision = Decision(
        proposal_id=proposal["id"],
        approved=True,
        approver=TEST_APPROVER,
        approval_token=r.json()["approval_token"],
    )

    # Resume with a fresh investigator: state comes back from the Postgres checkpoints.
    final = run(resume(mcp_url, first.investigation_id, [decision], dsn))
    assert final.status == "completed"
    report = final.report
    assert report is not None
    assert [p.status for p in report.actions_taken] == ["executed"]
    assert report.verification is not None
    assert report.verification.verdict in ("resolved", "improved"), report.verification
    assert sim.version("orders-service") == "2.3.1"
    assert any(a.trigger_ref == "d-407" for a in report.alternatives), (
        "red herring rejected but listed"
    )
    assert report.root_cause is not None
    for section in (
        "## Root cause",
        "## Timeline",
        "## Evidence",
        "## Actions taken",
        "## Verification",
    ):
        assert section in report.markdown


@pytest.mark.parametrize(
    ("scenario", "ready", "question", "expected"),
    [
        (
            "redis-outage",
            ("redis_up", lambda v: v == 0),
            "Carts are failing and the shop is slow since a few minutes ago, what is going on?",
            ("cache_failure", "redis"),
        ),
        (
            "payments-provider-errors",
            (
                'sum(rate(http_client_requests_total{peer="payments-provider",status="503"}[30s]))',
                lambda v: v > 0,
            ),
            "Customers report that placing orders fails in the last 15 minutes. Why?",
            ("dependency_failure", "payments-provider"),
        ),
        (
            "worker-deploy-backlog",
            ('queue_depth{queue="fulfilment"}', lambda v: v > 30),
            "Orders are not being fulfilled, the fulfilment queue keeps growing for the last 15 minutes.",
            ("bad_deploy", "fulfilment-worker"),
        ),
    ],
)
def test_other_scenarios_root_cause(
    mcp_url: str,
    sim: Sim,
    scenario: str,
    ready: tuple[str, Any],
    question: str,
    expected: tuple[str, str],
) -> None:
    start_scenario(sim, scenario, ready)
    result = run(investigate(mcp_url, question, Limits(verification_wait_s=0), None))
    rc = (result.report.root_cause if result.report else None) or (
        type("RC", (), result.approval["root_cause"]) if result.approval else None  # type: ignore[index]
    )
    assert rc is not None, result
    detail = result.report.markdown if result.report else result.approval
    assert (rc.category, rc.component) == expected, detail
    if result.status == "awaiting_approval":  # deploy-caused: rollback proposed, nothing executed
        assert result.approval is not None
        assert result.approval["proposals"][0]["action"] == "rollback_deployment_simulation"
    else:
        assert result.report is not None
        assert all(p.action == "manual" for p in result.report.recommendations)
    sim.clear()
