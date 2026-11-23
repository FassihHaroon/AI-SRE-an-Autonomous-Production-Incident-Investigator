"""Monitored-environment data for the UI: overview, services, metrics, logs, deployments.

Read through the MCP tools (the same interface the agent uses). Data is live from the simulated
environment: nothing here is synthesised.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request

from ai_sre.mcp_servers.metrics import SIGNALS, applicable
from ai_sre.services.platform import (
    APP_SERVICES,
    OBSERVED,
    PlatformError,
    PlatformService,
    metric_catalog,
)

router = APIRouter(tags=["platform"])


def _svc(request: Request) -> PlatformService:
    service: PlatformService = request.app.state.platform
    return service


async def _guard(coro: Any) -> Any:
    try:
        return await coro
    except PlatformError as exc:
        raise HTTPException(502, f"{exc.tool} unavailable ({exc.code}): {exc}") from exc


@router.get("/overview")
async def overview(request: Request) -> dict[str, Any]:
    data: dict[str, Any] = await _guard(_svc(request).overview())
    repo = request.app.state.investigations.repo
    active = [
        r for r in await repo.list_recent(50) if r["status"] in ("running", "awaiting_approval")
    ]
    data["active_investigations"] = [
        {
            "id": r["id"],
            "question": r["question"],
            "status": r["status"],
            "severity": r["severity"],
            "created_at": r["created_at"],
        }
        for r in active
    ]
    return data


@router.get("/services")
async def services(request: Request) -> dict[str, Any]:
    data = await _guard(_svc(request).call("infrastructure", "get_service_status", {}))
    return {"services": data["services"]}


@router.get("/services/{service}")
async def service_detail(request: Request, service: str) -> dict[str, Any]:
    if service not in OBSERVED:
        raise HTTPException(404, f"unknown service {service}; known: {', '.join(OBSERVED)}")
    detail: dict[str, Any] = await _guard(_svc(request).service_detail(service))
    return detail


@router.get("/metrics/catalog")
async def catalog() -> dict[str, Any]:
    return {"services": list(APP_SERVICES), "signals": metric_catalog()}


@router.get("/metrics/series")
async def series(
    request: Request,
    services: Annotated[list[str], Query(min_length=1, max_length=3)],
    signal: str,
    since_minutes: Annotated[int, Query(ge=5, le=1440)] = 60,
    compare_offset_minutes: Annotated[int, Query(ge=0, le=10080)] = 0,
) -> dict[str, Any]:
    if signal not in SIGNALS:
        raise HTTPException(422, f"unknown signal {signal}")
    out = []
    for service in services:
        if service not in APP_SERVICES or not applicable(signal, service):
            raise HTTPException(422, f"{signal} is not available for {service}")
        svc = _svc(request)
        if compare_offset_minutes:
            out.append(
                await _guard(svc.compare(service, signal, since_minutes, compare_offset_minutes))
            )
        else:
            out.append(await _guard(svc.timeseries(service, signal, since_minutes, max_points=120)))
    return {
        "signal": signal,
        "unit": SIGNALS[signal][1],
        "since_minutes": since_minutes,
        "compare_offset_minutes": compare_offset_minutes,
        "series": out,
    }


@router.get("/deployments")
async def deployments(
    request: Request,
    service: str | None = None,
    since_hours: Annotated[int, Query(ge=1, le=720)] = 168,
) -> dict[str, Any]:
    args: dict[str, Any] = {"since_hours": since_hours, "limit": 50}
    if service:
        args["service"] = service
    data = await _guard(_svc(request).call("deployments", "list_recent_deployments", args))
    return {"deployments": data["deployments"]}


@router.get("/logs")
async def logs(
    request: Request,
    service: str | None = None,
    level: str | None = None,
    contains: Annotated[str | None, Query(max_length=200)] = None,
    since_minutes: Annotated[int, Query(ge=1, le=1440)] = 30,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> dict[str, Any]:
    args: dict[str, Any] = {"since_minutes": since_minutes, "limit": limit}
    for key, value in (("service", service), ("level", level), ("contains", contains)):
        if value:
            args[key] = value
    data: dict[str, Any] = await _guard(_svc(request).call("logs", "search_logs", args))
    return data
