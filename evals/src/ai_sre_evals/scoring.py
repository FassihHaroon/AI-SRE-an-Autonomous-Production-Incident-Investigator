"""Programmatic scoring of one investigation against a scenario's ground truth.

Everything here is deterministic. The optional model-based judge (``judge.py``) adds a second,
subjective opinion on explanation quality and is reported separately.
"""

from __future__ import annotations

import json
import statistics
from typing import Any

from ai_sre.agent.models import Evidence, Report, RootCause
from ai_sre_evals.dataset import Cause, Scenario

KNOWN_ENTITIES = {
    "api-gateway",
    "orders-service",
    "fulfilment-worker",
    "postgres",
    "redis",
    "payments-provider",
    "fulfilment-queue",
}
COMPONENT_ALIASES = {"shop-db": "postgres", "shop-cache": "redis", "orders-db": "postgres"}


def _component(value: str | None) -> str | None:
    return COMPONENT_ALIASES.get(value or "", value)


def matches(rc: RootCause | None, cause: Cause) -> bool:
    if rc is None:
        return False
    if rc.category != cause.category or _component(rc.component) != _component(cause.component):
        return False
    return cause.trigger_ref is None or rc.trigger_ref == cause.trigger_ref


def root_cause_score(report: Report | None, scenario: Scenario) -> tuple[float, str]:
    exp = scenario.expected
    rc = report.root_cause if report else None
    if exp.root_cause is None:
        if rc is None or rc.band in ("low", "insufficient"):
            return 1.0, "correctly abstained"
        return 0.0, f"claimed {rc.category}:{rc.component} without a real cause"
    if any(matches(rc, c) for c in [exp.root_cause, *exp.also_correct]):
        return 1.0, "correct"
    if any(matches(rc, c) for c in exp.acceptable_alternatives):
        return 0.5, "acceptable alternative (symptom rather than trigger)"
    if rc is None:
        return 0.0, "no root cause reported"
    return (
        0.0,
        f"wrong: {rc.category}:{rc.component}{':' + rc.trigger_ref if rc.trigger_ref else ''}",
    )


def in_top3(report: Report | None, scenario: Scenario) -> bool | None:
    exp = scenario.expected
    if exp.root_cause is None or report is None:
        return None
    candidates = [report.root_cause, *report.alternatives[:2]]
    return any(
        matches(c, cause) for c in candidates for cause in [exp.root_cause, *exp.also_correct]
    )


def _prf(found: set[str], expected: set[str]) -> tuple[float | None, float | None]:
    hit = len(found & expected)
    precision = hit / len(found) if found else None
    recall = hit / len(expected) if expected else None
    return precision, recall


def remediation_correct(report: Report | None, scenario: Scenario) -> tuple[bool, str]:
    exp = scenario.expected.remediation
    proposals = sorted(report.recommendations, key=lambda p: p.order) if report else []
    if not proposals:
        return False, "no remediation proposed"
    first = proposals[0]
    if first.action in scenario.expected.unsafe_actions:
        return False, f"unsafe first action {first.action}"
    if first.action != exp.action:
        return False, f"first action {first.action}, expected {exp.action}"
    if exp.target and first.arguments.get("service") != exp.target:
        return False, f"targets {first.arguments.get('service')}, expected {exp.target}"
    text = first.description.lower()
    missing = [k for k in exp.keywords if k.lower() not in text]
    if missing:
        return False, f"description lacks {missing}"
    return True, f"{first.action} {first.arguments.get('service') or ''}".strip()


def grounding(report: Report | None, evidence: list[Evidence]) -> dict[str, Any]:
    """Claims in the report that are not supported by tool results (hallucinations)."""
    if report is None:
        return {"claims": 0, "ungrounded": [], "rate": 0.0}
    ok = {e.id: e for e in evidence if e.ok}
    blob = json.dumps([e.data for e in ok.values()], default=str)
    retrieved_docs = {
        c.get("citation", {}).get("document_id")
        for e in ok.values()
        if e.server == "knowledge"
        for c in (e.data or {}).get("hits", [])
    } | {
        (e.data or {}).get("incident", {}).get("document_id")
        for e in ok.values()
        if e.tool == "compare_with_incident"
    }
    claims: list[tuple[str, bool]] = []
    rc = report.root_cause
    if rc:
        comp = _component(rc.component)
        claims.append(
            (
                f"root cause component {rc.component}",
                comp in KNOWN_ENTITIES and str(rc.component) in blob,
            )
        )
        if rc.trigger_ref:
            claims.append((f"trigger {rc.trigger_ref}", rc.trigger_ref in blob))
        for ev_id in rc.evidence:
            claims.append((f"cited evidence {ev_id}", ev_id in ok))
    for alt in report.alternatives:
        claims.append((f"alternative component {alt.component}", str(alt.component) in blob))
    for ref in report.references:
        doc = ref.get("document_id")
        claims.append((f"citation {doc}", doc in retrieved_docs))
    for svc in report.affected_services:
        claims.append((f"affected service {svc}", svc in blob))
    ungrounded = [c for c, good in claims if not good]
    return {
        "claims": len(claims),
        "ungrounded": ungrounded,
        "rate": len(ungrounded) / len(claims) if claims else 0.0,
    }


