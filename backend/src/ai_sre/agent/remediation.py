"""Remediation proposals (catalog), verification and the final report text."""

from __future__ import annotations

from typing import Any

from ai_sre.agent.models import (
    Category,
    Evidence,
    Hypothesis,
    Proposal,
    Report,
    RootCause,
    Verification,
)

APP_SERVICES = ("api-gateway", "orders-service", "fulfilment-worker")
VERIFY_SIGNALS = ("error_rate", "latency_p99", "db_pool_utilization", "queue_depth", "memory_bytes")
HEALTHY = {
    "error_rate": 0.01,
    "latency_p99": 1.0,
    "db_pool_utilization": 0.9,
    "queue_depth": 20,
    "memory_bytes": 250e6,
}


def _p(
    order: int,
    action: str,
    args: dict[str, Any],
    description: str,
    risk: str,
    impact: str,
    reversible: bool,
    enabled: bool,
) -> Proposal:
    executable = action != "manual" and enabled
    return Proposal(
        id=f"p{order}",
        order=order,
        action=action,
        arguments=args,
        description=description,
        risk=risk,  # type: ignore[arg-type]
        expected_impact=impact,
        reversible=reversible,
        executable=executable,
        status="proposed" if executable else "not_executable",
    )


def propose(root: RootCause | None, enabled: bool) -> list[Proposal]:
    """Ordered remediation steps for the root cause. Only catalogued MCP actions are executable."""
    if root is None:
        return [
            _p(
                1,
                "manual",
                {},
                "Gather more evidence: the root cause is not established.",
                "low",
                "No change to the system.",
                True,
                enabled,
            )
        ]
    svc, cat, ref = root.component, root.category, root.trigger_ref
    reason = f"{root.title} (confidence {root.band})"[:300]
    out: list[Proposal] = []
    deploy_like: tuple[Category, ...] = ("bad_deploy", "failed_deploy", "config_change")
    if cat in deploy_like and svc in APP_SERVICES:
        out.append(
            _p(
                1,
                "rollback_deployment_simulation",
                {"service": svc, "reason": reason},
                f"Roll back {svc}, undoing {ref}.",
                "medium",
                f"{svc} returns to its previous version/config; symptoms should clear within ~1 minute.",
                True,
                enabled,
            )
        )
        if svc == "fulfilment-worker":
            out.append(
                _p(
                    2,
                    "manual",
                    {},
                    "After the rollback, re-drive the dead-letter queue.",
                    "low",
                    "Jobs that exhausted their retries get fulfilled.",
                    True,
                    enabled,
                )
            )
        out.append(
            _p(
                len(out) + 1,
                "manual",
                {},
                f"Fix forward: review and test the change in {ref} before re-deploying.",
                "low",
                "Prevents recurrence.",
                True,
                enabled,
            )
        )
    elif cat == "resource_exhaustion" and svc in APP_SERVICES:
        out.append(
            _p(
                1,
                "restart_service_simulation",
                {"service": svc, "reason": reason},
                f"Restart {svc} to release memory (temporary mitigation).",
                "medium",
                "Memory drops; it will grow again until the leak is fixed. In-flight requests fail.",
                True,
                enabled,
            )
        )
        out.append(
            _p(
                2,
                "manual",
                {},
                f"Find and fix the leak in {svc}.",
                "low",
                "Permanent fix.",
                True,
                enabled,
            )
        )
    elif cat == "worker_degradation":
        out.append(
            _p(
                1,
                "scale_service_simulation",
                {"service": "fulfilment-worker", "replicas": 2, "reason": reason},
                "Scale fulfilment-worker to 2 replicas (mitigation).",
                "low",
                "Doubles processing capacity if jobs succeed; no effect if every job fails.",
                True,
                enabled,
            )
        )
    elif cat == "cache_failure":
        out.append(
            _p(
                1,
                "manual",
                {},
                "Restore connectivity to Redis (shop-cache); escalate to the platform team.",
                "medium",
                "Carts, catalog cache and order queueing recover.",
                True,
                enabled,
            )
        )
        out.append(
            _p(
                2,
                "manual",
                {},
                "Reconcile orders that were paid but not queued during the outage.",
                "low",
                "Customers charged during the outage get their orders.",
                True,
                enabled,
            )
        )
    elif cat == "dependency_failure":
        out.append(
            _p(
                1,
                "manual",
                {},
                "Contact the payments provider; consider failover or pausing checkout.",
                "medium",
                "Order placement recovers when the provider does. Rolling back services will not help.",
                True,
                enabled,
            )
        )
    elif cat == "infrastructure":
        out.append(
            _p(
                1,
                "manual",
                {},
                "Escalate the network latency to the infrastructure team.",
                "low",
                "Query latency returns to normal once the path is fixed; service rollbacks will not help.",
                True,
                enabled,
            )
        )
    else:
        out.append(
            _p(
                1,
                "manual",
                {},
                f"Investigate {svc} further ({cat}).",
                "low",
                "None yet.",
                True,
                enabled,
            )
        )
    return out


