"""Deployment records and requests."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ai_sre_simulator.common.faults import APP_SERVICES

DeploymentKind = Literal["deploy", "rollback", "config_change"]
DeploymentStatus = Literal["in_progress", "succeeded", "failed"]
AppService = Literal["api-gateway", "orders-service", "fulfilment-worker"]

if set(AppService.__args__) != set(APP_SERVICES):  # type: ignore[attr-defined]
    raise RuntimeError("AppService and APP_SERVICES are out of sync")


def commit_sha_for(service: str, version: str) -> str:
    """A stable, fake commit SHA for a service version."""
    return hashlib.sha1(f"{service}@{version}".encode(), usedforsecurity=False).hexdigest()[:12]


class CanarySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fraction: float = Field(gt=0, le=1)
    error: str = Field(min_length=1)


class DeployRequest(BaseModel):
    """Start a rollout. ``flags`` define the behaviour of the new build and are never exposed
    through the public API (they stand in for the code inside the build)."""

    model_config = ConfigDict(extra="forbid")

    service: AppService
    version: str = Field(min_length=1, max_length=40)
    kind: DeploymentKind = "deploy"
    changes_summary: str = Field(min_length=1, max_length=500)
    author: str = Field(default="ci-bot", max_length=80)
    commit_sha: str | None = Field(default=None, max_length=40)
    flags: dict[str, Any] = Field(default_factory=dict)
    rollout_s: float = Field(default=8.0, ge=0, le=600)
    outcome: Literal["succeeded", "failed"] = "succeeded"
    failure_reason: str | None = Field(default=None, max_length=500)
    canary: CanarySpec | None = None


class Deployment(BaseModel):
    """Public view of a deployment record (what an SRE sees in the CD tool)."""

    id: str
    service: str
    kind: DeploymentKind
    version: str
    previous_version: str | None
    status: DeploymentStatus
    started_at: datetime
    finished_at: datetime | None
    changes_summary: str
    author: str
    commit_sha: str | None
    failure_reason: str | None


class ServiceVersion(BaseModel):
    service: str
    version: str
    deployment_id: str
    deployed_at: datetime | None


class RuntimeConfig(BaseModel):
    version: str
    flags: dict[str, Any]


class BaselineDeployment(BaseModel):
    service: AppService
    version: str
    age: timedelta
    changes_summary: str
    author: str


BASELINE: tuple[BaselineDeployment, ...] = (
    BaselineDeployment(
        service="api-gateway",
        version="3.7.4",
        age=timedelta(days=6, hours=3),
        changes_summary="Upgrade FastAPI and uvicorn; no functional changes",
        author="dependabot",
    ),
    BaselineDeployment(
        service="orders-service",
        version="2.2.0",
        age=timedelta(days=5, hours=1),
        changes_summary="Paginate order history endpoint (default 20, max 50)",
        author="m.okafor",
    ),
    BaselineDeployment(
        service="fulfilment-worker",
        version="1.6.2",
        age=timedelta(days=4, hours=6),
        changes_summary="Retry failed jobs up to 5 times, then dead-letter",
        author="j.lindqvist",
    ),
    BaselineDeployment(
        service="api-gateway",
        version="3.8.0",
        age=timedelta(days=3, hours=2),
        changes_summary="Product detail: add related-products widget",
        author="a.rahman",
    ),
    BaselineDeployment(
        service="orders-service",
        version="2.3.0",
        age=timedelta(days=2, hours=4),
        changes_summary="Payments client: 2.5 s timeout and idempotency references",
        author="m.okafor",
    ),
    BaselineDeployment(
        service="orders-service",
        version="2.3.1",
        age=timedelta(hours=26),
        changes_summary="Fix rounding of order totals for multi-quantity lines",
        author="s.ito",
    ),
)
"""Deterministic history every environment starts from (ids d-401 … d-406)."""
