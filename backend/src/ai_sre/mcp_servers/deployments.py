"""deployments MCP server: deployment history from the CD system (read-only)."""

from datetime import datetime, timedelta
from typing import Annotated, Literal

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, Field

from ai_sre.mcp_servers.backends import BackendError, Backends
from ai_sre.mcp_servers.core import (
    AppService,
    ToolFailure,
    ToolResult,
    ToolRuntime,
    iso,
    tool,
    utcnow,
)


class Deployment(BaseModel):
    id: str
    service: str
    kind: Literal["deploy", "rollback", "config_change"]
    version: str
    previous_version: str | None
    status: Literal["in_progress", "succeeded", "failed"]
    started_at: datetime
    finished_at: datetime | None
    changes_summary: str
    author: str
    commit_sha: str | None
    failure_reason: str | None


class DeploymentListResult(ToolResult):
    since: str
    count: int
    deployments: list[Deployment]


class DeploymentDetailsResult(ToolResult):
    deployment: Deployment
    minutes_ago: float
    previous_deployment: Deployment | None = Field(
        description="Earlier deployment of the same service"
    )
    currently_running_version: str | None
    is_current_version: bool


class VersionCompareResult(ToolResult):
    service: str
    from_version: str
    to_version: str
    currently_running_version: str | None
    deployments_between: list[Deployment] = Field(
        description="Deployments that moved the service from `from_version` to `to_version`, oldest first"
    )
    changes: list[str]


def build_deployments_server(backends: Backends, runtime: ToolRuntime) -> MCPServer:
    server = MCPServer("deployments", instructions="Read-only deployment history (CD system).")
    api = backends.deployer

    async def fetch(service: str | None = None, limit: int = 500) -> list[Deployment]:
        params: dict[str, str | int] = {"limit": limit}
        if service:
            params["service"] = service
        rows = await api.request("GET", "/api/v1/deployments", params=params)
        return [Deployment.model_validate(r) for r in rows]

    async def running(service: str) -> str | None:
        for row in await api.request("GET", "/api/v1/services"):
            if row["service"] == service:
                return str(row["version"])
        return None

    @tool(server, runtime, read_only=True, title="List recent deployments")
    async def list_recent_deployments(
        service: Annotated[AppService | None, Field(description="Only this service")] = None,
        since_hours: Annotated[int, Field(ge=1, le=720)] = 24,
        status: Literal["in_progress", "succeeded", "failed"] | None = None,
        limit: Annotated[int, Field(ge=1, le=50)] = 20,
    ) -> DeploymentListResult:
        """Deployments, rollbacks and config changes, newest first, within a time window."""
        since = utcnow() - timedelta(hours=since_hours)
        params: dict[str, str | int] = {"limit": limit, "since": iso(since)}
        if service:
            params["service"] = service
        if status:
            params["status"] = status
        rows = [
            Deployment.model_validate(r)
            for r in await api.request("GET", "/api/v1/deployments", params=params)
        ]
        latest = (
            f"; latest {rows[0].id} {rows[0].service} {rows[0].version} ({rows[0].status})"
            if rows
            else ""
        )
        return DeploymentListResult(
            summary=f"{len(rows)} deployments in the last {since_hours} h{latest}",
            since=iso(since),
            count=len(rows),
            deployments=rows,
        )

    @tool(server, runtime, read_only=True, title="Get deployment details")
    async def get_deployment_details(
        deployment_id: Annotated[str, Field(pattern=r"^d-\d{1,9}$", description="e.g. d-408")],
    ) -> DeploymentDetailsResult:
        """One deployment with the previous deployment of the same service and whether its
        version is the one running now."""
        try:
            record = Deployment.model_validate(
                await api.request("GET", f"/api/v1/deployments/{deployment_id}")
            )
        except BackendError as exc:
            if "HTTP 404" in str(exc):
                recent = [d.id for d in await fetch(limit=10)]
                raise ToolFailure(
                    "not_found",
                    f"deployment {deployment_id} does not exist",
                    f"recent deployment ids: {', '.join(recent)}",
                ) from exc
            raise
        history = await fetch(record.service)
        earlier = [d for d in history if int(d.id[2:]) < int(record.id[2:])]
        current = await running(record.service)
        age = (utcnow() - record.started_at).total_seconds() / 60
        return DeploymentDetailsResult(
            summary=f"{record.id}: {record.service} {record.previous_version} -> {record.version} "
            f"({record.kind}, {record.status}) {age:.0f} min ago by {record.author}: {record.changes_summary}",
            deployment=record,
            minutes_ago=round(age, 1),
            previous_deployment=earlier[0] if earlier else None,
            currently_running_version=current,
            is_current_version=current == record.version,
        )

    @tool(server, runtime, read_only=True, title="Compare deployment versions")
    async def compare_deployment_versions(
        service: AppService,
        from_version: Annotated[str, Field(min_length=1, max_length=40)],
        to_version: Annotated[str, Field(min_length=1, max_length=40)],
    ) -> VersionCompareResult:
        """What changed between two versions of a service: the deployments in between with
        their change summaries, authors and commits."""
        history = list(reversed(await fetch(service)))  # oldest first
        versions = [d.version for d in history]
        missing = [v for v in (from_version, to_version) if v not in versions]
        if missing:
            raise ToolFailure(
                "not_found",
                f"{service} was never deployed at {', '.join(missing)}",
                f"known versions: {', '.join(dict.fromkeys(versions))}",
            )
        start = max(i for i, d in enumerate(history) if d.version == from_version)
        later = [i for i, d in enumerate(history) if d.version == to_version and i > start]
        if later:
            between = history[start + 1 : later[0] + 1]
        else:  # to_version is older: report the path backwards
            end = max(i for i, d in enumerate(history) if d.version == to_version)
            between = list(reversed(history[end + 1 : start + 1]))
        changes = [f"{d.id} {d.kind} {d.version}: {d.changes_summary}" for d in between]
        return VersionCompareResult(
            summary=f"{service} {from_version} -> {to_version}: {len(between)} deployment(s)",
            service=service,
            from_version=from_version,
            to_version=to_version,
            currently_running_version=await running(service),
            deployments_between=between,
            changes=changes,
        )

    return server
