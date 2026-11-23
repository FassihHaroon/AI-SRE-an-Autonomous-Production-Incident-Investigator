"""Typed state of an investigation (LangGraph state + the Pydantic models it holds)."""

from __future__ import annotations

import operator
from datetime import datetime
from typing import Annotated, Any, Literal, TypedDict

from pydantic import BaseModel, Field

Severity = Literal["SEV1", "SEV2", "SEV3", "SEV4"]
Band = Literal["high", "medium", "low", "insufficient"]
StopReason = Literal[
    "sufficient_evidence",
    "max_rounds",
    "tool_budget",
    "tools_unavailable",
    "cancelled",
    "error",
    "time_budget",
    "plan_exhausted",
]
Category = Literal[
    "bad_deploy",
    "config_change",
    "failed_deploy",
    "cache_failure",
    "dependency_failure",
    "database_degradation",
    "infrastructure",
    "resource_exhaustion",
    "capacity",
    "worker_degradation",
]


class Limits(BaseModel):
    max_rounds: int = Field(default=3, ge=1, le=6)
    max_tool_calls: int = Field(default=24, ge=3, le=100)
    time_budget_s: float = Field(default=300, gt=0, le=3600)
    sufficient_confidence: float = Field(default=0.75, ge=0, le=1)
    verification_wait_s: float = Field(default=45, ge=0, le=600)


class IncidentScope(BaseModel):
    """Output of intake."""

    question: str
    services: list[str] = Field(description="Affected services (application or dependencies)")
    window_minutes: int
    severity: Severity
    symptoms: list[str]
    missing_information: list[str]
    assumptions: list[str]


class PlannedCall(BaseModel):
    server: str
    tool: str
    arguments: dict[str, Any]
    purpose: str
    priority: int = Field(default=2, ge=1, le=3, description="1 = highest value")
    hypothesis: str | None = None

    def key(self) -> str:
        import json

        return f"{self.server}.{self.tool}:{json.dumps(self.arguments, sort_keys=True)}"


class Plan(BaseModel):
    round: int
    rationale: str
    hypotheses_considered: list[str]
    calls: list[PlannedCall]


class Evidence(BaseModel):
    """One tool call and its (bounded) result. Cited by id in the report."""

    id: str
    round: int
    server: str
    tool: str
    arguments: dict[str, Any]
    ok: bool
    summary: str
    data: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    duration_ms: float
    at: datetime


class Finding(BaseModel):
    """A normalised fact extracted from evidence."""

    id: str
    evidence_id: str
    kind: str
    service: str | None = None
    at: datetime | None = Field(default=None, description="When it happened/started (UTC)")
    detail: str
    values: dict[str, Any] = Field(default_factory=dict)


class ScoreBreakdown(BaseModel):
    prior: float = 0.15
    corroboration: float = 0.0
    temporal: float = 0.0
    topology: float = 0.0
    mechanism: float = 0.0
    historical: float = 0.0
    contradictions: float = 0.0
    coverage_gaps: float = 0.0


class Hypothesis(BaseModel):
    id: str = Field(description="Stable key, e.g. bad_deploy:orders-service:d-408")
    category: Category
    component: str
    title: str
    trigger_ref: str | None = None
    trigger_at: datetime | None = None
    supporting: list[str] = Field(default_factory=list, description="Finding ids")
    contradicting: list[str] = Field(default_factory=list, description="Finding ids")
    sources: list[str] = Field(default_factory=list, description="Distinct evidence domains")
    temporal: Literal["strong", "plausible", "n/a", "violation"] = "n/a"
    on_causal_path: bool = True
    mechanism: Literal["strong", "moderate", "weak"] = "weak"
    historical_match: str | None = None
    rationale: str = ""
    confidence: float = 0.0
    band: Band = "insufficient"
    breakdown: ScoreBreakdown = Field(default_factory=ScoreBreakdown)


class TimelineEvent(BaseModel):
    at: datetime
    event: str
    source: str = Field(description="Finding or evidence id")


class RootCause(BaseModel):
    hypothesis_id: str
    category: Category
    component: str
    title: str
    trigger_ref: str | None
    explanation: str
    confidence: float
    band: Band
    evidence: list[str] = Field(description="Evidence ids cited")
    capped: str | None = Field(default=None, description="Why the band was capped, if it was")


class Proposal(BaseModel):
    id: str
    order: int
    action: str = Field(description="MCP remediation tool, or 'manual'")
    arguments: dict[str, Any] = Field(default_factory=dict)
    description: str
    risk: Literal["low", "medium", "high"]
    expected_impact: str
    reversible: bool
    executable: bool = Field(description="Has an MCP action and remediation is enabled")
    approval_request_id: str | None = None
    status: Literal[
        "proposed",
        "awaiting_approval",
        "approved",
        "rejected",
        "executed",
        "failed",
        "not_executable",
    ] = "proposed"
    result: dict[str, Any] | None = None
    error: str | None = None
    references: list[str] = Field(default_factory=list, description="Runbook/procedure citations")


class Decision(BaseModel):
    proposal_id: str
    approved: bool
    approver: str
    approval_token: str | None = None
    comment: str | None = None


class Verification(BaseModel):
    verdict: Literal["resolved", "improved", "no_change", "worse", "not_verified"]
    service: str | None
    before: dict[str, float | None]
    after: dict[str, float | None]
    details: str


class NodeError(BaseModel):
    node: str
    error: str
    at: datetime


class Report(BaseModel):
    investigation_id: str
    question: str
    summary: str
    severity: Severity
    affected_services: list[str]
    root_cause: RootCause | None
    alternatives: list[RootCause]
    timeline: list[TimelineEvent]
    evidence: list[dict[str, Any]] = Field(description="Cited evidence: id, tool, summary")
    recommendations: list[Proposal]
    actions_taken: list[Proposal]
    verification: Verification | None
    limitations: list[str]
    references: list[dict[str, Any]] = Field(
        default_factory=list, description="Knowledge-base citations (retrieved, never invented)"
    )
    stop_reason: StopReason | None
    rounds: int
    tool_calls: int
    markdown: str


class InvestigationState(TypedDict, total=False):
    # identity / config (set at start)
    investigation_id: str
    question: str
    started_at: datetime
    limits: Limits
    # intake + planning
    scope: IncidentScope
    round: int
    plan: Plan
    pending_calls: list[PlannedCall]
    executed_keys: Annotated[list[str], operator.add]
    # evidence and reasoning
    evidence: Annotated[list[Evidence], operator.add]
    findings: list[Finding]
    hypotheses: list[Hypothesis]
    timeline: list[TimelineEvent]
    root_cause: RootCause | None
    alternatives: list[RootCause]
    # control
    stop_reason: StopReason | None
    cancel_requested: bool
    # remediation
    remediation_enabled: bool
    proposals: list[Proposal]
    knowledge_refs: list[dict[str, Any]]
    decisions: list[Decision]
    baseline_metrics: dict[str, float | None]
    verification: Verification | None
    # output + accounting
    report: Report
    limitations: Annotated[list[str], operator.add]
    errors: Annotated[list[NodeError], operator.add]
