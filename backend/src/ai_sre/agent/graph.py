"""The investigation workflow as an explicit LangGraph state machine.

Nodes: intake → planning → tool_selection → tool_execution → evidence_analysis →
hypothesis_evaluation → replanning ─(more evidence needed)→ planning
                                   └(stop)→ root_cause → remediation_proposal → approval
(interrupt for humans) → remediation_execution → verification → report.

Every node runs inside :func:`guarded`: cancellation check, per-node timeout, retry of transient
failures, and error recovery (the error is recorded and the run proceeds to a partial report
instead of crashing). Limits (rounds, tool calls, wall-clock) are enforced by code.
"""

from __future__ import annotations

import asyncio
import functools
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from ai_sre.agent.findings import extract_findings
from ai_sre.agent.gateway import ToolGateway
from ai_sre.agent.hypotheses import SCORING_VERSION, generate, sufficient
from ai_sre.agent.models import (
    Decision,
    Evidence,
    InvestigationState,
    NodeError,
    Plan,
    PlannedCall,
    Report,
    RootCause,
    TimelineEvent,
)
from ai_sre.agent.reasoner import APP_SERVICES, Reasoner
from ai_sre.agent.remediation import (
    knowledge_references,
    propose,
    render_markdown,
    snapshot,
    verify,
)

logger = structlog.get_logger("agent.graph")

NODE_TIMEOUT_S = {"tool_execution": 150.0, "verification": 700.0, "remediation_execution": 120.0}
TRANSIENT = (ConnectionError, TimeoutError, OSError)
INVESTIGATIVE = {
    "planning",
    "tool_selection",
    "tool_execution",
    "evidence_analysis",
    "hypothesis_evaluation",
}


@dataclass
class Deps:
    gateway: ToolGateway
    reasoner: Reasoner


Node = Callable[[InvestigationState], Awaitable[dict[str, Any]]]


def guarded(name: str) -> Callable[[Node], Node]:
    """Cancellation, timeout, retry (transient errors, 2 attempts) and error recovery."""

    def wrap(fn: Node) -> Node:
        @functools.wraps(fn)
        async def run(state: InvestigationState) -> dict[str, Any]:
            if name in INVESTIGATIVE and (
                state.get("cancel_requested") or state.get("stop_reason") in ("cancelled", "error")
            ):
                return {}  # skip: route to the report with what we have
            logger.info("node_enter", node=name, investigation=state.get("investigation_id"))
            for attempt in (1, 2):
                try:
                    async with asyncio.timeout(NODE_TIMEOUT_S.get(name, 60.0)):
                        return await fn(state)
                except TRANSIENT as exc:
                    if attempt == 2:
                        return _failed(name, exc)
                    await asyncio.sleep(1.0)
                except Exception as exc:
                    if (
                        isinstance(exc, asyncio.CancelledError)
                        or type(exc).__name__ == "GraphInterrupt"
                    ):
                        raise
                    return _failed(name, exc)
            return {}

        return run

    return wrap


def _failed(node: str, exc: BaseException) -> dict[str, Any]:
    logger.exception("node_failed", node=node)
    return {
        "errors": [
            NodeError(node=node, error=f"{type(exc).__name__}: {exc}", at=datetime.now(UTC))
        ],
        "stop_reason": "error",
        "limitations": [
            f"{node} failed ({type(exc).__name__}); the report is based on partial evidence"
        ],
    }


