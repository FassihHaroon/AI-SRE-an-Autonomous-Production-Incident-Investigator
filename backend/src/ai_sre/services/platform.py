"""Read the monitored (simulated) environment for the UI, through the same MCP tools the agent uses.

One integration path: the API never talks to Prometheus/Loki/the deployer directly.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ai_sre.agent.gateway import ToolGateway
from ai_sre.mcp_servers.metrics import SIGNALS, applicable, signal_query

APP_SERVICES = ("api-gateway", "orders-service", "fulfilment-worker")
OBSERVED = (*APP_SERVICES, "deployer", "postgres", "redis")


class PlatformError(Exception):
    def __init__(self, tool: str, code: str | None, message: str | None) -> None:
        super().__init__(f"{tool}: {message}")
        self.tool = tool
        self.code = code or "error"


class PlatformService:
    def __init__(self, gateway: ToolGateway) -> None:
        self.gw = gateway

    async def call(self, server: str, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        ev = await self.gw.call(server, tool, args, 0)
        if not ev.ok or ev.data is None:
            raise PlatformError(f"{server}.{tool}", ev.error_code, ev.error_message)
        return ev.data

    async def _maybe(self, server: str, tool: str, args: dict[str, Any]) -> dict[str, Any] | None:
        """Partial pages: one failing panel must not break the whole screen."""
        try:
            return await self.call(server, tool, args)
        except PlatformError:
            return None

    async def overview(self) -> dict[str, Any]:
        status, deps, deploys, *metrics = await asyncio.gather(
            self._maybe("infrastructure", "get_service_status", {}),
            self._maybe("infrastructure", "get_dependency_status", {}),
            self._maybe("deployments", "list_recent_deployments", {"since_hours": 72, "limit": 8}),
            *(self._maybe("metrics", "get_service_metrics", {"service": s}) for s in APP_SERVICES),
        )
        return {
            "services": status["services"] if status else None,
            "dependencies": deps["dependencies"] if deps else None,
            "deployments": deploys["deployments"] if deploys else None,
            "golden_signals": {s: m for s, m in zip(APP_SERVICES, metrics, strict=True) if m},
            "unavailable": [
                name
                for name, v in (
                    ("services", status),
                    ("dependencies", deps),
                    ("deployments", deploys),
                )
                if v is None
            ],
        }

    async def timeseries(
        self, service: str, signal: str, since_minutes: int, max_points: int = 60
    ) -> dict[str, Any]:
        data = await self.call(
            "metrics",
            "get_metric_timeseries",
            {
                "promql": signal_query(signal, service),
                "since_minutes": since_minutes,
                "max_points": max_points,
                "max_series": 1,
            },
        )
        series = data["series"][0] if data["series"] else {"points": [], "stats": {}}
        return {
            "service": service,
            "signal": signal,
            "unit": SIGNALS[signal][1],
            "points": series["points"],
            "stats": series["stats"],
            "step_seconds": data["step_seconds"],
        }

    async def service_detail(self, service: str) -> dict[str, Any]:
        is_app = service in APP_SERVICES
        chart_signals = [
            s
            for s in ("request_rate", "error_rate", "latency_p99", "memory_bytes")
            if is_app and applicable(s, service)
        ]
        calls = [
            self._maybe("infrastructure", "get_service_status", {"service": service}),
            self._maybe("metrics", "get_service_metrics", {"service": service})
            if is_app
            else _none(),
            self._maybe("infrastructure", "get_container_health", {"service": service})
            if is_app
            else _none(),
            self._maybe(
                "logs", "search_logs", {"service": service, "since_minutes": 30, "limit": 40}
            ),
            self._maybe(
                "logs", "get_error_logs", {"service": service, "since_minutes": 60, "limit": 20}
            ),
            self._maybe(
                "deployments",
                "list_recent_deployments",
                {"service": service, "since_hours": 720, "limit": 20},
            )
            if is_app
            else _none(),
            *(self._safe_series(service, s, 60) for s in chart_signals),
        ]
        status, metrics, container, logs, errors, deploys, *series = await asyncio.gather(*calls)
        return {
            "service": service,
            "status": (status or {}).get("services", [None])[0],
            "metrics": metrics,
            "container": (container or {}).get("containers", [None])[0] if container else None,
            "logs": (logs or {}).get("entries"),
            "error_logs": (errors or {}).get("entries"),
            "deployments": (deploys or {}).get("deployments"),
            "series": [s for s in series if s],
        }

    async def _safe_series(self, service: str, signal: str, since: int) -> dict[str, Any] | None:
        try:
            return await self.timeseries(service, signal, since)
        except PlatformError:
            return None

    async def compare(
        self, service: str, signal: str, window_minutes: int, offset_minutes: int
    ) -> dict[str, Any]:
        """Current window and the same-length window `offset_minutes` earlier, aligned on the
        current window's timestamps (one range query, split server-side)."""
        data = await self.timeseries(
            service, signal, window_minutes + offset_minutes, max_points=120
        )
        from datetime import datetime, timedelta

        pts = [(datetime.fromisoformat(t.replace("Z", "+00:00")), v) for t, v in data["points"]]
        if not pts:
            return {**data, "points": [], "baseline": []}
        end = pts[-1][0]
        start = end - timedelta(minutes=window_minutes)
        current = [(t.isoformat(), v) for t, v in pts if t >= start]
        shift = timedelta(minutes=offset_minutes)
        baseline = [
            ((t + shift).isoformat(), v) for t, v in pts if start - shift <= t <= end - shift
        ]
        return {**data, "points": current, "baseline": baseline, "offset_minutes": offset_minutes}


async def _none() -> None:
    return None


def metric_catalog() -> list[dict[str, Any]]:
    return [
        {"signal": name, "unit": unit, "services": [s for s in APP_SERVICES if applicable(name, s)]}
        for name, (_, unit) in SIGNALS.items()
    ]