def snapshot(evidence: Evidence | None) -> dict[str, float | None]:
    if evidence is None or not evidence.ok or evidence.data is None:
        return {}
    signals = {s["signal"]: s["value"] for s in evidence.data.get("signals", [])}
    return {k: signals.get(k) for k in VERIFY_SIGNALS if k in signals}


def verify(
    service: str | None, before: dict[str, float | None], after: dict[str, float | None]
) -> Verification:
    if not after:
        return Verification(
            verdict="not_verified",
            service=service,
            before=before,
            after=after,
            details="metrics could not be read after the action",
        )
    degraded = [k for k, v in before.items() if v is not None and v > HEALTHY[k]]
    if not degraded:
        return Verification(
            verdict="no_change",
            service=service,
            before=before,
            after=after,
            details="the measured signals were not degraded before the action",
        )
    healthy_now = [k for k in degraded if (after.get(k) is not None and after[k] <= HEALTHY[k])]  # type: ignore[operator]
    better = [k for k in degraded if after.get(k) is not None and after[k] < 0.7 * before[k]]  # type: ignore[operator]
    worse = [k for k in degraded if after.get(k) is not None and after[k] > 1.3 * before[k]]  # type: ignore[operator]
    if len(healthy_now) == len(degraded):
        verdict, text = "resolved", f"all degraded signals back to healthy: {', '.join(degraded)}"
    elif better:
        verdict, text = (
            "improved",
            f"improved: {', '.join(better)}; still degraded: {', '.join(set(degraded) - set(healthy_now))}",
        )
    elif worse:
        verdict, text = "worse", f"worse: {', '.join(worse)}"
    else:
        verdict, text = "no_change", f"still degraded: {', '.join(degraded)}"
    return Verification(verdict=verdict, service=service, before=before, after=after, details=text)  # type: ignore[arg-type]


def render_markdown(r: Report, hypotheses: list[Hypothesis]) -> str:
    lines = [
        f"# Incident report: {r.investigation_id}",
        "",
        f"> {r.question}",
        "",
        "*Simulated environment. Confidence is computed by a deterministic rubric, not by the model.*",
        "",
        "## Summary",
        "",
        r.summary,
        "",
        f"- **Severity:** {r.severity}",
        f"- **Affected services:** {', '.join(r.affected_services) or 'unknown'}",
        f"- **Stop reason:** {r.stop_reason} after {r.rounds} round(s) and {r.tool_calls} tool call(s)",
        "",
    ]
    if r.root_cause:
        rc = r.root_cause
        lines += [
            "## Root cause",
            "",
            f"**{rc.title}**",
            "",
            f"- Category: `{rc.category}` · component: `{rc.component}` · trigger: `{rc.trigger_ref or '-'}`",
            f"- Confidence: **{rc.band}** ({rc.confidence:.2f})"
            + (f", capped: {rc.capped}" if rc.capped else ""),
            f"- Evidence: {', '.join(rc.evidence)}",
            "",
            rc.explanation,
            "",
        ]
        top = next((h for h in hypotheses if h.id == rc.hypothesis_id), None)
        if top:
            b = top.breakdown
            lines += [
                "Confidence breakdown: "
                + ", ".join(f"{k} {v:+.2f}" for k, v in b.model_dump().items() if v),
                "",
            ]
    else:
        lines += ["## Root cause", "", "Not established with the evidence gathered.", ""]
    if r.alternatives:
        lines += [
            "## Alternative hypotheses",
            "",
            "| Hypothesis | Confidence | Why it ranks lower |",
            "|---|---|---|",
        ]
        for a in r.alternatives:
            lines.append(f"| {a.title} | {a.band} ({a.confidence:.2f}) | {a.explanation} |")
        lines.append("")
    if r.timeline:
        lines += (
            ["## Timeline (UTC)", ""]
            + [f"- {t.at:%H:%M:%S} {t.event} [{t.source}]" for t in r.timeline]
            + [""]
        )
    lines += ["## Evidence", "", "| Id | Tool | Result |", "|---|---|---|"]
    lines += [f"| {e['id']} | {e['tool']} | {str(e['summary'])[:160]} |" for e in r.evidence] + [""]
    lines += ["## Recommended remediation", ""]
    for p in r.recommendations:
        flag = "reversible" if p.reversible else "IRREVERSIBLE"
        lines.append(
            f"{p.order}. {p.description} (risk {p.risk}, {flag}; action `{p.action}`, status `{p.status}`) "
            f"Expected: {p.expected_impact}"
        )
    lines.append("")
    lines += ["## Actions taken", ""]
    lines += [
        f"- `{p.action}` {p.arguments.get('service', '')}: **{p.status}**"
        + (f" ({p.error})" if p.error else "")
        for p in r.actions_taken
    ] or ["- None."]
    lines.append("")
    if r.verification:
        v = r.verification
        lines += [
            "## Verification",
            "",
            f"**{v.verdict}**: {v.details}",
            "",
            "| Signal | Before | After |",
            "|---|---|---|",
        ]
        lines += [f"| {k} | {v.before.get(k)} | {v.after.get(k)} |" for k in v.before] + [""]
    if r.limitations:
        lines += ["## Limitations", ""] + [f"- {x}" for x in r.limitations] + [""]
    return "\n".join(lines)