def build_graph(deps: Deps) -> StateGraph[InvestigationState]:
    gw, reasoner = deps.gateway, deps.reasoner

    # ------------------------------------------------------------------ 1. intake
    @guarded("intake")
    async def intake(state: InvestigationState) -> dict[str, Any]:
        scope = await reasoner.intake(state["question"])
        update: dict[str, Any] = {
            "scope": scope,
            "round": 0,
            "stop_reason": None,
            "hypotheses": [],
            "findings": [],
            "timeline": [],
            "proposals": [],
            "decisions": [],
            "limitations": [f"assumption: {a}" for a in scope.assumptions]
            + ["data comes from the SIMULATED environment"],
        }
        registry = await gw.registry()
        if registry is None:
            update["stop_reason"] = "tools_unavailable"
            update["limitations"].append(
                "the MCP tool servers were unreachable; nothing could be investigated"
            )
            return update
        servers = {s["name"]: s for s in registry.get("servers", [])}
        update["remediation_enabled"] = bool(servers.get("remediation", {}).get("enabled"))
        missing = {"logs", "metrics", "deployments"} - servers.keys()
        if missing:
            update["limitations"].append(f"tool servers missing: {', '.join(sorted(missing))}")
        return update

    # ------------------------------------------------------------------ 2. planning
    @guarded("planning")
    async def planning(state: InvestigationState) -> dict[str, Any]:
        round_ = state.get("round", 0) + 1
        done = set(state.get("executed_keys", []))
        rationale, calls = await reasoner.plan(
            state["scope"], state.get("hypotheses", []), round_, done
        )
        plan = Plan(
            round=round_,
            rationale=rationale,
            hypotheses_considered=[h.id for h in state.get("hypotheses", [])[:3]],
            calls=calls,
        )
        return {"round": round_, "plan": plan}

    # ------------------------------------------------------------------ 3. tool selection
    @guarded("tool_selection")
    async def tool_selection(state: InvestigationState) -> dict[str, Any]:
        limits = state["limits"]
        used = len(state.get("evidence", []))
        remaining = limits.max_tool_calls - used
        done = set(state.get("executed_keys", []))
        unique: dict[str, PlannedCall] = {}
        for call in sorted(state["plan"].calls, key=lambda c: c.priority):
            if call.key() not in done and call.key() not in unique:
                unique[call.key()] = call
        selected = list(unique.values())[: max(0, remaining)]
        update: dict[str, Any] = {"pending_calls": selected}
        if remaining <= 0:
            update["stop_reason"] = "tool_budget"
        elif len(unique) > len(selected):
            update["limitations"] = [
                f"round {state['round']}: {len(unique) - len(selected)} planned call(s) skipped (tool budget)"
            ]
        return update

    # ------------------------------------------------------------------ 4. tool execution
    @guarded("tool_execution")
    async def tool_execution(state: InvestigationState) -> dict[str, Any]:
        calls = state.get("pending_calls", [])
        gw.resume_numbering(len(state.get("evidence", [])))
        sem = asyncio.Semaphore(4)

        async def one(call: PlannedCall) -> Evidence:
            async with sem:
                return await gw.call(call.server, call.tool, call.arguments, state["round"])

        results = await asyncio.gather(*(one(c) for c in calls))
        results = sorted(results, key=lambda e: e.id)
        failed = [e for e in results if not e.ok]
        update: dict[str, Any] = {
            "evidence": results,
            "executed_keys": [c.key() for c in calls],
            "pending_calls": [],
        }
        if failed:
            update["limitations"] = [f"{e.server}.{e.tool} failed: {e.error_code}" for e in failed]
        return update

    # ------------------------------------------------------------------ 5. evidence analysis
    @guarded("evidence_analysis")
    async def evidence_analysis(state: InvestigationState) -> dict[str, Any]:
        findings = extract_findings(state.get("evidence", []))
        timeline = sorted(
            (
                TimelineEvent(at=f.at, event=f.detail, source=f.id)
                for f in findings
                if f.at and f.kind in ("deployment", "metric_anomaly", "error_signature")
            ),
            key=lambda t: t.at,
        )
        return {"findings": findings, "timeline": timeline[:40]}

    # ------------------------------------------------------------------ 6. hypothesis evaluation
    @guarded("hypothesis_evaluation")
    async def hypothesis_evaluation(state: InvestigationState) -> dict[str, Any]:
        hyps = generate(
            state["scope"], state.get("findings", []), state.get("evidence", []), datetime.now(UTC)
        )
        return {"hypotheses": hyps}

    # ------------------------------------------------------------------ 7. replanning (decision)
    @guarded("replanning")
    async def replanning(state: InvestigationState) -> dict[str, Any]:
        limits = state["limits"]
        if state.get("stop_reason"):
            return {}
        if state.get("cancel_requested"):
            return {"stop_reason": "cancelled"}
        evidence = state.get("evidence", [])
        if evidence and not any(e.ok for e in evidence):
            return {"stop_reason": "tools_unavailable"}
        if sufficient(state.get("hypotheses", []), limits.sufficient_confidence):
            return {"stop_reason": "sufficient_evidence"}
        if not any(e.round == state["round"] for e in evidence):
            return {"stop_reason": "plan_exhausted"}
        if state["round"] >= limits.max_rounds:
            return {"stop_reason": "max_rounds"}
        if len(evidence) >= limits.max_tool_calls:
            return {"stop_reason": "tool_budget"}
        if (datetime.now(UTC) - state["started_at"]).total_seconds() > limits.time_budget_s:
            return {"stop_reason": "time_budget"}
        return {}

    def after_replanning(state: InvestigationState) -> str:
        return "root_cause" if state.get("stop_reason") else "planning"

    # ------------------------------------------------------------------ 8. root cause
    @guarded("root_cause")
    async def root_cause(state: InvestigationState) -> dict[str, Any]:
        hyps = state.get("hypotheses", [])
        findings = {f.id: f for f in state.get("findings", [])}

        def to_root(h: Any, explanation: str) -> RootCause:
            cited = list(
                dict.fromkeys(findings[i].evidence_id for i in h.supporting if i in findings)
            )
            return RootCause(
                hypothesis_id=h.id,
                category=h.category,
                component=h.component,
                title=h.title,
                trigger_ref=h.trigger_ref,
                explanation=explanation,
                confidence=h.confidence,
                band=h.band,
                evidence=cited,
            )

        if not hyps or hyps[0].band == "insufficient":
            return {
                "root_cause": None,
                "alternatives": [to_root(h, h.rationale) for h in hyps[:3]],
                "limitations": ["no hypothesis reached a usable confidence"],
            }
        top = hyps[0]
        support = "; ".join(findings[i].detail for i in top.supporting[:6] if i in findings)
        against = "; ".join(findings[i].detail for i in top.contradicting[:3] if i in findings)
        explanation = (
            f"{top.rationale} Supporting evidence ({len(top.supporting)} findings from {', '.join(top.sources)}): "
            f"{support}."
            + (f" Contradicting: {against}." if against else " No contradicting evidence found.")
            + f" Temporal relation to onset: {top.temporal}; mechanism: {top.mechanism}."
            + (f" Similar past incident: {top.historical_match}." if top.historical_match else "")
        )
        rc = to_root(top, explanation)
        if len(hyps) > 1 and top.confidence - hyps[1].confidence < 0.10 and rc.band == "high":
            rc.band, rc.capped = "medium", f"competing explanation within 0.10 ({hyps[1].id})"
        alternatives = []
        for h in hyps[1:4]:
            why = []
            if h.contradicting:
                why.append(f"{len(h.contradicting)} contradicting finding(s)")
            if h.temporal in ("violation", "n/a"):
                why.append(f"temporal: {h.temporal}")
            if h.mechanism != "strong":
                why.append(f"mechanism {h.mechanism}")
            alternatives.append(to_root(h, ", ".join(why) or "less corroborated"))
        update: dict[str, Any] = {"root_cause": rc, "alternatives": alternatives}
        if rc.capped:
            update["limitations"] = [f"confidence capped: {rc.capped}"]
        # Baseline for verification: current golden signals of the component.
        if top.component in APP_SERVICES:
            ev = await gw.call(
                "metrics", "get_service_metrics", {"service": top.component}, state["round"]
            )
            update["evidence"] = [ev]
            update["baseline_metrics"] = snapshot(ev)
        # Knowledge: remediation procedures for this cause, and a comparison with the most similar
        # past incident. Citations come only from these results.
        gw.resume_numbering(len(state.get("evidence", [])) + len(update.get("evidence", [])))
        kb_args: dict[str, Any] = {
            "query": f"{top.title}. {top.category.replace('_', ' ')} {top.component} remediation"[
                :480
            ],
            "doc_types": ["runbook", "procedure"],
            "top_k": 3,
        }
        if top.component in APP_SERVICES:
            kb_args["services"] = [top.component]
        kb = [await gw.call("knowledge", "search_knowledge", kb_args, state["round"])]
        if top.historical_match:
            observations = [findings[i].detail[:280] for i in top.supporting[:8] if i in findings]
            kb.append(
                await gw.call(
                    "knowledge",
                    "compare_with_incident",
                    {
                        "incident_id": top.historical_match,
                        "observations": observations or [top.title],
                        "services": [top.component],
                    },
                    state["round"],
                )
            )
        update["evidence"] = [*update.get("evidence", []), *kb]
        update["knowledge_refs"] = knowledge_references([*state.get("evidence", []), *kb])
        return update

    # ------------------------------------------------------------------ 9. remediation proposal
    @guarded("remediation_proposal")
    async def remediation_proposal(state: InvestigationState) -> dict[str, Any]:
        enabled = bool(state.get("remediation_enabled"))
        proposals = propose(state.get("root_cause"), enabled)
        procedures = [
            f"{ref['document_id']} > {ref['section']} ({ref['source']})"
            for ref in state.get("knowledge_refs", [])
            if ref["kind"] in ("runbook", "procedure") and not ref.get("superseded_by")
        ]
        if proposals and procedures:
            proposals[0].references = procedures[:3]
        if state.get("stop_reason") in ("cancelled", "error"):
            return {"proposals": proposals}
        limitations = []
        if not enabled and any(p.action != "manual" for p in proposals):
            limitations.append("remediation is disabled in configuration: no action was requested")
        for p in proposals:
            if p.executable and p.order == 1:  # request approval for the first executable step only
                ev = await gw.call_remediation(p.action, p.arguments, state.get("round", 0))
                if ev.ok and ev.data and ev.data.get("status") == "approval_required":
                    p.approval_request_id = ev.data["approval_request_id"]
                    p.status = "awaiting_approval"
                else:
                    p.status, p.error = "failed", ev.error_message or "approval request failed"
            elif p.executable:
                p.status = "proposed"
        return {"proposals": proposals, "limitations": limitations}

    def after_proposal(state: InvestigationState) -> str:
        return (
            "approval"
            if any(p.status == "awaiting_approval" for p in state.get("proposals", []))
            else "report"
        )

    # ------------------------------------------------------------------ 10. approval (human in the loop)
    async def approval(state: InvestigationState) -> dict[str, Any]:
        waiting = [p for p in state["proposals"] if p.status == "awaiting_approval"]
        answer = interrupt(
            {
                "type": "approval_required",
                "investigation_id": state["investigation_id"],
                "root_cause": rc.model_dump(mode="json")
                if (rc := state.get("root_cause"))
                else None,
                "proposals": [p.model_dump(mode="json") for p in waiting],
                "how": "approve with the MCP admin endpoint, then resume with decisions "
                "[{proposal_id, approved, approver, approval_token}]",
            }
        )
        decisions = [Decision.model_validate(d) for d in (answer or [])]
        by_id = {d.proposal_id: d for d in decisions}
        proposals, notes = [], []
        for p in state["proposals"]:
            d = by_id.get(p.id)
            if p.status == "awaiting_approval":
                p = p.model_copy()
                if d is None:
                    p.status = "rejected"
                    notes.append(f"{p.id}: no decision recorded; treated as rejected")
                elif not d.approved:
                    p.status = "rejected"
                elif not d.approval_token or d.approver.strip().lower() in {
                    "",
                    "agent",
                    "ai-sre",
                    "mcp",
                }:
                    p.status, p.error = "rejected", "approval lacks a human approver or token"
                    notes.append(f"{p.id}: approval refused: {p.error}")
                else:
                    p.status = "approved"
            proposals.append(p)
        return {"decisions": decisions, "proposals": proposals, "limitations": notes}

    def after_approval(state: InvestigationState) -> str:
        return (
            "remediation_execution"
            if any(p.status == "approved" for p in state["proposals"])
            else "report"
        )

    # ------------------------------------------------------------------ 11. remediation execution
    @guarded("remediation_execution")
    async def remediation_execution(state: InvestigationState) -> dict[str, Any]:
        tokens = {d.proposal_id: d.approval_token for d in state.get("decisions", []) if d.approved}
        gw.resume_numbering(len(state.get("evidence", [])))
        proposals, evidence = [], []
        for p in state["proposals"]:
            if p.status == "approved":
                p = p.model_copy()
                ev = await gw.call_remediation(
                    p.action, {**p.arguments, "approval_token": tokens[p.id]}, state["round"]
                )
                evidence.append(ev)
                if ev.ok and ev.data and ev.data.get("status") == "executed":
                    p.status, p.result = "executed", ev.data.get("result")
                else:  # never claim success when the action failed
                    p.status, p.error = "failed", ev.error_message or "unexpected response"
            proposals.append(p)
        return {"proposals": proposals, "evidence": evidence}

    def after_execution(state: InvestigationState) -> str:
        return (
            "verification" if any(p.status == "executed" for p in state["proposals"]) else "report"
        )

    # ------------------------------------------------------------------ 12. verification
    @guarded("verification")
    async def verification(state: InvestigationState) -> dict[str, Any]:
        rc = state.get("root_cause")
        service = rc.component if rc and rc.component in APP_SERVICES else None
        if service is None:
            return {}
        await asyncio.sleep(state["limits"].verification_wait_s)
        gw.resume_numbering(len(state.get("evidence", [])))
        ev = await gw.call("metrics", "get_service_metrics", {"service": service}, state["round"])
        result = verify(service, state.get("baseline_metrics", {}), snapshot(ev))
        return {"evidence": [ev], "verification": result}

    # ------------------------------------------------------------------ 13. report
    @guarded("report")
    async def report(state: InvestigationState) -> dict[str, Any]:
        scope = state.get("scope")
        rc = state.get("root_cause")
        evidence = state.get("evidence", [])
        cited = set(rc.evidence if rc else [])
        for alt in state.get("alternatives", []):
            cited |= set(alt.evidence)
        ev_rows = [
            {"id": e.id, "tool": f"{e.server}.{e.tool}", "summary": e.summary}
            for e in evidence
            if e.id in cited or not e.ok
        ]
        actions = [
            p
            for p in state.get("proposals", [])
            if p.status in ("executed", "failed", "rejected", "approved")
        ]
        affected = sorted(
            {
                f.service
                for f in state.get("findings", [])
                if f.service
                and f.kind
                in (
                    "metric_anomaly",
                    "error_signature",
                    "pool_saturation",
                    "queue_backlog",
                    "memory_growth",
                )
            }
        )
        severity = scope.severity if scope else "SEV4"
        if (
            rc
            and rc.band in ("high", "medium")
            and severity in ("SEV3", "SEV4")
            and len(affected) >= 2
        ):
            severity = "SEV2"
        if rc:
            summary = (
                f"Most probable cause: {rc.title} ({rc.band} confidence, {rc.confidence:.2f})."
            )
        else:
            summary = "The root cause could not be established with the evidence gathered."
        if (checked := state.get("verification")) is not None:
            summary += f" Remediation verification: {checked.verdict}."
        limitations = list(dict.fromkeys(state.get("limitations", [])))
        limitations.append(f"confidence rubric {SCORING_VERSION}; reasoner: {reasoner.name}")
        r = Report(
            investigation_id=state["investigation_id"],
            question=state["question"],
            summary=summary,
            severity=severity,
            affected_services=affected or (scope.services if scope else []),
            root_cause=rc,
            alternatives=state.get("alternatives", []),
            timeline=state.get("timeline", []),
            evidence=ev_rows,
            recommendations=state.get("proposals", []),
            actions_taken=actions,
            verification=state.get("verification"),
            limitations=limitations,
            references=state.get("knowledge_refs") or knowledge_references(evidence),
            stop_reason=state.get("stop_reason"),
            rounds=state.get("round", 0),
            tool_calls=len(evidence),
            markdown="",
        )
        r.markdown = render_markdown(r, state.get("hypotheses", []))
        return {"report": r}

    # ------------------------------------------------------------------ wiring
    g = StateGraph(InvestigationState)
    for name, fn in [
        ("intake", intake),
        ("planning", planning),
        ("tool_selection", tool_selection),
        ("tool_execution", tool_execution),
        ("evidence_analysis", evidence_analysis),
        ("hypothesis_evaluation", hypothesis_evaluation),
        ("replanning", replanning),
        ("root_cause", root_cause),
        ("remediation_proposal", remediation_proposal),
        ("approval", approval),
        ("remediation_execution", remediation_execution),
        ("verification", verification),
        ("report", report),
    ]:
        g.add_node(name, fn)  # type: ignore[call-overload]
    g.add_edge(START, "intake")
    g.add_conditional_edges(
        "intake",
        lambda s: "report" if s.get("stop_reason") else "planning",
        {"planning": "planning", "report": "report"},
    )
    g.add_edge("planning", "tool_selection")
    g.add_conditional_edges(
        "tool_selection",
        lambda s: "root_cause" if s.get("stop_reason") == "tool_budget" else "tool_execution",
        {"tool_execution": "tool_execution", "root_cause": "root_cause"},
    )
    g.add_edge("tool_execution", "evidence_analysis")
    g.add_edge("evidence_analysis", "hypothesis_evaluation")
    g.add_edge("hypothesis_evaluation", "replanning")
    g.add_conditional_edges(
        "replanning", after_replanning, {"planning": "planning", "root_cause": "root_cause"}
    )
    g.add_edge("root_cause", "remediation_proposal")
    g.add_conditional_edges(
        "remediation_proposal", after_proposal, {"approval": "approval", "report": "report"}
    )
    g.add_conditional_edges(
        "approval",
        after_approval,
        {"remediation_execution": "remediation_execution", "report": "report"},
    )
    g.add_conditional_edges(
        "remediation_execution",
        after_execution,
        {"verification": "verification", "report": "report"},
    )
    g.add_edge("verification", "report")
    g.add_edge("report", END)
    return g
