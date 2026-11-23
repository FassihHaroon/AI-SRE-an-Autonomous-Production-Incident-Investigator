"""infrastructure MCP server: service status, container health, resources, dependencies."""

from datetime import UTC, datetime
from typing import Annotated, Literal

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, Field

from ai_sre.mcp_servers.backends import Backends
from ai_sre.mcp_servers.core import (
    APP_SERVICES,
    AppService,
    ObservedService,
    ToolResult,
    ToolRuntime,
    iso,
    tool,
)

# Static topology of the simulated environment (what a service catalogue would provide).
MEMORY_LIMITS = {
    "api-gateway": 320 * 2**20,
    "orders-service": 256 * 2**20,
    "fulfilment-worker": 256 * 2**20,
}
DEPENDENCIES: dict[str, list[str]] = {
    "api-gateway": ["orders-service", "postgres", "redis"],
    "orders-service": ["postgres", "redis", "payments-provider"],
    "fulfilment-worker": ["postgres", "redis"],
}
Health = Literal["healthy", "degraded", "down", "unknown"]


class ServiceStatus(BaseModel):
    service: str
    up: bool | None
    version: str | None
    uptime_s: float | None
    restarts_last_hour: int | None
    firing_alerts: list[str]
    health: Health


class ServiceStatusResult(ToolResult):
    services: list[ServiceStatus]


class ContainerHealth(BaseModel):
    service: str
    up: bool | None
    started_at: str | None
    restarts_last_hour: int | None
    memory_bytes: float | None
    memory_limit_bytes: int
    memory_utilization: float | None
    open_fds: float | None
    oom_risk: bool = Field(description="Memory above 85% of the container limit")


class ContainerHealthResult(ToolResult):
    containers: list[ContainerHealth]


class Usage(BaseModel):
    service: str
    cpu_avg_cores: float | None
    cpu_max_cores: float | None
    memory_avg_bytes: float | None
    memory_max_bytes: float | None
    memory_growth_bytes_per_min: float | None


class ResourceUsageResult(ToolResult):
    window_minutes: int
    usage: list[Usage]


class Dependency(BaseModel):
    name: str
    kind: Literal["service", "database", "cache", "third_party", "queue"]
    used_by: list[str]
    health: Health
    evidence: list[str]


class DependencyStatusResult(ToolResult):
    dependencies: list[Dependency]


