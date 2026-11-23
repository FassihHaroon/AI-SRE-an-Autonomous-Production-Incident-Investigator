"""Health endpoints.

* ``GET /healthz``: liveness. The process is up. No dependency checks (used by Docker).
* ``GET /readyz``: readiness. Postgres and Redis are reachable; 503 otherwise.
* ``GET /api/v1/system/status``: the same readiness data plus build info, always 200, for
  the dashboard (proxied through the web container like every other API call).
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel

from ai_sre import __version__
from ai_sre.api.deps import HealthChecksDep, SettingsDep
from ai_sre.services.health import ReadinessReport, check_readiness

probes = APIRouter(tags=["health"])
system = APIRouter(prefix="/system", tags=["system"])


class LivenessResponse(BaseModel):
    status: Literal["ok"] = "ok"


class LLMStatus(BaseModel):
    provider: str
    model: str | None
    configured: bool


class SystemStatus(BaseModel):
    version: str
    environment: str
    readiness: ReadinessReport
    llm: LLMStatus


@probes.get("/healthz", response_model=LivenessResponse)
async def healthz() -> LivenessResponse:
    return LivenessResponse()


@probes.get(
    "/readyz",
    response_model=ReadinessReport,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadinessReport}},
)
async def readyz(
    response: Response, settings: SettingsDep, checks: HealthChecksDep
) -> ReadinessReport:
    report = await check_readiness(checks, settings.dependency_check_timeout_seconds)
    if report.status != "ready":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return report


@system.get("/status", response_model=SystemStatus)
async def system_status(settings: SettingsDep, checks: HealthChecksDep) -> SystemStatus:
    report = await check_readiness(checks, settings.dependency_check_timeout_seconds)
    # The LLM is not called here: a probe should not spend free-tier quota.
    return SystemStatus(
        version=__version__,
        environment=settings.environment,
        readiness=report,
        llm=LLMStatus(
            provider=settings.llm_provider,
            model=settings.gemini_model if settings.llm_provider == "gemini" else None,
            configured=settings.llm_configured,
        ),
    )
