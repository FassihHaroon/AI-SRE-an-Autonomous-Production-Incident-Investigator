"""Dependency health checks used by the readiness endpoints."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Literal

from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ai_sre.observability.logging import get_logger

log = get_logger(__name__)

ComponentState = Literal["ok", "error"]


class ComponentHealth(BaseModel):
    name: str
    status: ComponentState
    latency_ms: float
    detail: str | None = None


class ReadinessReport(BaseModel):
    status: Literal["ready", "not_ready"]
    components: list[ComponentHealth]


HealthCheck = Callable[[], Awaitable[None]]


def postgres_check(engine: AsyncEngine) -> HealthCheck:
    async def check() -> None:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    return check


def redis_check(client: Redis) -> HealthCheck:
    async def check() -> None:
        if not await client.ping():
            raise RuntimeError("PING returned a falsy reply")

    return check


async def run_check(name: str, check: HealthCheck, timeout_seconds: float) -> ComponentHealth:
    started = time.perf_counter()
    try:
        await asyncio.wait_for(check(), timeout=timeout_seconds)
    except Exception as exc:  # any failure means "not ready"; details go to the log
        latency = round((time.perf_counter() - started) * 1000, 2)
        log.warning("health.check_failed", component=name, error=type(exc).__name__)
        # Only the exception type is exposed: messages can contain hosts or credentials.
        return ComponentHealth(
            name=name, status="error", latency_ms=latency, detail=type(exc).__name__
        )
    latency = round((time.perf_counter() - started) * 1000, 2)
    return ComponentHealth(name=name, status="ok", latency_ms=latency)


async def check_readiness(
    checks: dict[str, HealthCheck], timeout_seconds: float
) -> ReadinessReport:
    results = await asyncio.gather(
        *(run_check(name, check, timeout_seconds) for name, check in checks.items())
    )
    ready = all(r.status == "ok" for r in results)
    return ReadinessReport(status="ready" if ready else "not_ready", components=list(results))
