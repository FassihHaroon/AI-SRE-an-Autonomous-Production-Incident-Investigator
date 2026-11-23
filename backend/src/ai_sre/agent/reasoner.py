"""Reasoners: the language-understanding parts of the workflow.

* :class:`RuleBasedReasoner`: deterministic intake parsing and planning. Used when no LLM key is
  configured, in tests, and as the fallback for every LLM failure.
* :class:`LLMReasoner`: asks the configured LLM for intake and planning through
  schema-validated structured output, then validates the plan against the tool allow-list.

What counts as evidence, the confidence number, and whether an action may execute are never
decided here (see findings.py, hypotheses.py and the approval node).
"""

from __future__ import annotations

import math
import re
from typing import Any, Protocol

import structlog
from pydantic import BaseModel, Field

from ai_sre.agent.models import Hypothesis, IncidentScope, PlannedCall, Severity
from ai_sre.integrations.llm.base import (
    LLMError,
    LLMMessage,
    LLMProvider,
    LLMRequest,
    generate_structured,
)

logger = structlog.get_logger("agent.reasoner")

APP_SERVICES = ["api-gateway", "orders-service", "fulfilment-worker"]
_SERVICE_WORDS: list[tuple[str, str]] = [
    (r"\bgateway\b|public api|\bcheckout\b|\bcatalog\b|\bproducts?\b|\bcarts?\b", "api-gateway"),
    (r"\borders?\b", "orders-service"),
    (r"\bworker\b|fulfil|shipping|\bqueue\b|backlog|not (being )?fulfilled", "fulfilment-worker"),
    (r"\bdatabase\b|\bdb\b|postgres", "postgres"),
    (r"\bredis\b|\bcache\b", "redis"),
    (r"payment", "payments-provider"),
]
_SYMPTOMS: list[tuple[str, str]] = [
    (r"\b5\d\d(?:s)?\b|5xx|errors?|failing|failures?|fail\b", "errors"),
    (r"latency|slow|timeouts?|p9\d", "latency"),
    (r"backlog|queue|stuck|not (being )?fulfilled", "backlog"),
    (r"memory|oom|restart", "memory"),
    (r"\bdown\b|unavailable|outage", "availability"),
]

# Signals compared before/after a deployment of each service.
DEPLOY_SIGNALS: dict[str, tuple[str, ...]] = {
    "api-gateway": ("error_rate", "latency_p99"),
    "orders-service": ("error_rate", "latency_p99"),
    "fulfilment-worker": ("worker_failure_rate", "queue_depth"),
}

# Tools the planner may choose from (read-only allow-list) with their purpose.
TOOL_CATALOG: dict[str, str] = {
    "metrics.detect_metric_anomalies": "which golden signals changed, and when (onset)",
    "metrics.get_service_metrics": "current golden signals of one service",
    "metrics.compare_metric_windows": "one signal now vs a baseline window",
    "logs.aggregate_errors": "error signatures with counts and first/last seen",
    "logs.get_error_logs": "recent error lines (versions, trace ids)",
    "logs.search_logs": "log lines matching a substring",
    "logs.get_logs_by_trace_id": "one request across services",
    "deployments.list_recent_deployments": "deployments and config changes in the window",
    "deployments.get_deployment_details": "one deployment in detail",
    "database.get_database_health": "connections by state, idle in transaction, locks",
    "database.get_slow_queries": "slowest statements (pg_stat_statements)",
    "database.get_connection_pool_status": "application connection pools",
    "database.get_database_metrics": "DB throughput and client-side query latency",
    "infrastructure.get_service_status": "up/version/restarts/alerts per service",
    "infrastructure.get_dependency_status": "health of orders-service, postgres, redis, payments, queue",
    "infrastructure.get_resource_usage": "CPU and memory growth",
    "infrastructure.get_container_health": "memory vs limit, restarts",
    "incidents.search_incident_history": "similar past incidents (keyword)",
    "knowledge.find_similar_incidents": "similar past incidents (hybrid retrieval, with citations)",
    "knowledge.search_knowledge": "runbooks, procedures, service/architecture docs (with citations)",
}


