"""database MCP server: PostgreSQL health, slow queries, pools (read-only).

Reads ``pg_stat_activity`` / ``pg_stat_statements`` through a read-only role (``pg_monitor``)
and client-side pool metrics from Prometheus.
"""

from typing import Annotated, Literal

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, Field

from ai_sre.mcp_servers.backends import Backends
from ai_sre.mcp_servers.core import (
    APP_SERVICES,
    AppService,
    ToolResult,
    ToolRuntime,
    clip,
    iso,
    tool,
)


class SessionGroup(BaseModel):
    application: str
    state: str
    count: int
    oldest_transaction_s: float | None
    longest_query_s: float | None
    sample_query: str | None


class DatabaseHealthResult(ToolResult):
    reachable: bool
    server_version: str
    max_connections: int
    total_connections: int
    connection_utilization: float
    idle_in_transaction: int
    waiting_on_locks: int
    cache_hit_ratio: float | None
    deadlocks: int
    sessions: list[SessionGroup]


class SlowQuery(BaseModel):
    queryid: str
    query: str
    calls: int
    mean_ms: float
    max_ms: float
    total_ms: float
    rows: int


class SlowQueriesResult(ToolResult):
    order_by: str
    stats_since: str | None = Field(description="pg_stat_statements is cumulative since this time")
    queries: list[SlowQuery]


class PoolStatus(BaseModel):
    service: str
    max_connections: float | None
    in_use: float | None
    idle: float | None
    pending_acquires: float | None
    utilization: float | None
    acquire_timeouts_per_s: float | None
    acquire_p95_s: float | None
    server_sessions: dict[str, int] = Field(description="pg_stat_activity sessions by state")


class PoolStatusResult(ToolResult):
    pools: list[PoolStatus]


class QueryLatency(BaseModel):
    service: str
    query: str
    p95_s: float
    calls_per_s: float


class DatabaseMetricsResult(ToolResult):
    window_minutes: int
    commits_per_s: float | None
    rollbacks_per_s: float | None
    rows_fetched_per_s: float | None
    active_sessions: float | None
    idle_in_transaction: float | None
    client_query_latency: list[QueryLatency] = Field(
        description="Slowest client-side queries (p95)"
    )


