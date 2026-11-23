"""metrics MCP server: time series, golden signals, window comparison, anomaly detection."""

import statistics
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, Field

from ai_sre.mcp_servers.backends import Backends, Prometheus
from ai_sre.mcp_servers.core import (
    APP_SERVICES,
    HTTP_SERVICES,
    AppService,
    ToolFailure,
    ToolResult,
    ToolRuntime,
    iso,
    step_for,
    tool,
    utcnow,
    window,
)

Signal = Literal[
    "request_rate",
    "error_rate",
    "latency_p50",
    "latency_p95",
    "latency_p99",
    "cpu",
    "memory_bytes",
    "db_pool_utilization",
    "db_pool_pending",
    "db_query_p95",
    "redis_error_rate",
    "upstream_error_rate",
    "queue_depth",
    "worker_failure_rate",
]

_R = "[1m]"
SIGNALS: dict[str, tuple[str, str]] = {
    # name: (PromQL template with {s} = service, unit)
    "request_rate": (f'sum(rate(http_server_requests_total{{service="{{s}}"}}{_R}))', "req/s"),
    "error_rate": (
        f'sum(rate(http_server_requests_total{{service="{{s}}",status=~"5.."}}{_R}))'
        f' / clamp_min(sum(rate(http_server_requests_total{{service="{{s}}"}}{_R})), 1e-9)',
        "ratio",
    ),
    "latency_p50": (
        f'histogram_quantile(0.5, sum by (le) (rate(http_server_request_duration_seconds_bucket{{service="{{s}}"}}{_R})))',
        "s",
    ),
    "latency_p95": (
        f'histogram_quantile(0.95, sum by (le) (rate(http_server_request_duration_seconds_bucket{{service="{{s}}"}}{_R})))',
        "s",
    ),
    "latency_p99": (
        f'histogram_quantile(0.99, sum by (le) (rate(http_server_request_duration_seconds_bucket{{service="{{s}}"}}{_R})))',
        "s",
    ),
    "cpu": (f'rate(process_cpu_seconds_total{{service="{{s}}"}}{_R})', "cores"),
    "memory_bytes": ('process_resident_memory_bytes{service="{s}"}', "bytes"),
    "db_pool_utilization": (
        'db_pool_connections_in_use{service="{s}"} / db_pool_max_connections{service="{s}"}',
        "ratio",
    ),
    "db_pool_pending": ('db_pool_pending_acquires{service="{s}"}', "callers"),
    "db_query_p95": (
        f'histogram_quantile(0.95, sum by (le) (rate(db_query_duration_seconds_bucket{{service="{{s}}"}}{_R})))',
        "s",
    ),
    "redis_error_rate": (
        f'sum(rate(redis_command_errors_total{{service="{{s}}"}}{_R}))',
        "errors/s",
    ),
    "upstream_error_rate": (
        f'sum(rate(http_client_requests_total{{service="{{s}}",status=~"5..|error"}}{_R}))',
        "errors/s",
    ),
    "queue_depth": ('sum(queue_depth{service="{s}",queue="fulfilment"})', "jobs"),
    "worker_failure_rate": (
        f'sum(rate(worker_job_failures_total{{service="{{s}}"}}{_R}))',
        "failures/s",
    ),
}
_HTTP_ONLY = {
    "request_rate",
    "error_rate",
    "latency_p50",
    "latency_p95",
    "latency_p99",
    "upstream_error_rate",
}
_WORKER_ONLY = {"queue_depth", "worker_failure_rate"}
ANOMALY_SIGNALS = (
    "request_rate",
    "error_rate",
    "latency_p99",
    "cpu",
    "memory_bytes",
    "db_pool_utilization",
    "queue_depth",
    "worker_failure_rate",
    "redis_error_rate",
)


def signal_query(signal: str, service: str) -> str:
    if signal in _HTTP_ONLY and service not in HTTP_SERVICES:
        raise ToolFailure(
            "invalid_argument",
            f"{signal} is not available for {service}",
            f"HTTP signals exist for {', '.join(HTTP_SERVICES)}",
        )
    if signal in _WORKER_ONLY and service != "fulfilment-worker":
        raise ToolFailure("invalid_argument", f"{signal} is only available for fulfilment-worker")
    return SIGNALS[signal][0].replace("{s}", service)