def parse_scope(question: str) -> IncidentScope:
    text = question.lower()
    services = list(
        dict.fromkeys(svc for pattern, svc in _SERVICE_WORDS if re.search(pattern, text))
    )
    if (
        "orders-service" in services
        and re.search(r"\bapi\b", text)
        and "api-gateway" not in services
    ):
        services.insert(0, "api-gateway")  # "orders API" is served through the gateway
    symptoms = list(dict.fromkeys(name for pattern, name in _SYMPTOMS if re.search(pattern, text)))
    assumptions, missing = [], []
    match = re.search(r"(?:last|past)\s+(\d+)\s*(minutes?|mins?|m\b|hours?|hrs?|h\b)", text)
    if match:
        window = int(match.group(1)) * (60 if match.group(2).startswith("h") else 1)
    elif re.search(r"last hour|past hour", text):
        window = 60
    else:
        window = 30
        assumptions.append("no time window given: assumed the last 30 minutes")
        missing.append("time window")
    window = max(5, min(window, 24 * 60))
    if not services:
        services = list(APP_SERVICES)
        assumptions.append("no service named: investigating all application services")
        missing.append("affected service")
    if not symptoms:
        missing.append("observed symptoms")
    severity: Severity
    if re.search(r"outage|\bdown\b|completely|all (requests|users)", text):
        severity = "SEV1"
    elif ("errors" in symptoms and "latency" in symptoms) or re.search(
        r"orders? (are )?failing|checkout", text
    ):
        severity = "SEV2"
    elif symptoms:
        severity = "SEV3"
    else:
        severity = "SEV4"
    return IncidentScope(
        question=question.strip(),
        services=services,
        window_minutes=window,
        severity=severity,
        symptoms=symptoms,
        missing_information=missing,
        assumptions=assumptions,
    )


def triage_calls(scope: IncidentScope) -> list[PlannedCall]:
    app = [s for s in scope.services if s in APP_SERVICES] or list(APP_SERVICES)
    window = min(scope.window_minutes, 120)
    return [
        PlannedCall(
            server="metrics",
            tool="detect_metric_anomalies",
            arguments={
                "services": list(APP_SERVICES),
                "since_minutes": min(window, 60),
                "baseline_minutes": 60,
            },
            purpose="Which signals changed and when (onset)",
            priority=1,
        ),
        PlannedCall(
            server="deployments",
            tool="list_recent_deployments",
            arguments={
                "since_hours": max(1, math.ceil((scope.window_minutes + 90) / 60)),
                "limit": 20,
            },
            purpose="Changes shortly before the onset",
            priority=1,
        ),
        PlannedCall(
            server="logs",
            tool="aggregate_errors",
            arguments={"since_minutes": window, "top_k": 10},
            purpose="Dominant error signatures",
            priority=1,
        ),
        PlannedCall(
            server="infrastructure",
            tool="get_dependency_status",
            arguments={},
            purpose="Health of dependencies (DB, cache, payments, queue)",
            priority=1,
        ),
        PlannedCall(
            server="infrastructure",
            tool="get_service_status",
            arguments={},
            purpose="Versions, restarts and firing alerts",
            priority=2,
        ),
        PlannedCall(
            server="database",
            tool="get_connection_pool_status",
            arguments={},
            purpose="Connection pool saturation",
            priority=2,
        ),
        PlannedCall(
            server="knowledge",
            tool="find_similar_incidents",
            arguments={
                "description": scope.question[:780],
                "services": scope.services[:5],
                "top_k": 3,
            },
            purpose="Similar past incidents (knowledge base)",
            priority=2,
        ),
        *[
            PlannedCall(
                server="metrics",
                tool="get_service_metrics",
                arguments={"service": s},
                purpose=f"Golden signals of {s}",
                priority=2,
            )
            for s in app[:2]
        ],
    ]


