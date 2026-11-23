"""Fault catalog: every injectable fault type, its parameters, targets and mechanism.

Mechanisms:

* ``in_app``      the target service applies the effect itself (it polls the chaos controller
                  for active faults every second). Examples: extra latency, error responses,
                  leaked DB connections, a query-plan regression, memory growth.
* ``network``     the chaos controller adds a toxic to (or disables) a Toxiproxy proxy that
                  sits between the services and PostgreSQL / Redis / the payments provider.
                  The services see genuine timeouts and refused connections.
* ``deployment``  the chaos controller asks the deployer to roll out a version; the version's
                  behaviour flags change what the service does.
* ``traffic``     the load generator changes its request rate.

Fault definitions are never exported as metrics or logs, so an investigator that reads only
the monitoring interfaces sees symptoms, not the injected cause.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

APP_SERVICES = ("api-gateway", "orders-service", "fulfilment-worker")
HTTP_SERVICES = ("api-gateway", "orders-service")


class FaultType(StrEnum):
    API_LATENCY = "api_latency"
    HTTP_ERRORS = "http_errors"
    DB_POOL_EXHAUSTION = "db_pool_exhaustion"
    SLOW_DB_QUERIES = "slow_db_queries"
    DB_NETWORK_LATENCY = "db_network_latency"
    REDIS_FAILURE = "redis_failure"
    WORKER_BACKLOG = "worker_backlog"
    FAILED_DEPLOYMENT = "failed_deployment"
    MEMORY_PRESSURE = "memory_pressure"
    DEPENDENCY_OUTAGE = "dependency_outage"
    DEPENDENCY_ERRORS = "dependency_errors"
    TRAFFIC_SURGE = "traffic_surge"


Mechanism = Literal["in_app", "network", "deployment", "traffic"]


class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ApiLatencyParams(_Params):
    delay_ms: int = Field(ge=1, le=30_000)
    jitter_ms: int = Field(default=0, ge=0, le=30_000)
    probability: float = Field(default=1.0, gt=0, le=1)
    route_prefix: str = "/"


class HttpErrorsParams(_Params):
    rate: float = Field(gt=0, le=1)
    status: int = Field(default=500, ge=500, le=599)
    route_prefix: str = "/"


class DbPoolExhaustionParams(_Params):
    """Each matching request leaks one connection (left idle in transaction) with this
    probability, until ``max_leaked`` (default: the whole pool) are held."""

    leak_probability: float = Field(default=0.25, gt=0, le=1)
    max_leaked: int | None = Field(default=None, ge=1, le=200)
    route_prefix: str = "/"


class SlowDbQueriesParams(_Params):
    """Query-plan regression: index scans are disabled for the named queries (``*`` = all),
    so they fall back to sequential scans. Real slow queries, real DB CPU."""

    queries: list[str] = Field(default_factory=lambda: ["*"], min_length=1)


class DbNetworkLatencyParams(_Params):
    latency_ms: int = Field(ge=1, le=10_000)
    jitter_ms: int = Field(default=0, ge=0, le=10_000)


class RedisFailureParams(_Params):
    """``down``: connections refused. ``timeout``: connections hang until the client times
    out. ``latency``: every command is delayed by ``latency_ms``."""

    mode: Literal["down", "timeout", "latency"] = "timeout"
    latency_ms: int = Field(default=800, ge=1, le=10_000)


class WorkerBacklogParams(_Params):
    delay_ms: int = Field(default=1500, ge=0, le=60_000)
    failure_rate: float = Field(default=0.5, ge=0, le=1)


class FailedDeploymentParams(_Params):
    version: str = Field(min_length=1, max_length=40)
    canary_fraction: float = Field(default=0.25, gt=0, le=1)
    changes_summary: str = "Routine release"


class MemoryPressureParams(_Params):
    mb_per_second: float = Field(default=2.0, gt=0, le=100)
    max_mb: int = Field(default=200, ge=1, le=4096)


class DependencyOutageParams(_Params):
    mode: Literal["down", "timeout"] = "down"


class DependencyErrorsParams(_Params):
    error_rate: float = Field(default=0.5, ge=0, le=1)
    status: int = Field(default=503, ge=400, le=599)
    extra_latency_ms: int = Field(default=0, ge=0, le=30_000)


class TrafficSurgeParams(_Params):
    multiplier: float = Field(gt=1, le=10)


@dataclass(frozen=True, slots=True)
class FaultSpec:
    params: type[_Params]
    targets: tuple[str, ...]
    mechanism: Mechanism
    description: str


FAULT_SPECS: dict[FaultType, FaultSpec] = {
    FaultType.API_LATENCY: FaultSpec(
        ApiLatencyParams, HTTP_SERVICES, "in_app", "Adds latency to matching HTTP requests."
    ),
    FaultType.HTTP_ERRORS: FaultSpec(
        HttpErrorsParams, HTTP_SERVICES, "in_app", "Fails a fraction of requests with 5xx."
    ),
    FaultType.DB_POOL_EXHAUSTION: FaultSpec(
        DbPoolExhaustionParams,
        APP_SERVICES,
        "in_app",
        "Leaks DB connections until the pool is exhausted.",
    ),
    FaultType.SLOW_DB_QUERIES: FaultSpec(
        SlowDbQueriesParams,
        APP_SERVICES,
        "in_app",
        "Query-plan regression: named queries run as sequential scans.",
    ),
    FaultType.DB_NETWORK_LATENCY: FaultSpec(
        DbNetworkLatencyParams,
        ("shop-db",),
        "network",
        "Adds latency on the network path to PostgreSQL.",
    ),
    FaultType.REDIS_FAILURE: FaultSpec(
        RedisFailureParams, ("shop-cache",), "network", "Makes Redis unreachable or slow."
    ),
    FaultType.WORKER_BACKLOG: FaultSpec(
        WorkerBacklogParams,
        ("fulfilment-worker",),
        "in_app",
        "Slows down and fails fulfilment jobs so the queue accumulates.",
    ),
    FaultType.FAILED_DEPLOYMENT: FaultSpec(
        FailedDeploymentParams,
        HTTP_SERVICES,
        "deployment",
        "Rolls out a version whose canary fails health checks; the deploy ends 'failed'.",
    ),
    FaultType.MEMORY_PRESSURE: FaultSpec(
        MemoryPressureParams, APP_SERVICES, "in_app", "Grows resident memory at a fixed rate."
    ),
    FaultType.DEPENDENCY_OUTAGE: FaultSpec(
        DependencyOutageParams,
        ("payments-provider",),
        "network",
        "Makes the third-party payments provider unreachable.",
    ),
    FaultType.DEPENDENCY_ERRORS: FaultSpec(
        DependencyErrorsParams,
        ("payments-provider",),
        "in_app",
        "The payments provider returns errors for a fraction of calls.",
    ),
    FaultType.TRAFFIC_SURGE: FaultSpec(
        TrafficSurgeParams, ("loadgen",), "traffic", "Multiplies user traffic."
    ),
}


def validate_params(fault_type: FaultType, params: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalise ``params`` (fills defaults). Raises ``pydantic.ValidationError``."""
    return FAULT_SPECS[fault_type].params.model_validate(params).model_dump()