def applicable(signal: str, service: str) -> bool:
    return not (
        (signal in _HTTP_ONLY and service not in HTTP_SERVICES)
        or (signal in _WORKER_ONLY and service != "fulfilment-worker")
    )


class Stats(BaseModel):
    min: float | None
    max: float | None
    avg: float | None
    last: float | None
    points: int


def stats(values: list[float]) -> Stats:
    if not values:
        return Stats(min=None, max=None, avg=None, last=None, points=0)
    return Stats(
        min=round(min(values), 6),
        max=round(max(values), 6),
        avg=round(statistics.fmean(values), 6),
        last=round(values[-1], 6),
        points=len(values),
    )


def _values(series: dict[str, Any]) -> list[tuple[float, float]]:
    out = []
    for ts, raw in series["values"]:
        value = float(raw)
        if value == value and value not in (float("inf"), float("-inf")):
            out.append((float(ts), value))
    return out


class Series(BaseModel):
    labels: dict[str, str]
    stats: Stats
    points: list[tuple[str, float]] = Field(description="[timestamp, value], downsampled")


class TimeseriesResult(ToolResult):
    promql: str
    window_start: str
    window_end: str
    step_seconds: int
    series_total: int
    series: list[Series]


class SignalValue(BaseModel):
    signal: str
    unit: str
    value: float | None


class ServiceMetricsResult(ToolResult):
    service: str
    at: str
    version: str | None
    signals: list[SignalValue]


class WindowStats(BaseModel):
    start: str
    end: str
    stats: Stats


class CompareResult(ToolResult):
    service: str
    signal: str | None
    promql: str
    current: WindowStats
    baseline: WindowStats
    change_pct: float | None = Field(description="(current avg - baseline avg) / baseline avg")
    direction: Literal["up", "down", "flat", "unknown"]


class Anomaly(BaseModel):
    service: str
    signal: str
    unit: str
    baseline_avg: float
    recent_avg: float
    recent_max: float
    change_pct: float | None
    z_score: float | None
    onset: str | None = Field(description="First point in the recent window beyond the threshold")


class AnomaliesResult(ToolResult):
    window_start: str
    baseline_start: str
    checked: int
    anomalies: list[Anomaly]


async def _range(
    prom: Prometheus, expr: str, start: datetime, end: datetime, step: int
) -> list[dict[str, Any]]:
    return await prom.range(expr, start, end, step)


def _anomaly(
    service: str,
    signal: str,
    base: list[float],
    recent: list[tuple[float, float]],
    z_threshold: float,
) -> Anomaly | None:
    if len(base) < 3 or not recent:
        return None
    # Robust baseline (median / MAD): earlier incidents inside the baseline window must not
    # inflate it the way a mean / standard deviation would.
    mean = statistics.median(base)
    std = 1.4826 * statistics.median(abs(v - mean) for v in base)
    values = [v for _, v in recent]
    recent_avg = statistics.fmean(values)
    # Absolute floor so tiny noisy signals (e.g. 0.001 errors/s) are not flagged.
    floor = {
        "error_rate": 0.02,
        "db_pool_utilization": 0.2,
        "cpu": 0.05,
        "queue_depth": 5,
        "memory_bytes": 20e6,
        "request_rate": 1.0,
    }.get(signal, 0.05)
    threshold = max(z_threshold * std, abs(mean) * 0.5, floor)
    if abs(recent_avg - mean) <= threshold:
        return None
    # Onset of the *current* episode: start of the last contiguous run of anomalous points.
    # An earlier, already-resolved episode in the window is not the onset.
    flags = [abs(v - mean) > threshold for _, v in recent]
    onset = None
    if any(flags):
        last = max(i for i, f in enumerate(flags) if f)
        start = last
        while start > 0 and flags[start - 1]:
            start -= 1
        onset = recent[start][0]
    return Anomaly(
        service=service,
        signal=signal,
        unit=SIGNALS[signal][1],
        baseline_avg=round(mean, 6),
        recent_avg=round(recent_avg, 6),
        recent_max=round(max(values), 6),
        change_pct=round((recent_avg - mean) / mean * 100, 1) if mean else None,
        z_score=round((recent_avg - mean) / std, 2) if std else None,
        onset=iso(datetime.fromtimestamp(onset, UTC)) if onset else None,
    )


