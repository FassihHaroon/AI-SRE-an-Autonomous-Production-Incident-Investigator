"""Hypothesis generation, evaluation and confidence scoring (deterministic).

Candidate root causes are generated from findings (deployments, dependency health, database,
resources). Each is evaluated for supporting and contradicting findings, temporal relation to
the symptom onset, mechanism strength, topology and history, then scored with the transparent
rubric of agent-design.md § 6. The LLM never sets the confidence number.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta

from ai_sre.agent.models import (
    Band,
    Category,
    Evidence,
    Finding,
    Hypothesis,
    IncidentScope,
    ScoreBreakdown,
)

SCORING_VERSION = "v1"
# Static topology (service catalogue of the simulated environment).
DEPENDS_ON: dict[str, set[str]] = {
    "api-gateway": {"orders-service", "postgres", "redis"},
    "orders-service": {"postgres", "redis", "payments-provider", "fulfilment-queue"},
    "fulfilment-worker": {"postgres", "redis", "fulfilment-queue"},
}
DOMAIN = {
    "logs": "logs",
    "metrics": "metrics",
    "deployments": "deployments",
    "database": "database",
    "infrastructure": "infrastructure",
    "incidents": "history",
}
SYMPTOM_KINDS = {
    "metric_anomaly",
    "error_signature",
    "service_unhealthy",
    "pool_saturation",
    "queue_backlog",
    "worker_failures",
    "memory_growth",
    "container_pressure",
    "restarts",
}


def band_for(score: float) -> Band:
    if score >= 0.75:
        return "high"
    if score >= 0.50:
        return "medium"
    if score >= 0.30:
        return "low"
    return "insufficient"


def score(h: Hypothesis, failed_relevant_calls: int) -> Hypothesis:
    """Rubric v1 (agent-design.md § 6). Mutates and returns ``h``."""
    b = ScoreBreakdown()
    n = len(set(h.sources))
    b.corroboration = 0.30 if n >= 3 else 0.20 if n == 2 else 0.10 if n == 1 else 0.0
    b.temporal = {"strong": 0.20, "plausible": 0.10, "n/a": 0.0, "violation": -0.40}[h.temporal]
    b.topology = 0.05 if h.on_causal_path else -0.10
    b.mechanism = {"strong": 0.15, "moderate": 0.08, "weak": 0.0}[h.mechanism]
    b.historical = 0.05 if h.historical_match else 0.0
    b.contradictions = -min(0.15 * len(h.contradicting), 0.30)
    b.coverage_gaps = -min(0.05 * failed_relevant_calls, 0.15)
    total = b.prior + b.corroboration + b.temporal + b.topology + b.mechanism + b.historical
    total += b.contradictions + b.coverage_gaps
    h.breakdown = b
    h.confidence = round(max(0.0, min(1.0, total)), 3)
    h.band = band_for(h.confidence)
    return h


def symptom_onset(findings: list[Finding], scope: IncidentScope) -> datetime | None:
    in_scope = set(scope.services)
    times = [
        f.at
        for f in findings
        if f.kind == "metric_anomaly" and f.at and (not in_scope or f.service in in_scope)
    ]
    times = times or [f.at for f in findings if f.kind == "metric_anomaly" and f.at]
    if not times:
        times = [
            f.at
            for f in findings
            if f.kind == "error_signature" and f.at and f.values.get("count", 0) >= 5
        ]
    return min(times) if times else None


class _Index:
    def __init__(self, findings: list[Finding], evidence: list[Evidence]) -> None:
        self.findings = findings
        self.by_id = {f.id: f for f in findings}
        self.server_of = {e.id: e.server for e in evidence}

    def of(self, kind: str, service: str | None = None) -> list[Finding]:
        return [
            f for f in self.findings if f.kind == kind and (service is None or f.service == service)
        ]

    def domains(self, ids: Iterable[str]) -> list[str]:
        return sorted(
            {DOMAIN.get(self.server_of.get(self.by_id[i].evidence_id, ""), "other") for i in ids}
        )


def _ids(findings: Iterable[Finding]) -> list[str]:
    return list(dict.fromkeys(f.id for f in findings))


def _passthrough_errors(ix: _Index, service: str) -> list[Finding]:
    """Errors on `service` that only relay a failure of something it calls."""
    return [
        f
        for f in ix.of("error_signature", service)
        if f.values.get("error_type") == "UpstreamError"
        or "returned HTTP" in str(f.values.get("template"))
    ]


def _own_symptoms(ix: _Index, service: str) -> list[Finding]:
    relayed = {f.id for f in _passthrough_errors(ix, service)}
    return [
        f
        for f in ix.findings
        if f.service == service and f.kind in SYMPTOM_KINDS and f.id not in relayed
    ]


def generate(
    scope: IncidentScope, findings: list[Finding], evidence: list[Evidence], now: datetime
) -> list[Hypothesis]:
    ix = _Index(findings, evidence)
    onset = symptom_onset(findings, scope)
    lookback = now - timedelta(minutes=scope.window_minutes + 90)
    hyps: list[Hypothesis] = []

    # ---------------------------------------------------------------- deployments / config
    for d in ix.of("deployment"):
        if d.values["kind"] == "rollback" or (d.at and d.at < lookback):
            continue
        svc = d.service or "?"
        category: Category = (
            "failed_deploy"
            if d.values["status"] == "failed"
            else "config_change"
            if d.values["kind"] == "config_change"
            else "bad_deploy"
        )
        own = _own_symptoms(ix, svc)
        mech: list[Finding] = []
        pools = ix.of("pool_saturation", svc)
        if pools and (
            ix.of("slow_query")
            or ix.of("client_query_slow", svc)
            or ix.of("idle_in_transaction", svc)
        ):
            mech += (
                pools
                + ix.of("slow_query")
                + ix.of("client_query_slow", svc)
                + ix.of("idle_in_transaction", svc)
            )
        mech += (
            ix.of("worker_failures", svc)
            + ix.of("queue_backlog", svc)
            + ix.of("memory_growth", svc)
        )
        version_errors = [
            f
            for f in ix.of("log_versions", svc)
            if d.values["version"] in f.values.get("versions", {})
        ]
        if category == "failed_deploy":
            mech += version_errors
        supporting = [d, *own, *mech]
        contradicting = _passthrough_errors(ix, svc) + ix.of("pool_ok", svc)
        if not own and not mech:
            contradicting += ix.of("service_metrics", svc)[:1]  # deployed service looks healthy
        temporal = "n/a"
        if onset and d.at:
            started = (
                datetime.fromisoformat(str(d.values.get("started_at")).replace("Z", "+00:00"))
                if d.values.get("started_at")
                else d.at
            )
            if started <= onset + timedelta(minutes=1) and onset - d.at <= timedelta(minutes=25):
                temporal = "strong"
            elif d.at <= onset:
                temporal = "plausible"
            else:
                temporal = "violation"
        elif d.at:
            temporal = "plausible"
        # Did the service's signals change after this deployment? (compare_metric_windows)
        near = timedelta(minutes=5)
        after = [f for f in ix.of("change_after", svc) if f.at and d.at and abs(f.at - d.at) < near]
        flat = [
            f for f in ix.of("no_change_after", svc) if f.at and d.at and abs(f.at - d.at) < near
        ]
        if after:
            supporting += after
            temporal = "strong"
        elif flat:
            contradicting += flat
        mechanism = "strong" if mech else ("moderate" if own else "weak")
        hyps.append(
            Hypothesis(
                id=f"{category}:{svc}:{d.values['id']}",
                category=category,
                component=svc,
                title=f"{d.values['id']} ({svc} {d.values.get('previous_version')} -> {d.values['version']}) caused the incident",
                trigger_ref=d.values["id"],
                trigger_at=d.at,
                supporting=_ids(supporting),
                contradicting=_ids(contradicting),
                temporal=temporal,  # type: ignore[arg-type]
                mechanism=mechanism,  # type: ignore[arg-type]
                rationale=f"{d.detail}. Own symptoms: {len(own)}; mechanism findings: {len(mech)}.",
            )
        )

    # ---------------------------------------------------------------- redis
    redis_bad = ix.of("dependency_unhealthy", "redis")
    redis_sup = (
        redis_bad
        + ix.of("redis_errors")
        + [
            f
            for f in ix.of("error_signature")
            if f.values.get("error_type") in ("CacheUnavailableError",)
            or "shop-cache" in str(f.values.get("example"))
            or "queued" in str(f.values.get("template"))
        ]
    )
    if redis_sup:
        hyps.append(
            Hypothesis(
                id="cache_failure:redis",
                category="cache_failure",
                component="redis",
                title="Redis (shop-cache) is unavailable",
                supporting=_ids(redis_sup),
                contradicting=_ids(ix.of("dependency_healthy", "redis")),
                mechanism="strong" if redis_bad else "moderate",
                rationale="Redis errors/timeouts seen by callers.",
            )
        )

    # ---------------------------------------------------------------- payments provider
    pay_bad = ix.of("dependency_unhealthy", "payments-provider")
    pay_sup = (
        pay_bad
        + ix.of("upstream_errors", "orders-service")
        + [
            f
            for f in ix.of("error_signature")
            if "payments-provider" in str(f.values.get("template")) + str(f.values.get("example"))
        ]
    )
    if pay_sup:
        hyps.append(
            Hypothesis(
                id="dependency_failure:payments-provider",
                category="dependency_failure",
                component="payments-provider",
                title="The third-party payments provider is failing",
                supporting=_ids(pay_sup),
                contradicting=_ids(ix.of("dependency_healthy", "payments-provider")),
                mechanism="strong" if pay_bad else "moderate",
                rationale="Outbound payment calls fail or are slow.",
            )
        )

    # ---------------------------------------------------------------- postgres: network vs database
    uniform = ix.of("uniform_query_latency")
    slow_clients = ix.of("client_query_slow")
    pg_bad = ix.of("dependency_unhealthy", "postgres")
    if uniform or (len({f.service for f in slow_clients}) >= 2):
        hyps.append(
            Hypothesis(
                id="infrastructure:postgres-network",
                category="infrastructure",
                component="postgres",
                title="Network latency on the path to PostgreSQL",
                supporting=_ids(uniform + slow_clients + pg_bad + ix.of("db_healthy")),
                contradicting=_ids(ix.of("slow_query") + ix.of("idle_in_transaction")),
                mechanism="strong" if uniform and ix.of("db_healthy") else "moderate",
                rationale="Every query on every service is slower while the database itself is idle.",
            )
        )
    if ix.of("slow_query") or ix.of("long_running_query"):
        hyps.append(
            Hypothesis(
                id="database_degradation:postgres",
                category="database_degradation",
                component="postgres",
                title="Slow queries on PostgreSQL",
                supporting=_ids(
                    ix.of("slow_query") + ix.of("long_running_query") + ix.of("pool_saturation")
                ),
                contradicting=_ids(ix.of("db_healthy")),
                mechanism="moderate",
                rationale="Server-side statements are slow; the trigger is not established.",
            )
        )

    # ---------------------------------------------------------------- worker / memory / traffic
    worker = ix.of("queue_backlog") + ix.of("worker_failures")
    if worker:
        hyps.append(
            Hypothesis(
                id="worker_degradation:fulfilment-worker",
                category="worker_degradation",
                component="fulfilment-worker",
                title="fulfilment-worker cannot keep up",
                supporting=_ids(worker),
                mechanism="moderate",
                rationale="Queue grows and jobs fail; trigger not established.",
            )
        )
    for svc in {
        f.service for f in ix.of("memory_growth") + ix.of("container_pressure") if f.service
    }:
        mem = (
            ix.of("memory_growth", svc) + ix.of("container_pressure", svc) + ix.of("restarts", svc)
        )
        hyps.append(
            Hypothesis(
                id=f"resource_exhaustion:{svc}",
                category="resource_exhaustion",
                component=svc,
                title=f"{svc} is running out of memory",
                supporting=_ids(mem),
                mechanism="moderate",
                rationale="Resident memory grows steadily; trigger not established.",
            )
        )
    surge = [
        f
        for f in ix.of("metric_anomaly", "api-gateway")
        if f.values.get("signal") == "request_rate" and (f.values.get("change_pct") or 0) > 50
    ]
    if surge:
        hyps.append(
            Hypothesis(
                id="capacity:traffic",
                category="capacity",
                component="api-gateway",
                title="Traffic surge beyond capacity",
                supporting=_ids(surge),
                mechanism="moderate",
                rationale="Request rate rose sharply.",
            )
        )

    # ---------------------------------------------------------------- common evaluation
    similar = ix.of("similar_incident")
    failed = sum(1 for e in evidence if not e.ok and e.server != "incidents")
    scope_services = set(scope.services)
    for h in hyps:
        h.sources = ix.domains(h.supporting)
        h.on_causal_path = (
            not scope_services
            or h.component in scope_services
            or any(h.component in DEPENDS_ON.get(s, set()) for s in scope_services)
            or h.component in {s for svc in scope_services for s in DEPENDS_ON.get(svc, set())}
        )
        match = next(
            (
                f
                for f in similar
                if f.values["category"] == h.category
                and (
                    h.component in f.values["services"]
                    or h.component in ("redis", "postgres", "payments-provider")
                )
                and f.values.get("score", 0) > 0
                and f.values.get("confident", True)
            ),
            None,
        )
        h.historical_match = match.values["id"] if match else None
        score(h, failed)
    hyps.sort(key=lambda h: h.confidence, reverse=True)
    return hyps


def sufficient(hyps: list[Hypothesis], threshold: float) -> bool:
    if not hyps:
        return False
    top = hyps[0]
    margin = top.confidence - (hyps[1].confidence if len(hyps) > 1 else 0.0)
    return top.confidence >= threshold and margin >= 0.10