class FaultRequest(BaseModel):
    """A request to inject one fault."""

    model_config = ConfigDict(extra="forbid")

    type: FaultType
    target: str
    params: dict[str, Any] = Field(default_factory=dict)
    seed: int | None = None

    @model_validator(mode="after")
    def _check(self) -> FaultRequest:
        spec = FAULT_SPECS[self.type]
        if self.target not in spec.targets:
            raise ValueError(
                f"target {self.target!r} is not valid for {self.type.value}; "
                f"valid targets: {', '.join(spec.targets)}"
            )
        self.params = validate_params(self.type, self.params)
        return self


class ActiveFault(BaseModel):
    id: str
    type: FaultType
    target: str
    params: dict[str, Any]
    seed: int
    scenario_run_id: str | None = None
    created_at: datetime


class RuntimeFault(BaseModel):
    """One active fault, as a service receives it when polling."""

    id: str
    type: FaultType
    params: dict[str, Any]
    seed: int


class RuntimeFaults(BaseModel):
    """Response of ``GET /internal/faults/{service}`` on the chaos controller."""

    epoch: int
    faults: list[RuntimeFault]
    # Orchestrator state for this service (remediation actions):
    restart_generation: int = (
        0  # incremented by a restart; the service process exits and Docker restarts it
    )
    replicas: int = 1  # desired replicas (the worker runs `concurrency x replicas` consumers)