def build_metrics_server(backends: Backends, runtime: ToolRuntime) -> MCPServer:
    server = MCPServer("metrics", instructions="Read-only Prometheus metrics. Times are UTC.")
    prom = backends.prometheus

    @tool(server, runtime, read_only=True, title="Get metric time series")
    async def get_metric_timeseries(
        promql: Annotated[
            str, Field(min_length=1, max_length=600, description="PromQL expression")
        ],
        since_minutes: Annotated[int, Field(ge=1, le=1440)] = 30,
        max_points: Annotated[int, Field(ge=5, le=120)] = 30,
        max_series: Annotated[int, Field(ge=1, le=20)] = 10,
    ) -> TimeseriesResult:
        """Run a PromQL range query. Each series comes back with summary statistics and at
        most `max_points` points; at most `max_series` series are returned."""
        start, end = window(since_minutes)
        step = step_for(start, end, max_points)
        raw = await prom.range(promql, start, end, step)
        series = []
        for s in raw[:max_series]:
            pts = _values(s)
            series.append(
                Series(
                    labels={
                        k: v for k, v in s["metric"].items() if k not in ("environment", "instance")
                    },
                    stats=stats([v for _, v in pts]),
                    points=[(iso(datetime.fromtimestamp(t, UTC)), round(v, 6)) for t, v in pts],
                )
            )
        return TimeseriesResult(
            summary=f"{len(raw)} series over {since_minutes} min (step {step}s)",
            promql=promql,
            window_start=iso(start),
            window_end=iso(end),
            step_seconds=step,
            series_total=len(raw),
            series=series,
            truncated=len(raw) > max_series,
        )

    @tool(server, runtime, read_only=True, title="Get service metrics")
    async def get_service_metrics(service: AppService) -> ServiceMetricsResult:
        """Current golden signals for one service: traffic, errors, latency percentiles,
        saturation (CPU, memory, DB pool, queue) and the running version (1-minute rates)."""
        values = []
        for name, (_, unit) in SIGNALS.items():
            if applicable(name, service):
                value = await prom.scalar(signal_query(name, service))
                values.append(
                    SignalValue(
                        signal=name, unit=unit, value=None if value is None else round(value, 6)
                    )
                )
        build = await prom.instant(f'app_build_info{{service="{service}"}} == 1')
        version = build[0]["metric"].get("version") if build else None
        brief = ", ".join(
            f"{v.signal}={v.value:.3g}"
            for v in values
            if v.value is not None
            and v.signal in ("request_rate", "error_rate", "latency_p99", "db_pool_utilization")
        )
        return ServiceMetricsResult(
            summary=f"{service} {version or '?'}: {brief}",
            service=service,
            at=iso(utcnow()),
            version=version,
            signals=values,
        )

    @tool(server, runtime, read_only=True, title="Compare metric windows")
    async def compare_metric_windows(
        service: AppService,
        signal: Annotated[Signal | None, Field(description="Named signal (or give promql)")] = None,
        promql: Annotated[
            str | None, Field(max_length=600, description="Custom PromQL instead of signal")
        ] = None,
        window_minutes: Annotated[int, Field(ge=1, le=240)] = 10,
        baseline_offset_minutes: Annotated[
            int, Field(ge=1, le=10080, description="Baseline = same-length window this long ago")
        ] = 60,
        split_at: Annotated[
            datetime | None,
            Field(
                description="Compare after vs before this moment (e.g. a deployment); overrides the windows"
            ),
        ] = None,
    ) -> CompareResult:
        """Compare a signal in the most recent window with an earlier baseline window of the
        same length (e.g. now vs one hour ago), or after vs before `split_at` (e.g. a deploy)."""
        if not (signal or promql):
            raise ToolFailure("invalid_argument", "give either `signal` or `promql`")
        expr = promql or signal_query(signal or "", service)
        end = utcnow()
        if split_at is not None:
            split = split_at if split_at.tzinfo else split_at.replace(tzinfo=UTC)
            if not end - timedelta(days=7) < split < end:
                raise ToolFailure(
                    "invalid_argument", "split_at must be within the last 7 days and in the past"
                )
            length = max(end - split, timedelta(minutes=2))
            start, b_end = split, split
            b_start = split - length
        else:
            start = end - timedelta(minutes=window_minutes)
            b_end = end - timedelta(minutes=baseline_offset_minutes)
            b_start = b_end - timedelta(minutes=window_minutes)
        step = step_for(b_start, end, 40)
        current = await prom.range(expr, start, end, step)
        baseline = await prom.range(expr, b_start, b_end, step)
        cur = stats([v for _, v in _values(current[0])] if current else [])
        base = stats([v for _, v in _values(baseline[0])] if baseline else [])
        change = None
        direction: Literal["up", "down", "flat", "unknown"] = "unknown"
        if cur.avg is not None and base.avg is not None:
            change = round((cur.avg - base.avg) / base.avg * 100, 1) if base.avg else None
            delta = cur.avg - base.avg
            tolerance = max(abs(base.avg) * 0.1, 1e-9)
            direction = "flat" if abs(delta) <= tolerance else ("up" if delta > 0 else "down")
        return CompareResult(
            summary=f"{signal or 'promql'} on {service}: baseline avg {base.avg} -> current avg {cur.avg} ({direction})",
            service=service,
            signal=signal,
            promql=expr,
            current=WindowStats(start=iso(start), end=iso(end), stats=cur),
            baseline=WindowStats(start=iso(b_start), end=iso(b_end), stats=base),
            change_pct=change,
            direction=direction,
        )

    @tool(server, runtime, read_only=True, title="Detect metric anomalies", timeout_s=40)
    async def detect_metric_anomalies(
        services: Annotated[
            list[AppService] | None, Field(description="Services to check (default: all)")
        ] = None,
        since_minutes: Annotated[int, Field(ge=1, le=120, description="Recent window")] = 10,
        baseline_minutes: Annotated[int, Field(ge=5, le=720, description="Window before it")] = 30,
        z_threshold: Annotated[float, Field(ge=1, le=10)] = 3.0,
    ) -> AnomaliesResult:
        """Flag golden signals whose recent average departs from the preceding baseline window
        (z-score and relative change, with absolute floors), with the onset time."""
        end = utcnow()
        recent_start = end - timedelta(minutes=since_minutes)
        base_start = recent_start - timedelta(minutes=baseline_minutes)
        step = step_for(base_start, end, 120)
        anomalies, checked = [], 0
        for service in services or APP_SERVICES:
            for name in ANOMALY_SIGNALS:
                if not applicable(name, service):
                    continue
                checked += 1
                rows = await prom.range(signal_query(name, service), base_start, end, step)
                if not rows:
                    continue
                pts = _values(rows[0])
                base = [v for t, v in pts if t < recent_start.timestamp()]
                recent = [(t, v) for t, v in pts if t >= recent_start.timestamp()]
                found = _anomaly(service, name, base, recent, z_threshold)
                if found:
                    anomalies.append(found)
        anomalies.sort(
            key=lambda a: abs(a.z_score or 0) + abs(a.change_pct or 0) / 100, reverse=True
        )
        listing = "; ".join(f"{a.service} {a.signal} {a.change_pct}%" for a in anomalies[:5])
        return AnomaliesResult(
            summary=f"{len(anomalies)} anomalies in {checked} signals"
            + (f": {listing}" if listing else ""),
            window_start=iso(recent_start),
            baseline_start=iso(base_start),
            checked=checked,
            anomalies=anomalies,
        )

    return server