def targeted_calls(hypotheses: list[Hypothesis], scope: IncidentScope) -> list[PlannedCall]:
    """Discriminating calls for the leading hypotheses (rule-based planner, later rounds)."""
    calls: list[PlannedCall] = []
    window = min(scope.window_minutes, 120)

    def add(
        server: str,
        tool: str,
        args: dict[str, Any],
        purpose: str,
        h: Hypothesis | None,
        prio: int = 1,
    ) -> None:
        calls.append(
            PlannedCall(
                server=server,
                tool=tool,
                arguments=args,
                purpose=purpose,
                priority=prio,
                hypothesis=h.id if h else None,
            )
        )

    if not hypotheses or hypotheses[0].confidence < 0.3:
        # Triage found no explanation. Look where triage does not: the database server side and
        # resource pressure, which can degrade services without any deployment.
        add("database", "get_database_health", {}, "explore: sessions, idle in transaction", None)
        add(
            "database",
            "get_slow_queries",
            {"top_k": 5, "order_by": "mean_time", "min_calls": 5},
            "explore: slow statements",
            None,
        )
        add(
            "database",
            "get_database_metrics",
            {"window_minutes": 5},
            "explore: client query latency",
            None,
        )
        add(
            "infrastructure",
            "get_resource_usage",
            {"window_minutes": 15},
            "explore: CPU/memory",
            None,
            2,
        )

    for h in hypotheses[:3]:
        svc = h.component
        if h.trigger_ref:
            add(
                "deployments",
                "get_deployment_details",
                {"deployment_id": h.trigger_ref},
                f"details of {h.trigger_ref}",
                h,
            )
            if h.trigger_at and svc in APP_SERVICES:
                for signal in DEPLOY_SIGNALS[svc]:
                    add(
                        "metrics",
                        "compare_metric_windows",
                        {"service": svc, "signal": signal, "split_at": h.trigger_at.isoformat()},
                        f"did {signal} of {svc} change after {h.trigger_ref}?",
                        h,
                    )
            if svc in APP_SERVICES:
                add(
                    "metrics",
                    "get_service_metrics",
                    {"service": svc},
                    f"is {svc} itself unhealthy?",
                    h,
                )
                add(
                    "infrastructure",
                    "get_resource_usage",
                    {"service": svc, "window_minutes": 15},
                    f"memory/CPU of {svc}",
                    h,
                    2,
                )
            if svc in ("api-gateway", "orders-service"):
                add(
                    "logs",
                    "get_error_logs",
                    {"service": svc, "since_minutes": window, "limit": 30},
                    f"error lines (versions, types) of {svc}",
                    h,
                )
        if h.category in (
            "bad_deploy",
            "config_change",
            "database_degradation",
            "infrastructure",
        ) or any(k in h.rationale for k in ("pool", "query")):
            add("database", "get_database_health", {}, "idle-in-transaction / active sessions", h)
            add(
                "database",
                "get_slow_queries",
                {"top_k": 5, "order_by": "mean_time", "min_calls": 5},
                "slow statements",
                h,
            )
            add(
                "database",
                "get_database_metrics",
                {"window_minutes": 5},
                "client-side query latency by service",
                h,
                2,
            )
        if h.category == "cache_failure":
            add(
                "logs",
                "search_logs",
                {"contains": "cache", "level": "WARNING", "since_minutes": window, "limit": 20},
                "cache failures in logs",
                h,
            )
            add(
                "metrics",
                "get_service_metrics",
                {"service": "orders-service"},
                "redis/queue errors on orders",
                h,
            )
        if h.category == "dependency_failure":
            add(
                "logs",
                "search_logs",
                {
                    "service": "orders-service",
                    "contains": "payments-provider",
                    "since_minutes": window,
                    "limit": 20,
                },
                "payment failures in logs",
                h,
            )
            add(
                "metrics",
                "get_service_metrics",
                {"service": "orders-service"},
                "upstream error rate",
                h,
            )
        if h.category == "worker_degradation" or svc == "fulfilment-worker":
            add(
                "metrics",
                "get_service_metrics",
                {"service": "fulfilment-worker"},
                "queue depth and failures",
                h,
            )
            add(
                "logs",
                "get_error_logs",
                {"service": "fulfilment-worker", "since_minutes": window, "limit": 20},
                "worker failure reasons",
                h,
            )
        if h.category == "resource_exhaustion":
            add(
                "infrastructure",
                "get_container_health",
                {"service": svc},
                "memory vs limit, restarts",
                h,
            )
            add(
                "infrastructure",
                "get_resource_usage",
                {"service": svc, "window_minutes": 15},
                "memory growth",
                h,
            )
    return calls


class Reasoner(Protocol):
    name: str

    async def intake(self, question: str) -> IncidentScope: ...

    async def plan(
        self, scope: IncidentScope, hypotheses: list[Hypothesis], round_: int, done: set[str]
    ) -> tuple[str, list[PlannedCall]]: ...


class RuleBasedReasoner:
    name = "rules"

    async def intake(self, question: str) -> IncidentScope:
        return parse_scope(question)

    async def plan(
        self, scope: IncidentScope, hypotheses: list[Hypothesis], round_: int, done: set[str]
    ) -> tuple[str, list[PlannedCall]]:
        if round_ == 1:
            return (
                "Triage: anomalies, recent changes, error signatures, dependencies, history.",
                triage_calls(scope),
            )
        calls = [c for c in targeted_calls(hypotheses, scope) if c.key() not in done]
        lead = ", ".join(h.id for h in hypotheses[:3]) or "none"
        return f"Discriminate between the leading hypotheses ({lead}).", calls


