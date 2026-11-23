"""logs MCP server: search and aggregate structured logs in Loki (read-only)."""

# No `from __future__ import annotations` in tool modules: the MCP SDK reads the real type
# objects from the signatures to build input/output schemas.

import json
import re
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, Field

from ai_sre.mcp_servers.backends import Backends
from ai_sre.mcp_servers.core import (
    ObservedService,
    ToolFailure,
    ToolResult,
    ToolRuntime,
    clip,
    iso,
    tool,
    window,
)

Level = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
SinceMinutes = Annotated[int, Field(ge=1, le=1440, description="Look back this many minutes")]
_ENV = 'environment="simulated-prod"'
_TEMPLATE_RULES = (
    (re.compile(r"\b[0-9a-f]{12,}\b"), "<hex>"),
    (re.compile(r"\b\d+(\.\d+)?\b"), "<n>"),
    (re.compile(r"'[^']*'"), "'<s>'"),
)


class LogEntry(BaseModel):
    timestamp: str
    service: str
    severity: str | None = None
    message: str
    endpoint: str | None = None
    version: str | None = None
    trace_id: str | None = None
    request_id: str | None = None
    error_type: str | None = None
    error_message: str | None = None


class SearchLogsResult(ToolResult):
    query: str = Field(description="LogQL that was executed")
    window_start: str
    window_end: str
    count: int
    entries: list[LogEntry]
    next_before: str | None = Field(
        default=None, description="Pass as `before` to fetch the next (older) page"
    )


class ErrorSignature(BaseModel):
    service: str
    template: str = Field(description="Message with numbers/ids normalised")
    error_type: str | None
    count: int
    first_seen: str
    last_seen: str
    example: str


class AggregateErrorsResult(ToolResult):
    window_start: str
    window_end: str
    total_errors: int = Field(description="Exact count of ERROR/CRITICAL lines in the window")
    analysed: int = Field(description="Lines read to build signatures (capped)")
    by_service: dict[str, int]
    signatures: list[ErrorSignature]


class TraceLogsResult(ToolResult):
    trace_id: str
    services: list[str]
    count: int
    entries: list[LogEntry]


def template(message: str) -> str:
    for pattern, repl in _TEMPLATE_RULES:
        message = pattern.sub(repl, message)
    return message[:200]


def build_query(service: str | None, level: str | None, contains: str | None) -> str:
    selector = [_ENV]
    if service:
        selector.append(f'service="{service}"')
    if level:
        selector.append(f'level=~"{level}"')
    query = "{" + ", ".join(selector) + "}"
    if contains:
        if "`" in contains:
            raise ToolFailure("invalid_argument", "`contains` must not include backticks")
        query += f" |= `{contains}`"
    return query


def parse_line(ts_ns: int, line: str, labels: dict[str, str]) -> LogEntry:
    timestamp = iso(datetime.fromtimestamp(ts_ns / 1e9, UTC))
    try:
        data: dict[str, Any] = json.loads(line)
    except ValueError:
        return LogEntry(
            timestamp=timestamp, service=labels.get("service", "?"), message=clip(line, 500) or ""
        )
    raw_error = data.get("error")
    error: dict[str, Any] = raw_error if isinstance(raw_error, dict) else {}
    return LogEntry(
        timestamp=data.get("timestamp", timestamp),
        service=data.get("service") or labels.get("service", "?"),
        severity=data.get("severity"),
        message=clip(str(data.get("message", "")), 500) or "",
        endpoint=data.get("endpoint"),
        version=data.get("version"),
        trace_id=data.get("trace_id"),
        request_id=data.get("request_id"),
        error_type=error.get("type"),
        error_message=clip(error.get("message"), 300),
    )


