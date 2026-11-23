"""Scenario definitions (``simulator/scenarios/*.yaml``): schema, loader, validation.

A scenario is a timeline of steps. Each step either rolls out a deployment (whose behaviour
flags may introduce a bug) or injects a fault. ``at_s`` is the offset from the trigger in
seconds; ``speed`` compresses the timeline (``speed=10`` runs a 300 s scenario in 30 s).
Scenarios are deterministic: fixed steps, fixed parameters and a fixed seed.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from ai_sre_simulator.common.faults import FaultRequest, FaultType
from ai_sre_simulator.deployer.models import AppService, CanarySpec

DEFAULT_SCENARIO_DIR = Path(__file__).resolve().parents[3] / "scenarios"
_SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


class DeployStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service: AppService
    version: str
    changes_summary: str
    kind: Literal["deploy", "config_change"] = "deploy"
    author: str = "ci-bot"
    flags: dict[str, Any] = Field(default_factory=dict)
    rollout_s: float = Field(default=8.0, ge=0, le=600)
    outcome: Literal["succeeded", "failed"] = "succeeded"
    failure_reason: str | None = None
    canary: CanarySpec | None = None


class FaultStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: FaultType
    target: str
    params: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate(self) -> FaultStep:
        normalised = FaultRequest(type=self.type, target=self.target, params=self.params)
        self.params = normalised.params
        return self


class Step(BaseModel):
    model_config = ConfigDict(extra="forbid")

    at_s: float = Field(ge=0)
    description: str
    deploy: DeployStep | None = None
    fault: FaultStep | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> Step:
        if (self.deploy is None) == (self.fault is None):
            raise ValueError("a step needs exactly one of 'deploy' or 'fault'")
        return self


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    category: str
    summary: str
    seed: int
    steps: list[Step] = Field(min_length=1)
    signals: list[str] = Field(
        min_length=1, description="Telemetry an SRE should see (documentation only)."
    )

    @model_validator(mode="after")
    def _check(self) -> Scenario:
        if not _SLUG.match(self.id):
            raise ValueError(f"id {self.id!r} must be a lowercase slug")
        offsets = [s.at_s for s in self.steps]
        if offsets != sorted(offsets):
            raise ValueError("steps must be ordered by at_s")
        return self

    @property
    def duration_s(self) -> float:
        return self.steps[-1].at_s


class ScenarioError(ValueError):
    pass


def load_scenario(path: Path) -> Scenario:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ScenarioError(f"{path.name}: invalid YAML: {exc}") from exc
    try:
        scenario = Scenario.model_validate(raw)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}"
            for err in exc.errors()
        )
        raise ScenarioError(f"{path.name}: {problems}") from exc
    if scenario.id != path.stem:
        raise ScenarioError(f"{path.name}: id {scenario.id!r} must match the file name")
    return scenario


def load_scenarios(directory: Path = DEFAULT_SCENARIO_DIR) -> dict[str, Scenario]:
    scenarios = [load_scenario(p) for p in sorted(directory.glob("*.yaml"))]
    return {s.id: s for s in scenarios}
