"""Repeatability and reset behaviour of the live environment."""

from __future__ import annotations

import pytest

from ai_sre_simulator.testing import Sim

pytestmark = pytest.mark.sim


def _scenario_fingerprint(sim: Sim, scenario: str) -> list[tuple[str, ...]]:
    sim.reset()
    run = sim.trigger(scenario, speed=20)
    run = sim.wait_run_applied(run["id"])
    deployments = [(d["id"], d["service"], d["version"], d["kind"]) for d in sim.deployments(3)]
    faults = [
        (f["id"], f["type"], f["target"], str(sorted(f["params"].items())))
        for f in sim.chaos.get("/api/v1/faults").json()
    ]
    steps = [(s["description"], str(s["deployment_id"]), str(s["fault_id"])) for s in run["steps"]]
    sim.clear()
    return [(run["id"],), *deployments, *faults, *steps]


@pytest.mark.parametrize("scenario", ["orders-n-plus-one-deploy", "redis-outage"])
def test_same_scenario_after_reset_produces_identical_records(sim: Sim, scenario: str) -> None:
    first = _scenario_fingerprint(sim, scenario)
    second = _scenario_fingerprint(sim, scenario)
    assert first == second


def test_overlapping_scenarios_are_refused(fresh: Sim) -> None:
    fresh.trigger("payments-provider-errors")
    r = fresh.chaos.post("/api/v1/scenarios/redis-outage/trigger", json={})
    assert r.status_code == 409
    assert "clear it first" in r.text


def test_reset_restores_baseline(sim: Sim) -> None:
    sim.trigger("worker-deploy-backlog")
    sim.inject("api_latency", "api-gateway", delay_ms=200)
    result = sim.reset()
    assert result["status"] == "reset"
    status = sim.chaos.get("/api/v1/status").json()
    assert status["active_runs"] == []
    assert status["active_faults"] == []
    assert status["simulated"] is True
    assert [d["id"] for d in sim.deployments(10)] == [f"d-{n}" for n in range(406, 400, -1)]
    sim.wait_version("fulfilment-worker", "1.6.2")
    sim.prom.wait_for('queue_depth{queue="fulfilment"}', lambda v: v < 5, what="queue drained")
