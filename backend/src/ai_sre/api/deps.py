"""FastAPI dependencies that expose application-scoped resources to route handlers."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from ai_sre.config import Settings
from ai_sre.services.health import HealthCheck, postgres_check, redis_check


def get_app_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_health_checks(request: Request) -> dict[str, HealthCheck]:
    state = request.app.state
    return {"postgres": postgres_check(state.db_engine), "redis": redis_check(state.redis)}


SettingsDep = Annotated[Settings, Depends(get_app_settings)]
HealthChecksDep = Annotated[dict[str, HealthCheck], Depends(get_health_checks)]