def build_logs_server(backends: Backends, runtime: ToolRuntime) -> MCPServer:
    server = MCPServer(
        "logs",
        instructions="Read-only access to structured service logs (Loki). Times are UTC.",
    )
    loki = backends.loki

    @tool(server, runtime, read_only=True, title="Search logs")
    async def search_logs(
        service: Annotated[ObservedService | None, Field(description="Only this service")] = None,
        contains: Annotated[
            str | None, Field(max_length=200, description="Case-sensitive substring filter")
        ] = None,
        level: Annotated[Level | None, Field(description="Exact severity")] = None,
        since_minutes: SinceMinutes = 15,
        before: Annotated[
            datetime | None,
            Field(description="Page cursor: only lines older than this (ISO-8601, UTC)"),
        ] = None,
        limit: Annotated[int, Field(ge=1, le=200)] = 50,
    ) -> SearchLogsResult:
        """Search log lines (newest first) by service, severity and substring within a time
        window. Paginate with `before` = `next_before` of the previous page."""
        start, end = window(since_minutes, before)
        query = build_query(service, level, contains)
        rows = await loki.query_range(query, start, end, limit)
        entries = [parse_line(*row) for row in rows]
        next_before = (
            iso(datetime.fromtimestamp((rows[-1][0] - 1) / 1e9, UTC))
            if len(rows) == limit
            else None
        )
        return SearchLogsResult(
            summary=f"{len(entries)} log lines for {query} in the last {since_minutes} min",
            query=query,
            window_start=iso(start),
            window_end=iso(end),
            count=len(entries),
            entries=entries,
            next_before=next_before,
        )

    @tool(server, runtime, read_only=True, title="Get error logs")
    async def get_error_logs(
        service: Annotated[ObservedService | None, Field(description="Only this service")] = None,
        since_minutes: SinceMinutes = 15,
        limit: Annotated[int, Field(ge=1, le=100)] = 25,
    ) -> SearchLogsResult:
        """Most recent ERROR and CRITICAL log lines, with error type and message."""
        start, end = window(since_minutes)
        query = build_query(service, "ERROR|CRITICAL", None)
        rows = await loki.query_range(query, start, end, limit)
        entries = [parse_line(*row) for row in rows]
        return SearchLogsResult(
            summary=f"{len(entries)} error lines in the last {since_minutes} min"
            + (f" for {service}" if service else ""),
            query=query,
            window_start=iso(start),
            window_end=iso(end),
            count=len(entries),
            entries=entries,
        )

    @tool(server, runtime, read_only=True, title="Aggregate errors")
    async def aggregate_errors(
        service: Annotated[ObservedService | None, Field(description="Only this service")] = None,
        since_minutes: SinceMinutes = 30,
        top_k: Annotated[int, Field(ge=1, le=25)] = 10,
    ) -> AggregateErrorsResult:
        """Group error lines into signatures (message templates with numbers and ids
        normalised) with counts and first/last seen. Use this before reading raw lines."""
        start, end = window(since_minutes)
        selector = build_query(service, "ERROR|CRITICAL", None)
        counts = await loki.instant_vector(
            f"sum by (service) (count_over_time({selector} [{since_minutes}m]))", end
        )
        by_service = {r["metric"].get("service", "?"): int(float(r["value"][1])) for r in counts}
        rows = await loki.query_range(selector, start, end, 2000)
        groups: dict[tuple[str, str, str | None], list[LogEntry]] = {}
        for row in rows:
            entry = parse_line(*row)
            key = (entry.service, template(entry.message), entry.error_type)
            groups.setdefault(key, []).append(entry)
        ranked = sorted(groups.items(), key=lambda kv: len(kv[1]), reverse=True)[:top_k]
        signatures = [
            ErrorSignature(
                service=svc,
                template=tmpl,
                error_type=etype,
                count=len(items),
                first_seen=min(i.timestamp for i in items),
                last_seen=max(i.timestamp for i in items),
                example=items[0].error_message or items[0].message,
            )
            for (svc, tmpl, etype), items in ranked
        ]
        total = sum(by_service.values())
        top = f"; top: {signatures[0].template!r} x{signatures[0].count}" if signatures else ""
        return AggregateErrorsResult(
            summary=f"{total} errors in {since_minutes} min across {len(by_service)} services{top}",
            window_start=iso(start),
            window_end=iso(end),
            total_errors=total,
            analysed=len(rows),
            by_service=by_service,
            signatures=signatures,
            truncated=len(rows) < total,
        )

    @tool(server, runtime, read_only=True, title="Get logs by trace id")
    async def get_logs_by_trace_id(
        trace_id: Annotated[
            str, Field(pattern=r"^[0-9a-f]{32}$", description="W3C trace id (32 hex chars)")
        ],
        since_minutes: Annotated[int, Field(ge=1, le=1440)] = 60,
        limit: Annotated[int, Field(ge=1, le=200)] = 100,
    ) -> TraceLogsResult:
        """All log lines of one request across services (gateway, orders, worker), oldest first."""
        start, end = window(since_minutes)
        rows = await loki.query_range(f"{{{_ENV}}} |= `{trace_id}`", start, end, limit, "forward")
        entries = [parse_line(*row) for row in rows]
        services = sorted({e.service for e in entries})
        return TraceLogsResult(
            summary=f"{len(entries)} lines for trace {trace_id} across {', '.join(services) or 'no services'}",
            trace_id=trace_id,
            services=services,
            count=len(entries),
            entries=entries,
        )

    return server
