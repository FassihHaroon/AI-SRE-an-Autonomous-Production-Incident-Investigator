"""Clients for the systems the MCP tools read from and act on.

Each call has a timeout; transport and HTTP failures become :class:`BackendError`, which the tool
wrapper reports as ``backend_unavailable``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import asyncpg
import httpx

from ai_sre.mcp_servers.settings import McpSettings


class BackendError(Exception):
    def __init__(self, backend: str, message: str) -> None:
        super().__init__(f"{backend}: {message}")
        self.backend = backend


class _Http:
    name = "http"

    def __init__(self, client: httpx.AsyncClient, base_url: str) -> None:
        self._client = client
        self._base = base_url.rstrip("/")

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = await self._client.request(method, f"{self._base}{path}", **kwargs)
        except httpx.HTTPError as exc:
            raise BackendError(self.name, f"{type(exc).__name__}: {exc}") from exc
        if response.status_code >= 400:
            raise BackendError(
                self.name, f"HTTP {response.status_code} for {method} {path}: {response.text[:300]}"
            )
        return response.json()


class Prometheus(_Http):
    name = "prometheus"

    async def instant(self, expr: str) -> list[dict[str, Any]]:
        body = await self.request("GET", "/api/v1/query", params={"query": expr})
        result: list[dict[str, Any]] = body["data"]["result"]
        return result

    async def scalar(self, expr: str) -> float | None:
        rows = await self.instant(expr)
        if not rows:
            return None
        value = float(rows[0]["value"][1])
        return None if value != value else value  # NaN -> None

    async def range(
        self, expr: str, start: datetime, end: datetime, step_s: int
    ) -> list[dict[str, Any]]:
        body = await self.request(
            "GET",
            "/api/v1/query_range",
            params={
                "query": expr,
                "start": start.timestamp(),
                "end": end.timestamp(),
                "step": step_s,
            },
        )
        result: list[dict[str, Any]] = body["data"]["result"]
        return result

    async def alerts(self) -> list[dict[str, Any]]:
        body = await self.request("GET", "/api/v1/alerts")
        result: list[dict[str, Any]] = body["data"]["alerts"]
        return result


class Loki(_Http):
    name = "loki"

    async def query_range(
        self, query: str, start: datetime, end: datetime, limit: int, direction: str = "backward"
    ) -> list[tuple[int, str, dict[str, str]]]:
        body = await self.request(
            "GET",
            "/loki/api/v1/query_range",
            params={
                "query": query,
                "start": int(start.timestamp() * 1e9),
                "end": int(end.timestamp() * 1e9),
                "limit": limit,
                "direction": direction,
            },
        )
        rows = [
            (int(ts), line, stream["stream"])
            for stream in body["data"]["result"]
            for ts, line in stream["values"]
        ]
        rows.sort(key=lambda r: r[0], reverse=direction == "backward")
        return rows[:limit]

    async def instant_vector(self, query: str, at: datetime) -> list[dict[str, Any]]:
        body = await self.request(
            "GET", "/loki/api/v1/query", params={"query": query, "time": int(at.timestamp() * 1e9)}
        )
        result: list[dict[str, Any]] = body["data"]["result"]
        return result


class Deployer(_Http):
    name = "deployer"


class Orchestrator(_Http):
    name = "orchestrator"


class Database:
    """Read-only PostgreSQL access (role ``sre_readonly``, transaction read only)."""

    name = "postgres"

    def __init__(self, dsn: str, timeout_s: float) -> None:
        self._dsn = dsn
        self._timeout = timeout_s

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        try:
            conn = await asyncpg.connect(self._dsn, timeout=self._timeout)
        except (OSError, asyncpg.PostgresError) as exc:
            raise BackendError(self.name, f"cannot connect: {exc}") from exc
        try:
            async with conn.transaction(readonly=True):
                await conn.execute(f"SET LOCAL statement_timeout = {int(self._timeout * 1000)}")
                rows = await conn.fetch(sql, *args)
            return [dict(r) for r in rows]
        except asyncpg.PostgresError as exc:
            raise BackendError(self.name, f"query failed: {exc}") from exc
        finally:
            await conn.close()


@dataclass
class Backends:
    http: httpx.AsyncClient
    prometheus: Prometheus
    loki: Loki
    deployer: Deployer
    orchestrator: Orchestrator
    database: Database

    @classmethod
    def from_settings(cls, settings: McpSettings) -> Backends:
        http = httpx.AsyncClient(timeout=settings.backend_timeout_s)
        return cls(
            http=http,
            prometheus=Prometheus(http, settings.prometheus_url),
            loki=Loki(http, settings.loki_url),
            deployer=Deployer(http, settings.deployer_url),
            orchestrator=Orchestrator(http, settings.orchestrator_url),
            database=Database(settings.database_dsn.get_secret_value(), settings.backend_timeout_s),
        )

    async def close(self) -> None:
        await self.http.aclose()