def build_database_server(backends: Backends, runtime: ToolRuntime) -> MCPServer:
    server = MCPServer("database", instructions="Read-only PostgreSQL diagnostics for the shop DB.")
    db, prom = backends.database, backends.prometheus

    @tool(server, runtime, read_only=True, title="Get database health")
    async def get_database_health() -> DatabaseHealthResult:
        """Connections (by application and state, with oldest transaction and longest running
        query), utilisation vs max_connections, lock waits, cache hit ratio, deadlocks."""
        meta = (
            await db.fetch(
                "SELECT current_setting('server_version') AS version, "
                "current_setting('max_connections')::int AS max_conn, "
                "(SELECT blks_hit::float / NULLIF(blks_hit + blks_read, 0) FROM pg_stat_database "
                " WHERE datname = current_database()) AS hit_ratio, "
                "(SELECT deadlocks FROM pg_stat_database WHERE datname = current_database()) AS deadlocks"
            )
        )[0]
        rows = await db.fetch(
            "SELECT coalesce(nullif(application_name, ''), backend_type) AS application, "
            "coalesce(state, 'unknown') AS state, count(*) AS n, "
            "max(extract(epoch FROM now() - xact_start)) AS oldest_xact, "
            "max(extract(epoch FROM now() - query_start)) FILTER (WHERE state = 'active') AS longest, "
            "max(left(query, 200)) FILTER (WHERE state <> 'idle') AS sample, "
            "count(*) FILTER (WHERE wait_event_type = 'Lock') AS lock_waits "
            "FROM pg_stat_activity WHERE pid <> pg_backend_pid() "
            "GROUP BY 1, 2 ORDER BY n DESC"
        )
        sessions = [
            SessionGroup(
                application=r["application"],
                state=r["state"],
                count=r["n"],
                oldest_transaction_s=round(float(r["oldest_xact"]), 1)
                if r["oldest_xact"]
                else None,
                longest_query_s=round(float(r["longest"]), 2) if r["longest"] else None,
                sample_query=clip(r["sample"], 200),
            )
            for r in rows
        ]
        total = sum(s.count for s in sessions)
        idle_tx = sum(s.count for s in sessions if s.state.startswith("idle in transaction"))
        waits = sum(int(r["lock_waits"]) for r in rows)
        return DatabaseHealthResult(
            summary=f"{total}/{meta['max_conn']} connections, {idle_tx} idle in transaction, "
            f"{waits} waiting on locks",
            reachable=True,
            server_version=meta["version"],
            max_connections=meta["max_conn"],
            total_connections=total,
            connection_utilization=round(total / meta["max_conn"], 3),
            idle_in_transaction=idle_tx,
            waiting_on_locks=waits,
            cache_hit_ratio=round(meta["hit_ratio"], 4) if meta["hit_ratio"] is not None else None,
            deadlocks=int(meta["deadlocks"] or 0),
            sessions=sessions,
        )

    @tool(server, runtime, read_only=True, title="Get slow queries")
    async def get_slow_queries(
        top_k: Annotated[int, Field(ge=1, le=25)] = 10,
        order_by: Literal["mean_time", "total_time", "calls"] = "total_time",
        min_calls: Annotated[int, Field(ge=1, le=1_000_000)] = 1,
    ) -> SlowQueriesResult:
        """Top normalised statements from pg_stat_statements (cumulative since the last stats
        reset) with calls, mean/max/total execution time."""
        column = {"mean_time": "mean_exec_time", "total_time": "total_exec_time", "calls": "calls"}[
            order_by
        ]
        rows = await db.fetch(
            # `column` comes from a fixed map, never from user input.
            "SELECT queryid::text AS queryid, query, calls, mean_exec_time, max_exec_time, "  # noqa: S608
            "total_exec_time, rows FROM pg_stat_statements s "
            "JOIN pg_database d ON d.oid = s.dbid AND d.datname = current_database() "
            f"WHERE calls >= $1 AND query NOT ILIKE '%pg_stat%' ORDER BY {column} DESC LIMIT $2",
            min_calls,
            top_k,
        )
        since = await db.fetch("SELECT stats_reset FROM pg_stat_statements_info")
        queries = [
            SlowQuery(
                queryid=r["queryid"],
                query=clip(" ".join(r["query"].split()), 400) or "",
                calls=r["calls"],
                mean_ms=round(r["mean_exec_time"], 2),
                max_ms=round(r["max_exec_time"], 2),
                total_ms=round(r["total_exec_time"], 1),
                rows=r["rows"],
            )
            for r in rows
        ]
        top = f"; top: {queries[0].query[:80]!r} mean {queries[0].mean_ms} ms" if queries else ""
        return SlowQueriesResult(
            summary=f"{len(queries)} statements by {order_by}{top}",
            order_by=order_by,
            stats_since=iso(since[0]["stats_reset"]) if since and since[0]["stats_reset"] else None,
            queries=queries,
        )

    @tool(server, runtime, read_only=True, title="Get connection pool status")
    async def get_connection_pool_status(
        service: Annotated[AppService | None, Field(description="Only this service")] = None,
    ) -> PoolStatusResult:
        """Application connection pools (size, in use, idle, waiters, acquire timeouts, acquire
        latency) next to the sessions each service holds on the server."""
        sessions = await db.fetch(
            "SELECT application_name AS app, coalesce(state, 'unknown') AS state, count(*) AS n "
            "FROM pg_stat_activity WHERE datname = current_database() GROUP BY 1, 2"
        )
        pools = []
        for svc in [service] if service else list(APP_SERVICES):
            sel = f'service="{svc}"'
            vals = {
                "max": await prom.scalar(f"db_pool_max_connections{{{sel}}}"),
                "in_use": await prom.scalar(f"db_pool_connections_in_use{{{sel}}}"),
                "idle": await prom.scalar(f"db_pool_connections_idle{{{sel}}}"),
                "pending": await prom.scalar(f"db_pool_pending_acquires{{{sel}}}"),
                "timeouts": await prom.scalar(
                    f"sum(rate(db_pool_acquire_timeouts_total{{{sel}}}[1m]))"
                ),
                "p95": await prom.scalar(
                    f"histogram_quantile(0.95, sum by (le) (rate(db_pool_acquire_duration_seconds_bucket{{{sel}}}[1m])))"
                ),
            }
            pools.append(
                PoolStatus(
                    service=svc,
                    max_connections=vals["max"],
                    in_use=vals["in_use"],
                    idle=vals["idle"],
                    pending_acquires=vals["pending"],
                    utilization=round(vals["in_use"] / vals["max"], 3)
                    if vals["max"] and vals["in_use"] is not None
                    else None,
                    acquire_timeouts_per_s=vals["timeouts"],
                    acquire_p95_s=vals["p95"],
                    server_sessions={r["state"]: r["n"] for r in sessions if r["app"] == svc},
                )
            )
        hot = [
            f"{p.service} {p.in_use:.0f}/{p.max_connections:.0f}"
            for p in pools
            if p.in_use is not None and p.max_connections
        ]
        return PoolStatusResult(summary="pools in use: " + ", ".join(hot), pools=pools)

    @tool(server, runtime, read_only=True, title="Get database metrics")
    async def get_database_metrics(
        window_minutes: Annotated[int, Field(ge=1, le=60)] = 5,
        top_queries: Annotated[int, Field(ge=1, le=20)] = 8,
    ) -> DatabaseMetricsResult:
        """Database throughput and session metrics (postgres-exporter) plus the slowest
        client-side queries by p95 across services, over the last window."""
        w = f"[{window_minutes}m]"
        db_sel = 'datname="shop"'
        latency = await prom.instant(
            f"histogram_quantile(0.95, sum by (service, query, le) (rate(db_query_duration_seconds_bucket{w})))"
        )
        rates = await prom.instant(
            f"sum by (service, query) (rate(db_query_duration_seconds_count{w}))"
        )
        rate_map = {
            (r["metric"]["service"], r["metric"]["query"]): float(r["value"][1]) for r in rates
        }
        queries = [
            QueryLatency(
                service=r["metric"]["service"],
                query=r["metric"]["query"],
                p95_s=round(float(r["value"][1]), 4),
                calls_per_s=round(
                    rate_map.get((r["metric"]["service"], r["metric"]["query"]), 0.0), 3
                ),
            )
            for r in latency
            if r["value"][1] not in ("NaN", "+Inf")
        ]
        queries.sort(key=lambda q: q.p95_s, reverse=True)
        result = DatabaseMetricsResult(
            summary="",
            window_minutes=window_minutes,
            commits_per_s=await prom.scalar(
                f"sum(rate(pg_stat_database_xact_commit{{{db_sel}}}{w}))"
            ),
            rollbacks_per_s=await prom.scalar(
                f"sum(rate(pg_stat_database_xact_rollback{{{db_sel}}}{w}))"
            ),
            rows_fetched_per_s=await prom.scalar(
                f"sum(rate(pg_stat_database_tup_fetched{{{db_sel}}}{w}))"
            ),
            active_sessions=await prom.scalar(
                f'sum(pg_stat_activity_count{{{db_sel},state="active"}})'
            ),
            idle_in_transaction=await prom.scalar(
                f'sum(pg_stat_activity_count{{{db_sel},state="idle in transaction"}})'
            ),
            client_query_latency=queries[:top_queries],
        )
        slowest = (
            f"; slowest {queries[0].service}/{queries[0].query} p95 {queries[0].p95_s}s"
            if queries
            else ""
        )
        result.summary = f"{result.commits_per_s or 0:.1f} commits/s, {result.active_sessions or 0:.0f} active{slowest}"
        return result

    return server
