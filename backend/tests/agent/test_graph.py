"""Investigation graph: planning, tool selection, errors, replanning, loop limits, scoring,
approval interrupts, resumption and the final report (deterministic, no services needed)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from ai_sre.agent.graph import Deps, build_graph
from ai_sre.agent.hypotheses import band_for, score, sufficient
from ai_sre.agent.models import Decision, Hypothesis, IncidentScope, Limits, PlannedCall
from ai_sre.agent.reasoner import LLMReasoner, RuleBasedReasoner, parse_scope, triage_calls
from ai_sre.agent.runner import Investigator, RunResult, serializer
from ai_sre.integrations.llm.scripted import ScriptedProvider

Q = "Investigate why our orders API has been returning 500 errors and experiencing high latency for the last 20 minutes."
GatewayFactory = Callable[..., Any]
# Round-1 evidence of the canned scenario is already sufficient; a strict threshold forces round 2.
STRICT = Limits(sufficient_confidence=0.95, verification_wait_s=0)


def investigator(gw: Any, reasoner: Any = None) -> Investigator:
    graph = build_graph(Deps(gateway=gw, reasoner=reasoner or RuleBasedReasoner()))
    return Investigator(graph.compile(checkpointer=InMemorySaver(serde=serializer())))


async def start(
    gw: Any, limits: Limits | None = None, reasoner: Any = None
) -> tuple[Investigator, RunResult]:
    inv = investigator(gw, reasoner)
    return inv, await inv.start(
        Q, limits=limits or Limits(verification_wait_s=0), investigation_id="inv-test"
    )


# --------------------------------------------------------------------------- intake + planning
def test_intake_extracts_services_window_severity_symptoms() -> None:
    scope = parse_scope(Q)
    assert scope.services[:2] == ["api-gateway", "orders-service"]
    assert scope.window_minutes == 20
    assert scope.severity == "SEV2"
    assert {"errors", "latency"} <= set(scope.symptoms)
    assert scope.missing_information == []


def test_intake_records_missing_information_and_assumptions() -> None:
    scope = parse_scope("something feels off")
    assert scope.window_minutes == 30
    assert "time window" in scope.missing_information
    assert "affected service" in scope.missing_information
    assert scope.assumptions


def test_first_plan_is_a_broad_triage() -> None:
    tools = {(c.server, c.tool) for c in triage_calls(parse_scope(Q))}
    assert {
        ("metrics", "detect_metric_anomalies"),
        ("deployments", "list_recent_deployments"),
        ("logs", "aggregate_errors"),
        ("infrastructure", "get_dependency_status"),
        ("incidents", "search_incident_history"),
        ("database", "get_connection_pool_status"),
    } <= tools


# --------------------------------------------------------------------------- happy path + approval
async def test_finds_root_cause_and_pauses_for_approval(gateway_factory: GatewayFactory) -> None:
    gw = gateway_factory()
    _, result = await start(gw)
    assert result.status == "awaiting_approval"
    approval = result.approval
    assert approval is not None
    assert approval["root_cause"]["hypothesis_id"] == "bad_deploy:orders-service:d-408"
    assert approval["root_cause"]["band"] in ("high", "medium")
    assert [p["action"] for p in approval["proposals"]] == ["rollback_deployment_simulation"]
    executed = [c for c in gw.calls if c[0] == "remediation" and "approval_token" in c[2]]
    assert executed == [], "nothing may execute before approval"


async def test_replanning_runs_a_targeted_second_round(gateway_factory: GatewayFactory) -> None:
    gw = gateway_factory()
    # A strict threshold makes round-1 evidence insufficient, forcing a targeted second round.
    inv, _ = await start(gw, STRICT)
    state = (await inv.graph.aget_state(inv.config("inv-test"))).values
    rounds = {e.round for e in state["evidence"]}
    assert rounds >= {1, 2}
    round2 = {e.tool for e in state["evidence"] if e.round == 2}
    assert {"get_deployment_details", "get_slow_queries"} <= round2
    keys = [f"{c[0]}.{c[1]}:{c[2]}" for c in gw.calls if c[0] != "remediation"]
    assert len(keys) == len(set(keys)) + 1, "no duplicate calls except the verification baseline"


async def test_resume_after_approval_executes_verifies_and_reports(
    gateway_factory: GatewayFactory,
) -> None:
    gw = gateway_factory()
    inv, _ = await start(gw)
    result = await inv.resume(
        "inv-test",
        [Decision(proposal_id="p1", approved=True, approver="alice", approval_token="tok")],
    )
    assert result.status == "completed"
    report = result.report
    assert report is not None
    executed = [
        c for c in gw.calls if c[0] == "remediation" and c[2].get("approval_token") == "tok"
    ]
    assert len(executed) == 1
    assert [p.status for p in report.actions_taken] == ["executed"]
    assert report.verification is not None
    assert report.root_cause is not None
    assert report.root_cause.trigger_ref == "d-408"
    assert "## Root cause" in report.markdown
    assert "## Verification" in report.markdown


async def test_rejected_approval_executes_nothing(gateway_factory: GatewayFactory) -> None:
    gw = gateway_factory()
    inv, _ = await start(gw)
    result = await inv.resume(
        "inv-test", [Decision(proposal_id="p1", approved=False, approver="alice")]
    )
    assert result.report is not None
    assert [p.status for p in result.report.actions_taken] == ["rejected"]
    assert not [c for c in gw.calls if "approval_token" in c[2]]
    assert result.report.verification is None


@pytest.mark.parametrize(
    "decision",
    [
        Decision(proposal_id="p1", approved=True, approver="agent", approval_token="tok"),
        Decision(proposal_id="p1", approved=True, approver="alice", approval_token=None),
    ],
)
async def test_approval_without_human_or_token_is_refused(
    gateway_factory: GatewayFactory, decision: Decision
) -> None:
    gw = gateway_factory()
    inv, _ = await start(gw)
    result = await inv.resume("inv-test", [decision])
    assert result.report is not None
    assert result.report.actions_taken[0].status == "rejected"
    assert not [c for c in gw.calls if "approval_token" in c[2]]


async def test_failed_remediation_is_never_reported_as_success(
    gateway_factory: GatewayFactory,
) -> None:
    gw = gateway_factory(remediation="fail")
    inv, _ = await start(gw)
    result = await inv.resume(
        "inv-test",
        [Decision(proposal_id="p1", approved=True, approver="alice", approval_token="tok")],
    )
    assert result.report is not None
    assert result.report.actions_taken[0].status == "failed"
    assert result.report.verification is None
    assert "**failed**" in result.report.markdown


async def test_remediation_disabled_means_no_approval_request(
    gateway_factory: GatewayFactory,
) -> None:
    gw = gateway_factory(remediation="disabled")
    _, result = await start(gw)
    assert result.status == "completed"
    assert result.report is not None
    assert result.report.recommendations[0].status == "not_executable"
    assert any("remediation is disabled" in x for x in result.report.limitations)
    assert not [c for c in gw.calls if c[0] == "remediation"]


# --------------------------------------------------------------------------- errors and limits
async def test_tool_errors_become_limitations(gateway_factory: GatewayFactory) -> None:
    gw = gateway_factory(
        fail={"get_slow_queries", "get_database_health", "aggregate_errors"}, remediation="disabled"
    )
    _, result = await start(gw, STRICT)
    report = result.report
    assert report is not None
    assert any("get_slow_queries failed" in x for x in report.limitations)
    assert any(e["summary"].startswith("FAILED") for e in report.evidence)
    assert report.root_cause is not None


async def test_tools_unavailable_stops_before_investigating(
    gateway_factory: GatewayFactory,
) -> None:
    gw = gateway_factory(registry=False)
    _, result = await start(gw)
    assert result.report is not None
    assert result.report.stop_reason == "tools_unavailable"
    assert gw.calls == []


async def test_tool_budget_is_enforced(gateway_factory: GatewayFactory) -> None:
    gw = gateway_factory(remediation="disabled")
    _, result = await start(
        gw, Limits(max_tool_calls=4, sufficient_confidence=0.95, verification_wait_s=0)
    )
    assert result.report is not None
    investigative = [c for c in gw.calls if c[0] != "remediation"]
    assert len(investigative) <= 4 + 1  # + verification baseline
    assert result.report.stop_reason == "tool_budget"


class _EndlessReasoner(RuleBasedReasoner):
    """Always proposes new calls: the graph, not the reasoner, must stop the loop."""

    async def plan(
        self, scope: IncidentScope, hypotheses: list[Hypothesis], round_: int, done: set[str]
    ) -> tuple[str, list[PlannedCall]]:
        return "keep digging", [
            PlannedCall(
                server="logs",
                tool="search_logs",
                arguments={"contains": f"x{round_}-{i}"},
                purpose="more",
            )
            for i in range(3)
        ]


async def test_loop_prevention_max_rounds(gateway_factory: GatewayFactory) -> None:
    gw = gateway_factory(remediation="disabled")
    gw.data["search_logs"] = lambda a: {"summary": "nothing", "entries": []}
    _, result = await start(
        gw, Limits(max_rounds=3, max_tool_calls=100, verification_wait_s=0), _EndlessReasoner()
    )
    assert result.report is not None
    assert result.report.stop_reason == "max_rounds"
    assert result.report.rounds == 3
    assert len(gw.calls) == 9


async def test_cancellation_stops_the_investigation(gateway_factory: GatewayFactory) -> None:
    gw = gateway_factory()
    inv = investigator(gw)
    from datetime import UTC, datetime

    await inv.graph.ainvoke(
        {
            "investigation_id": "inv-c",
            "question": Q,
            "started_at": datetime.now(UTC),
            "limits": Limits(),
            "cancel_requested": True,
        },
        inv.config("inv-c"),
    )
    result = await inv.result("inv-c")
    assert result.report is not None
    assert result.report.stop_reason == "cancelled"
    assert [c for c in gw.calls if c[0] != "remediation"] == []


async def test_node_crash_is_recovered_into_a_partial_report(
    gateway_factory: GatewayFactory,
) -> None:
    gw = gateway_factory(remediation="disabled")
    original = gw.call

    async def explode(server: str, tool: str, args: dict[str, Any], round_: int) -> Any:
        if tool == "get_slow_queries":
            raise RuntimeError("boom")
        return await original(server, tool, args, round_)

    gw.call = explode  # type: ignore[method-assign]
    _, result = await start(gw, STRICT)
    assert result.report is not None
    assert result.report.stop_reason == "error"
    assert any("tool_execution failed" in x for x in result.report.limitations)


# --------------------------------------------------------------------------- scoring
def _h(**kw: Any) -> Hypothesis:
    base: dict[str, Any] = {
        "id": "x",
        "category": "bad_deploy",
        "component": "orders-service",
        "title": "t",
    }
    return Hypothesis(**{**base, **kw})


def test_confidence_rubric_components() -> None:
    weak = score(_h(sources=["logs"]), 0)
    strong = score(
        _h(
            sources=["logs", "metrics", "database"],
            temporal="strong",
            mechanism="strong",
            historical_match="INC-1",
        ),
        0,
    )
    assert strong.confidence > weak.confidence
    assert strong.band == "high"
    assert (
        score(_h(sources=["logs", "metrics", "database"], temporal="violation"), 0).confidence
        < score(_h(sources=["logs", "metrics", "database"], temporal="plausible"), 0).confidence
    )
    many = score(_h(sources=["logs"], contradicting=["a", "b", "c", "d"]), 0)
    assert many.breakdown.contradictions == -0.30
    gaps = score(_h(sources=["logs"]), 10)
    assert gaps.breakdown.coverage_gaps == -0.15
    assert band_for(0.75) == "high"
    assert band_for(0.5) == "medium"
    assert band_for(0.3) == "low"
    assert band_for(0.29) == "insufficient"


def test_confidence_increases_monotonically_with_corroboration() -> None:
    values = [
        score(_h(sources=s), 0).confidence
        for s in ([], ["a"], ["a", "b"], ["a", "b", "c"], ["a", "b", "c", "d"])
    ]
    assert values == sorted(values)


def test_sufficiency_requires_margin_over_runner_up() -> None:
    a = _h(id="a", confidence=0.8)
    assert sufficient([a], 0.75)
    assert not sufficient([a, _h(id="b", confidence=0.75)], 0.75)
    assert not sufficient([_h(id="a", confidence=0.6)], 0.75)


# --------------------------------------------------------------------------- report
async def test_report_cites_only_existing_evidence(gateway_factory: GatewayFactory) -> None:
    gw = gateway_factory(remediation="disabled")
    inv, result = await start(gw)
    report = result.report
    assert report is not None
    state = (await inv.graph.aget_state(inv.config("inv-test"))).values
    ids = {e.id for e in state["evidence"]}
    assert report.root_cause is not None
    assert set(report.root_cause.evidence) <= ids
    for alt in report.alternatives:
        assert set(alt.evidence) <= ids
    for section in (
        "## Summary",
        "## Root cause",
        "## Alternative hypotheses",
        "## Timeline",
        "## Evidence",
        "## Recommended remediation",
        "## Actions taken",
        "## Limitations",
    ):
        assert section in report.markdown
    assert report.severity == "SEV2"
    assert {"orders-service", "api-gateway"} <= set(report.affected_services)
    assert any(a.trigger_ref == "d-407" for a in report.alternatives), (
        "the red herring is listed as an alternative"
    )


# --------------------------------------------------------------------------- LLM reasoner
async def test_llm_reasoner_uses_structured_output_and_validates_plan(
    gateway_factory: GatewayFactory,
) -> None:
    llm = ScriptedProvider(
        [
            '{"services": ["orders-service", "mainframe"], "window_minutes": 20, "severity": "SEV2", '
            '"symptoms": ["errors"], "missing_information": []}',
            '{"rationale": "check db", "calls": [{"tool": "database.get_slow_queries", "arguments": {"top_k": 3}, '
            '"purpose": "slow"}, {"tool": "remediation.restart_service_simulation", "arguments": {}, "purpose": "nope"}]}',
        ]
    )
    reasoner = LLMReasoner(llm)
    scope = await reasoner.intake(Q)
    assert scope.services == ["orders-service"], "unknown services from the model are dropped"
    _, calls = await reasoner.plan(scope, [_h()], 2, set())
    assert [(c.server, c.tool) for c in calls] == [("database", "get_slow_queries")], (
        "allow-list enforced"
    )


async def test_llm_reasoner_falls_back_to_rules_on_errors() -> None:
    reasoner = LLMReasoner(ScriptedProvider(["not json", "also not json"]))
    scope = await reasoner.intake(Q)
    assert scope == parse_scope(Q)
    _, calls = await reasoner.plan(scope, [_h(trigger_ref="d-408")], 2, set())
    assert calls, "rule-based plan used when the model output is invalid"