# ------------------------------------------------------------------------------ LLM variant
class _LLMScope(BaseModel):
    services: list[str] = Field(
        description="Subset of: api-gateway, orders-service, fulfilment-worker, postgres, redis, payments-provider"
    )
    window_minutes: int = Field(ge=5, le=1440)
    severity: Severity
    symptoms: list[str]
    missing_information: list[str]


class _LLMCall(BaseModel):
    tool: str = Field(description="One of the catalog keys, e.g. metrics.get_service_metrics")
    arguments: dict[str, Any]
    purpose: str


class _LLMPlan(BaseModel):
    rationale: str
    calls: list[_LLMCall] = Field(max_length=8)


class LLMReasoner:
    """LLM-driven intake and planning with validation and rule-based fallback."""

    name = "llm"

    PROMPTS = ("default", "hypothesis_first")

    def __init__(self, provider: LLMProvider, prompt: str = "default") -> None:
        if prompt not in self.PROMPTS:
            raise ValueError(f"unknown prompt variant {prompt!r}; one of {self.PROMPTS}")
        self._llm = provider
        self._rules = RuleBasedReasoner()
        self.prompt = prompt

    async def intake(self, question: str) -> IncidentScope:
        base = parse_scope(question)
        prompt = (
            "Extract the incident scope from this SRE question. Services must be chosen from the list "
            f"in the schema.\n\nQuestion: {question}"
        )
        try:
            parsed, _ = await generate_structured(
                self._llm,
                LLMRequest(messages=[LLMMessage(role="user", content=prompt)], node="intake"),
                _LLMScope,
            )
        except LLMError as exc:
            logger.warning("llm_intake_fallback", error=str(exc))
            return base
        allowed = {*APP_SERVICES, "postgres", "redis", "payments-provider"}
        services = [s for s in parsed.services if s in allowed] or base.services
        return base.model_copy(
            update={
                "services": services,
                "window_minutes": parsed.window_minutes,
                "severity": parsed.severity,
                "symptoms": parsed.symptoms or base.symptoms,
                "missing_information": parsed.missing_information or base.missing_information,
            }
        )

    def _instruction(self) -> str:
        if self.prompt == "hypothesis_first":
            return (
                "For each of the top 3 hypotheses choose the single read-only tool call most likely to "
                "DISPROVE it (a result that would contradict it), then add calls that confirm the "
                "mechanism of the leading one. At most 8 calls."
            )
        return "Choose up to 8 read-only tool calls that best discriminate between the hypotheses."

    async def plan(
        self, scope: IncidentScope, hypotheses: list[Hypothesis], round_: int, done: set[str]
    ) -> tuple[str, list[PlannedCall]]:
        rationale, fallback = await self._rules.plan(scope, hypotheses, round_, done)
        if round_ == 1:
            return rationale, fallback  # triage is fixed: broad and cheap
        catalog = "\n".join(f"- {k}: {v}" for k, v in TOOL_CATALOG.items())
        hyps = "\n".join(
            f"- {h.id} conf={h.confidence} supporting={len(h.supporting)} "
            f"contradicting={len(h.contradicting)}"
            for h in hypotheses[:5]
        )
        prompt = (
            f"Incident: {scope.question}\nServices: {scope.services}\nRound {round_}. Current hypotheses:\n{hyps}\n\n"
            f"{self._instruction()}\nTools:\n{catalog}"
        )
        try:
            parsed, _ = await generate_structured(
                self._llm,
                LLMRequest(messages=[LLMMessage(role="user", content=prompt)], node="planning"),
                _LLMPlan,
            )
        except LLMError as exc:
            logger.warning("llm_plan_fallback", error=str(exc))
            return rationale, fallback
        calls = []
        for c in parsed.calls:
            if c.tool not in TOOL_CATALOG:
                continue  # never trust the model with the allow-list
            server, tool = c.tool.split(".", 1)
            call = PlannedCall(
                server=server, tool=tool, arguments=c.arguments, purpose=c.purpose, priority=1
            )
            if call.key() not in done:
                calls.append(call)
        return (parsed.rationale, calls) if calls else (rationale, fallback)
