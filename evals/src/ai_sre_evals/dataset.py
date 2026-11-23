"""Evaluation dataset: incident scenarios with ground truth (see docs/evaluation.md)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

FaultType = Literal[
    "api_latency",
    "http_errors",
    "db_pool_exhaustion",
    "slow_db_queries",
    "db_network_latency",
    "redis_failure",
    "worker_backlog",
    "failed_deployment",
    "memory_pressure",
    "dependency_outage",
    "dependency_errors",
    "traffic_surge",
]

Tag = Literal[
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
    "resource_exhaustion",
    "capacity",
    "network",
]


class Fault(BaseModel):
    """One injected fault, using the simulator's fault types (``simctl fault-types``)."""

    type: FaultType
    target: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    onset_minutes_ago: float = 10
    caused_by: str | None = Field(default=None, description="Deployment id that introduced it")
    knowledge: bool = Field(default=True, description="Knowledge base has a similar incident")


class Deployment(BaseModel):
    id: str
    service: str
    kind: Literal["deploy", "config_change", "rollback"] = "deploy"
    version: str
    previous_version: str | None = None
    status: Literal["succeeded", "failed", "in_progress"] = "succeeded"
    minutes_ago: float
    changes_summary: str = "Routine release"
    author: str = "ci-bot"
    failure_reason: str | None = None


class InjectedFault(BaseModel):
    """How the incident is produced: faults + deployments, and the live-simulator equivalent."""

    sim_scenario: str | None = Field(
        default=None, description="Matching predefined simulator scenario (live mode), if any"
    )
    faults: list[Fault] = Field(default_factory=list)
    deployments: list[Deployment] = Field(default_factory=list)


class Cause(BaseModel):
    category: str
    component: str
    trigger_ref: str | None = None


class Remediation(BaseModel):
    action: str = Field(
        description="Expected first proposal action, e.g. rollback_deployment_simulation"
    )
    target: str | None = Field(default=None, description="Service the action must target")
    keywords: list[str] = Field(
        default_factory=list, description="For manual actions: words the description must contain"
    )


class Expected(BaseModel):
    root_cause: Cause | None = Field(
        description="None: no root cause is identifiable; the agent should abstain"
    )
    also_correct: list[Cause] = Field(
        default_factory=list, description="Other true causes (simultaneous failures): full credit"
    )
    acceptable_alternatives: list[Cause] = Field(
        default_factory=list, description="Partially right (symptom, not trigger): half credit"
    )
    affected_services: list[str]
    evidence: list[str] = Field(description="server.tool keys whose results prove the root cause")
    tools: list[str] = Field(description="server.tool keys a competent investigation consults")
    remediation: Remediation
    unsafe_actions: list[str] = Field(default_factory=list)
    severity: Literal["SEV1", "SEV2", "SEV3", "SEV4"]


class Scenario(BaseModel):
    id: str
    title: str
    description: str = Field(description="What on-call sees; the story of the incident")
    query: str = Field(description="The question asked to the agent")
    tags: list[Tag]
    injected_fault: InjectedFault
    world: dict[str, Any] = Field(
        default_factory=dict,
        description="Extra world settings: unavailable/flaky tools, overrides, knowledge_confident",
    )
    expected: Expected

    @model_validator(mode="after")
    def _deployment_refs(self) -> Scenario:
        ids = {d.id for d in self.injected_fault.deployments}
        refs = [f.caused_by for f in self.injected_fault.faults if f.caused_by]
        if self.expected.root_cause and self.expected.root_cause.trigger_ref:
            refs.append(self.expected.root_cause.trigger_ref)
        missing = [r for r in refs if r not in ids]
        if missing:
            raise ValueError(f"{self.id}: unknown deployment ids {missing}")
        return self

    def world_spec(self) -> dict[str, Any]:
        return {
            **self.world,
            "faults": [f.model_dump() for f in self.injected_fault.faults],
            "deployments": [d.model_dump() for d in self.injected_fault.deployments],
        }


class Dataset(BaseModel):
    name: str
    version: str
    description: str
    scenarios: list[Scenario]

    @model_validator(mode="after")
    def _unique(self) -> Dataset:
        ids = [s.id for s in self.scenarios]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"duplicate scenario ids: {sorted(dupes)}")
        return self


def load_dataset(path: str | Path) -> Dataset:
    return Dataset.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))