def build_infrastructure_server(backends: Backends, runtime: ToolRuntime) -> MCPServer:
    server = MCPServer(
        "infrastructure", instructions="Read-only infrastructure and dependency status."
    )
    prom = backends.prometheus

    async def val(expr: str) -> float | None:
        return await prom.scalar(expr)

    async def firing() -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for alert in await prom.alerts():
            if alert["state"] == "firing":
                out.setdefault(alert["labels"].get("service", "global"), []).append(
                    alert["labels"]["alertname"]
                )
        return out

    @tool(server, runtime, read_only=True, title="Get service status")
    async def get_service_status(
        service: Annotated[ObservedService | None, Field(description="Only this service")] = None,
    ) -> ServiceStatusResult:
        """Up/down, running version, uptime, restarts in the last hour and firing alerts per
        service, with an overall health verdict."""
        alerts = await firing()
        services = [service] if service else [*APP_SERVICES, "deployer", "postgres", "redis"]
        out = []
        for svc in services:
            sel = f'service="{svc}"'
            up = await val(f"up{{{sel}}}")
            start = await val(f"process_start_time_seconds{{{sel}}}")
            restarts = await val(f"changes(process_start_time_seconds{{{sel}}}[1h])")
            build = await prom.instant(f"app_build_info{{{sel}}} == 1")
            svc_alerts = sorted(set(alerts.get(svc, [])))
            health: Health = (
                "unknown"
                if up is None
                else ("down" if up == 0 else ("degraded" if svc_alerts else "healthy"))
            )
            if svc == "redis" and await val("redis_up") == 0:
                health = "down"
            out.append(
                ServiceStatus(
                    service=svc,
                    up=None if up is None else up == 1,
                    version=build[0]["metric"].get("version") if build else None,
                    uptime_s=round(datetime.now(UTC).timestamp() - start, 0) if start else None,
                    restarts_last_hour=int(restarts) if restarts is not None else None,
                    firing_alerts=svc_alerts,
                    health=health,
                )
            )
        bad = [f"{s.service}={s.health}" for s in out if s.health != "healthy"]
        return ServiceStatusResult(
            summary="all healthy" if not bad else "; ".join(bad), services=out
        )

    @tool(server, runtime, read_only=True, title="Get container health")
    async def get_container_health(
        service: Annotated[AppService | None, Field(description="Only this service")] = None,
    ) -> ContainerHealthResult:
        """Container view per application service: running, start time, restarts, memory
        against the container limit (OOM risk), open file descriptors."""
        out = []
        for svc in [service] if service else list(APP_SERVICES):
            sel = f'service="{svc}"'
            mem = await val(f"process_resident_memory_bytes{{{sel}}}")
            start = await val(f"process_start_time_seconds{{{sel}}}")
            restarts = await val(f"changes(process_start_time_seconds{{{sel}}}[1h])")
            up = await val(f"up{{{sel}}}")
            limit = MEMORY_LIMITS[svc]
            util = round(mem / limit, 3) if mem is not None else None
            out.append(
                ContainerHealth(
                    service=svc,
                    up=None if up is None else up == 1,
                    started_at=iso(datetime.fromtimestamp(start, UTC)) if start else None,
                    restarts_last_hour=int(restarts) if restarts is not None else None,
                    memory_bytes=mem,
                    memory_limit_bytes=limit,
                    memory_utilization=util,
                    open_fds=await val(f"process_open_fds{{{sel}}}"),
                    oom_risk=bool(util and util > 0.85),
                )
            )
        return ContainerHealthResult(
            summary="; ".join(
                f"{c.service} mem {c.memory_utilization:.0%} of limit, restarts/h {c.restarts_last_hour}"
                for c in out
                if c.memory_utilization is not None
            ),
            containers=out,
        )

    @tool(server, runtime, read_only=True, title="Get resource usage")
    async def get_resource_usage(
        service: Annotated[AppService | None, Field(description="Only this service")] = None,
        window_minutes: Annotated[int, Field(ge=1, le=240)] = 15,
    ) -> ResourceUsageResult:
        """CPU (cores) and resident memory over a window: average, peak and memory growth
        rate (a steady positive growth suggests a leak)."""
        w = f"[{window_minutes}m:15s]"
        out = []
        for svc in [service] if service else list(APP_SERVICES):
            sel = f'service="{svc}"'
            cpu = f"rate(process_cpu_seconds_total{{{sel}}}[1m])"
            mem = f"process_resident_memory_bytes{{{sel}}}"
            growth = await val(f"deriv({mem}[{window_minutes}m])")
            out.append(
                Usage(
                    service=svc,
                    cpu_avg_cores=await val(f"avg_over_time({cpu}{w})"),
                    cpu_max_cores=await val(f"max_over_time({cpu}{w})"),
                    memory_avg_bytes=await val(f"avg_over_time({mem}[{window_minutes}m])"),
                    memory_max_bytes=await val(f"max_over_time({mem}[{window_minutes}m])"),
                    memory_growth_bytes_per_min=round(growth * 60, 1)
                    if growth is not None
                    else None,
                )
            )
        return ResourceUsageResult(
            summary="; ".join(
                f"{u.service} cpu {u.cpu_avg_cores or 0:.2f} cores, mem growth "
                f"{(u.memory_growth_bytes_per_min or 0) / 2**20:.1f} MiB/min"
                for u in out
            ),
            window_minutes=window_minutes,
            usage=out,
        )

    @tool(server, runtime, read_only=True, title="Get dependency status")
    async def get_dependency_status() -> DependencyStatusResult:
        """Health of each dependency (orders-service, PostgreSQL, Redis, payments provider,
        fulfilment queue) as seen from its callers, with the evidence used."""
        deps: list[Dependency] = []

        def verdict(bad: bool, warn: bool) -> Health:
            return "down" if bad else ("degraded" if warn else "healthy")

        # orders-service as seen by the gateway
        err = await val(
            'sum(rate(http_client_requests_total{service="api-gateway",peer="orders-service",status=~"5..|error"}[1m]))'
        )
        tot = await val(
            'sum(rate(http_client_requests_total{service="api-gateway",peer="orders-service"}[1m]))'
        )
        ratio = (err or 0) / tot if tot else 0.0
        deps.append(
            Dependency(
                name="orders-service",
                kind="service",
                used_by=["api-gateway"],
                health=verdict(ratio > 0.5, ratio > 0.02),
                evidence=[f"gateway->orders error ratio {ratio:.1%} ({tot or 0:.2f} req/s)"],
            )
        )
        # postgres
        pg_up = await val("pg_up")
        timeouts = await val("sum(rate(db_pool_acquire_timeouts_total[1m]))")
        q95 = await val(
            "histogram_quantile(0.95, sum by (le) (rate(db_query_duration_seconds_bucket[1m])))"
        )
        deps.append(
            Dependency(
                name="postgres",
                kind="database",
                used_by=list(APP_SERVICES),
                health=verdict(pg_up == 0, bool(timeouts) or (q95 or 0) > 0.25),
                evidence=[
                    f"pg_up={pg_up}",
                    f"pool acquire timeouts {timeouts or 0:.2f}/s",
                    f"client query p95 {q95 or 0:.3f}s",
                ],
            )
        )
        # redis
        redis_up = await val("redis_up")
        rerr = await val("sum(rate(redis_command_errors_total[1m]))")
        deps.append(
            Dependency(
                name="redis",
                kind="cache",
                used_by=list(APP_SERVICES),
                health=verdict(redis_up == 0, (rerr or 0) > 0.1),
                evidence=[f"redis_up={redis_up}", f"client errors {rerr or 0:.2f}/s"],
            )
        )
        # payments provider (third party; only the client view exists)
        perr = await val(
            'sum(rate(http_client_requests_total{peer="payments-provider",status=~"5..|error"}[1m]))'
        )
        ptot = await val('sum(rate(http_client_requests_total{peer="payments-provider"}[1m]))')
        p50 = await val(
            'histogram_quantile(0.5, sum by (le) (rate(http_client_request_duration_seconds_bucket{peer="payments-provider"}[1m])))'
        )
        pratio = (perr or 0) / ptot if ptot else 0.0
        deps.append(
            Dependency(
                name="payments-provider",
                kind="third_party",
                used_by=["orders-service"],
                health=verdict(pratio > 0.9, pratio > 0.02 or (p50 or 0) > 0.5),
                evidence=[f"error ratio {pratio:.1%}", f"p50 latency {p50 or 0:.3f}s"],
            )
        )
        # fulfilment queue
        depth = await val('sum(queue_depth{queue="fulfilment"})')
        age = await val('max(queue_oldest_job_age_seconds{queue="fulfilment"})')
        deps.append(
            Dependency(
                name="fulfilment-queue",
                kind="queue",
                used_by=["orders-service", "fulfilment-worker"],
                health=verdict(False, (depth or 0) > 50 or (age or 0) > 60),
                evidence=[f"depth {depth}", f"oldest job age {age}s"],
            )
        )
        bad = [f"{d.name}={d.health}" for d in deps if d.health != "healthy"]
        return DependencyStatusResult(
            summary="all dependencies healthy" if not bad else "; ".join(bad), dependencies=deps
        )

    return server
