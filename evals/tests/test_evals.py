"""Deterministic tests of the evaluation harness: dataset, worlds, scoring, end-to-end runs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ai_sre.agent.findings import extract_findings
from ai_sre.agent.models import Report, RootCause
from ai_sre_evals.configs import CONFIGS
from ai_sre_evals.dataset import Cause, Dataset, load_dataset
from ai_sre_evals.report import render_markdown
from ai_sre_evals.runner import build_results, run_one
from ai_sre_evals.scoring import aggregate, root_cause_score
from ai_sre_evals.world import WorldGateway

DATASET = Path(__file__).resolve().parents[1] / "datasets" / "incidents.json"
REQUIRED_TAGS = {
    "misleading_symptoms",
    "missing_evidence",
    "conflicting_evidence",
    "multiple_failures",
    "recent_deployment",
    "database_bottleneck",
    "dependency_failure",
    "redis_failure",
    "queue_failure",
    "no_obvious_root_cause",
}


@pytest.fixture(scope="module")
def dataset() -> Dataset:
    return load_dataset(DATASET)


def test_dataset_covers_required_situations(dataset: Dataset) -> None:
    assert len(dataset.scenarios) >= 20
    tags = {t for s in dataset.scenarios for t in s.tags}
    assert tags >= REQUIRED_TAGS
    assert sum("misleading_symptoms" in s.tags for s in dataset.scenarios) >= 3
    for s in dataset.scenarios:
        assert s.expected.severity
        assert s.expected.remediation.action
        assert s.injected_fault.faults or s.expected.root_cause is None, s.id


async def test_world_derives_consistent_telemetry_from_faults(dataset: Dataset) -> None:
    s01 = next(s for s in dataset.scenarios if s.id.startswith("S01"))
    gw = WorldGateway(s01.world_spec())
    evidence = [
        await gw.call("database", "get_connection_pool_status", {}, 1),
        await gw.call("database", "get_slow_queries", {"top_k": 5}, 1),
        await gw.call("deployments", "list_recent_deployments", {"since_hours": 2}, 1),
        await gw.call("logs", "aggregate_errors", {"since_minutes": 30}, 1),
        await gw.call("infrastructure", "get_dependency_status", {}, 1),
    ]
    kinds = {f.kind for f in extract_findings(evidence)}
    assert {
        "pool_saturation",
        "slow_query",
        "deployment",
        "error_signature",
        "dependency_unhealthy",
    } <= kinds
    deploys = evidence[2].data["deployments"]  # type: ignore[index]
    assert [d["id"] for d in deploys] == ["d-481", "d-479"]  # newest first


async def test_world_missing_and_flaky_tools() -> None:
    world = {
        "faults": [
            {"type": "redis_failure", "target": "shop-cache", "onset_minutes_ago": 5, "params": {}}
        ],
        "unavailable": ["infrastructure.*"],
        "flaky": ["logs.aggregate_errors"],
    }
    gw = WorldGateway(world)
    down = await gw.call("infrastructure", "get_dependency_status", {}, 1)
    assert not down.ok and down.error_code == "backend_unavailable"
    first = await gw.call("logs", "aggregate_errors", {}, 1)
    assert first.ok  # the retry succeeded
    assert any(s["error_type"] == "CacheUnavailableError" for s in first.data["signatures"])  # type: ignore[index]


async def test_compare_windows_distinguishes_cause_from_late_deploy(dataset: Dataset) -> None:
    s14 = next(s for s in dataset.scenarios if s.id.startswith("S14"))
    gw = WorldGateway(s14.world_spec())
    deploy_at = gw.now.replace(microsecond=0)
    from datetime import timedelta

    split = (deploy_at - timedelta(minutes=6)).isoformat()
    cmp = await gw.call(
        "metrics",
        "compare_metric_windows",
        {"service": "orders-service", "signal": "error_rate", "split_at": split},
        2,
    )
    assert cmp.data["direction"] == "flat"  # type: ignore[index]  # errors predate the deploy


def _rc(category: str, component: str, trigger: str | None = None, band: str = "high") -> RootCause:
    return RootCause(
        hypothesis_id="h",
        category=category,  # type: ignore[arg-type]
        component=component,
        title="t",
        trigger_ref=trigger,
        explanation="e",
        confidence=0.8,
        band=band,  # type: ignore[arg-type]
        evidence=[],
    )


def _report(rc: RootCause | None) -> Report:
    return Report(
        investigation_id="i",
        question="q",
        summary="s",
        severity="SEV2",
        affected_services=[],
        root_cause=rc,
        alternatives=[],
        timeline=[],
        evidence=[],
        recommendations=[],
        actions_taken=[],
        verification=None,
        limitations=[],
        stop_reason="sufficient_evidence",
        rounds=1,
        tool_calls=1,
        markdown="",
    )


def test_root_cause_scoring_rules(dataset: Dataset) -> None:
    s01 = next(s for s in dataset.scenarios if s.id.startswith("S01"))
    assert root_cause_score(_report(_rc("bad_deploy", "orders-service", "d-481")), s01)[0] == 1.0
    assert root_cause_score(_report(_rc("bad_deploy", "orders-service", "d-479")), s01)[0] == 0.0
    assert root_cause_score(_report(_rc("database_degradation", "postgres")), s01)[0] == 0.5
    assert root_cause_score(_report(None), s01)[0] == 0.0
    s18 = next(s for s in dataset.scenarios if s.id.startswith("S18"))
    assert root_cause_score(_report(None), s18)[0] == 1.0
    assert root_cause_score(_report(_rc("capacity", "api-gateway", band="low")), s18)[0] == 1.0
    assert root_cause_score(_report(_rc("capacity", "api-gateway", band="high")), s18)[0] == 0.0
    s16 = next(s for s in dataset.scenarios if s.id.startswith("S16"))
    assert Cause(category="cache_failure", component="redis") in s16.expected.also_correct
    assert root_cause_score(_report(_rc("cache_failure", "redis")), s16)[0] == 1.0


async def test_end_to_end_run_is_deterministic(dataset: Dataset) -> None:
    picked = [s for s in dataset.scenarios if s.id[:3] in ("S01", "S13", "S18")]
    config = CONFIGS["rules-baseline"]

    async def run() -> list[dict[str, Any]]:
        return [await run_one(s, config, run_id="test") for s in picked]

    first, second = await run(), await run()
    keys = (
        "root_cause_score",
        "severity",
        "remediation_correct",
        "tools_called",
        "evidence_recall",
    )
    assert [{k: r[k] for k in keys} for r in first] == [{k: r[k] for k in keys} for r in second]
    by_id = {r["scenario"][:3]: r for r in first}
    assert by_id["S01"]["root_cause_score"] == 1.0
    assert by_id["S01"]["completed"] and not by_id["S01"]["hallucinated"]
    assert by_id["S13"]["tool_errors"] >= 1  # infrastructure tools are down in S13
    assert all(r["latency_ms"] > 0 for r in first)

    results = build_results("20261123-000000", dataset, [config], first, picked)
    metrics = results["configs"][0]["metrics"]
    assert metrics["n"] == 3 and metrics["completion_rate"] == 1.0
    assert aggregate([])["n"] == 0
    md = render_markdown(results)
    assert "## Configurations compared" in md and "S01" in md