def score(
    scenario: Scenario,
    report: Report | None,
    evidence: list[Evidence],
    trace: dict[str, Any],
    *,
    completed: bool,
) -> dict[str, Any]:
    exp = scenario.expected
    tool_of = {e.id: f"{e.server}.{e.tool}" for e in evidence}
    called = [f"{e.server}.{e.tool}" for e in evidence]
    retrieved = {tool_of[e.id] for e in evidence if e.ok}
    cited = {
        tool_of[i]
        for i in (report.root_cause.evidence if report and report.root_cause else [])
        if i in tool_of
    }
    expected_evidence, expected_tools = set(exp.evidence), set(exp.tools)

    rc_score, rc_note = root_cause_score(report, scenario)
    cite_p, cite_r = _prf(cited, expected_evidence)
    _, retrieval_r = _prf(retrieved, expected_evidence)
    tool_acc = len(set(called) & expected_tools) / len(expected_tools) if expected_tools else None
    useful = expected_tools | expected_evidence
    efficiency = (
        len({c for c in called if c in useful}) / len(called) if called else 0.0
    )  # share of calls that were needed (duplicates and detours lower it)
    remed_ok, remed_note = remediation_correct(report, scenario)
    ground = grounding(report, evidence)
    severity = report.severity if report else None
    llm = trace.get("llm", {})
    return {
        "scenario": scenario.id,
        "tags": scenario.tags,
        "completed": completed,
        "root_cause_score": rc_score,
        "root_cause_note": rc_note,
        "root_cause_top3": in_top3(report, scenario),
        "predicted": {
            "category": report.root_cause.category if report and report.root_cause else None,
            "component": report.root_cause.component if report and report.root_cause else None,
            "trigger_ref": report.root_cause.trigger_ref if report and report.root_cause else None,
            "confidence": report.root_cause.confidence if report and report.root_cause else None,
            "band": report.root_cause.band if report and report.root_cause else None,
        },
        "evidence_precision": cite_p if exp.root_cause else None,
        "evidence_recall": cite_r if exp.root_cause else None,
        "retrieval_recall": retrieval_r,
        "tool_selection_accuracy": tool_acc,
        "tool_call_efficiency": round(efficiency, 3),
        "tool_calls": len(called),
        "severity": severity,
        "severity_expected": exp.severity,
        "severity_correct": severity == exp.severity,
        "remediation_correct": remed_ok,
        "remediation_note": remed_note,
        "hallucinated": bool(ground["ungrounded"]),
        "ungrounded_claims": ground["ungrounded"],
        "claims": ground["claims"],
        "latency_ms": trace.get("duration_ms", 0.0),
        "iterations": trace.get("iterations", 0),
        "tool_errors": trace.get("tools", {}).get("errors", 0),
        "retries": trace.get("retries", {}).get("tools", 0),
        "llm_calls": llm.get("calls", 0),
        # LLM calls that failed after retries: the reasoner fell back to rules for that step.
        "llm_errors": llm.get("errors", 0),
        "llm_retries": trace.get("retries", {}).get("llm", 0),
        "tokens": llm.get("total_tokens", 0),
        "cost_usd": llm.get("cost_usd", 0.0),
        "stop_reason": report.stop_reason if report else None,
    }


def _mean(values: list[Any]) -> float | None:
    vals = [float(v) for v in values if v is not None]
    return round(statistics.fmean(vals), 4) if vals else None


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Headline metrics over a set of scored investigations."""
    if not rows:
        return {"n": 0}
    lat = [r["latency_ms"] for r in rows if r["completed"]]
    return {
        "n": len(rows),
        "root_cause_accuracy": _mean([r["root_cause_score"] for r in rows]),
        "root_cause_exact": _mean([r["root_cause_score"] == 1.0 for r in rows]),
        "root_cause_top3": _mean([r["root_cause_top3"] for r in rows]),
        "evidence_precision": _mean([r["evidence_precision"] for r in rows]),
        "evidence_recall": _mean([r["evidence_recall"] for r in rows]),
        "retrieval_recall": _mean([r["retrieval_recall"] for r in rows]),
        "tool_selection_accuracy": _mean([r["tool_selection_accuracy"] for r in rows]),
        "tool_call_efficiency": _mean([r["tool_call_efficiency"] for r in rows]),
        "avg_tool_calls": _mean([r["tool_calls"] for r in rows]),
        "severity_accuracy": _mean([r["severity_correct"] for r in rows]),
        "remediation_accuracy": _mean([r["remediation_correct"] for r in rows]),
        "hallucination_rate": _mean([r["hallucinated"] for r in rows]),
        "ungrounded_claim_rate": round(
            sum(len(r["ungrounded_claims"]) for r in rows) / max(1, sum(r["claims"] for r in rows)),
            4,
        ),
        "completion_rate": _mean([r["completed"] for r in rows]),
        "avg_latency_ms": _mean(lat),
        "p95_latency_ms": round(sorted(lat)[int(0.95 * (len(lat) - 1))], 1) if lat else None,
        "avg_tokens": _mean([r["tokens"] for r in rows]),
        "total_tokens": sum(r["tokens"] for r in rows),
        "avg_cost_usd": _mean([r["cost_usd"] for r in rows]),
        "total_cost_usd": round(sum(r["cost_usd"] for r in rows), 6),
        "avg_llm_calls": _mean([r["llm_calls"] for r in rows]),
        "llm_fallback_rate": round(
            sum(r["llm_errors"] for r in rows) / max(1, sum(r["llm_calls"] for r in rows)), 4
        ),
    }
