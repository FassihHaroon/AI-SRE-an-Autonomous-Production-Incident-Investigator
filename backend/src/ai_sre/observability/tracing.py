"""Structured tracing of investigations with OpenTelemetry.

Every investigation produces a tree of spans:

    investigation.run                      (root; one per run segment: start, resume)
      node.<name>                          (one per LangGraph node execution, incl. retries)
        tool.<server>.<tool>               (one per MCP tool call, incl. retry attempts)
        llm.generate                       (one per LLM request: model, tokens, latency, cost)

Spans go through the normal OpenTelemetry SDK, so they can also be exported (console or OTLP,
see ``setup_tracing``). An in-process :class:`SpanCollector` keeps the finished spans of each
investigation so the API can persist them and the UI can show a trace view, and the evaluation
harness can compute latency/token/cost metrics without an external collector.

Privacy: span attributes never contain tool results, prompts or model output. User text and tool
arguments pass through :func:`redact_text` / :func:`sanitize_args` (credentials, tokens, emails,
card numbers) and are truncated.
"""

from __future__ import annotations

import os
import re
import threading
from collections import OrderedDict
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

import structlog
from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, Span, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor
from opentelemetry.trace import Status, StatusCode

logger = structlog.get_logger("observability.tracing")

INV_ATTR = "ai_sre.investigation.id"
KIND_ATTR = "ai_sre.span.kind"  # investigation | node | tool | llm

_current_investigation: ContextVar[str | None] = ContextVar("ai_sre_investigation", default=None)

# ---------------------------------------------------------------------------- redaction

_SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"eyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]{8,}"), "[REDACTED_JWT]"),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{20,}"), "[REDACTED_KEY]"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"), "[REDACTED_KEY]"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}"), "Bearer [REDACTED]"),
    (
        re.compile(r"(?i)\b(api[_-]?key|token|secret|password|passwd)\b\s*[=:]\s*\S+"),
        r"\1=[REDACTED]",
    ),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[REDACTED_EMAIL]"),
    (re.compile(r"\b(?:\d[ -]?){13,16}\b"), "[REDACTED_CARD]"),
]
_SENSITIVE_KEY = re.compile(r"(?i)token|secret|password|passwd|authorization|api[_-]?key|cookie")


def redact_text(text: str, limit: int = 300) -> str:
    """Remove credentials and personal data from free text, then truncate."""
    for pattern, repl in _SECRET_PATTERNS:
        text = pattern.sub(repl, text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def sanitize_args(value: Any, depth: int = 0) -> Any:
    """Tool arguments safe to record: sensitive keys dropped, strings redacted, sizes bounded."""
    if depth > 3:
        return "…"
    if isinstance(value, Mapping):
        return {
            str(k): "[REDACTED]" if _SENSITIVE_KEY.search(str(k)) else sanitize_args(v, depth + 1)
            for k, v in list(value.items())[:20]
        }
    if isinstance(value, list | tuple):
        return [sanitize_args(v, depth + 1) for v in list(value)[:10]]
    if isinstance(value, str):
        return redact_text(value, 200)
    if value is None or isinstance(value, bool | int | float):
        return value
    return redact_text(str(value), 200)


# ---------------------------------------------------------------------------- cost

# USD per 1M tokens (input, output). Estimates from public list prices for each model family;
# the Gemini free tier is billed at $0. Override with AI_SRE_LLM_PRICE_INPUT_PER_MTOK /
# AI_SRE_LLM_PRICE_OUTPUT_PER_MTOK.
PRICES_PER_MTOK: list[tuple[str, float, float]] = [
    ("scripted", 0.0, 0.0),
    ("flash-lite", 0.10, 0.40),
    ("flash", 0.30, 2.50),
    ("pro", 1.25, 10.0),
]


def estimate_cost_usd(model: str | None, input_tokens: int, output_tokens: int) -> float:
    env_in = os.environ.get("AI_SRE_LLM_PRICE_INPUT_PER_MTOK")
    env_out = os.environ.get("AI_SRE_LLM_PRICE_OUTPUT_PER_MTOK")
    if env_in is not None and env_out is not None:
        price_in, price_out = float(env_in), float(env_out)
    else:
        name = (model or "").lower()
        price_in, price_out = next(
            ((i, o) for key, i, o in PRICES_PER_MTOK if key in name), (0.30, 2.50)
        )
    return round((input_tokens * price_in + output_tokens * price_out) / 1_000_000, 8)


# ---------------------------------------------------------------------------- collection


def _iso(ns: int | None) -> str | None:
    return datetime.fromtimestamp(ns / 1e9, UTC).isoformat() if ns else None


def span_record(span: ReadableSpan) -> dict[str, Any]:
    attrs = dict(span.attributes or {})
    ctx = span.get_span_context()
    duration = ((span.end_time or 0) - (span.start_time or 0)) / 1e6 if span.end_time else None
    return {
        "span_id": f"{ctx.span_id:016x}" if ctx else None,
        "trace_id": f"{ctx.trace_id:032x}" if ctx else None,
        "parent_id": f"{span.parent.span_id:016x}" if span.parent else None,
        "name": span.name,
        "kind": attrs.pop(KIND_ATTR, None),
        "investigation_id": attrs.pop(INV_ATTR, None),
        "start": _iso(span.start_time),
        "end": _iso(span.end_time),
        "duration_ms": round(duration, 2) if duration is not None else None,
        "status": "error" if span.status.status_code == StatusCode.ERROR else "ok",
        "status_message": span.status.description,
        "attributes": {k: list(v) if isinstance(v, tuple) else v for k, v in attrs.items()},
    }


class SpanCollector(SpanProcessor):
    """Keeps finished spans per investigation (bounded) for persistence and evaluation."""

    def __init__(self, max_investigations: int = 500) -> None:
        self._spans: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
        self._lock = threading.Lock()
        self._max = max_investigations

    def on_start(self, span: Span, parent_context: Context | None = None) -> None:
        return None

    def on_end(self, span: ReadableSpan) -> None:
        inv = (span.attributes or {}).get(INV_ATTR)
        if not inv:
            return
        record = span_record(span)
        with self._lock:
            self._spans.setdefault(str(inv), []).append(record)
            self._spans.move_to_end(str(inv))
            while len(self._spans) > self._max:
                self._spans.popitem(last=False)

    def drain(self, investigation_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return self._spans.pop(investigation_id, [])

    def peek(self, investigation_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._spans.get(investigation_id, []))

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


_provider: TracerProvider | None = None
_collector = SpanCollector()
_setup_lock = threading.Lock()


def setup_tracing(exporter: str | None = None, service_name: str = "ai-sre") -> SpanCollector:
    """Configure the tracer provider once. ``exporter``: none (default) | console | otlp.

    ``otlp`` needs ``opentelemetry-exporter-otlp-proto-http`` installed and the standard
    ``OTEL_EXPORTER_OTLP_ENDPOINT`` variable; without them it logs a warning and keeps the
    in-process collector only.
    """
    global _provider
    with _setup_lock:
        if _provider is not None:
            return _collector
        exporter = (exporter or os.environ.get("AI_SRE_OTEL_EXPORTER", "none")).lower()
        provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
        provider.add_span_processor(_collector)
        if exporter == "console":
            provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
        elif exporter == "otlp":
            try:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # type: ignore[import-not-found]
                    OTLPSpanExporter,
                )
                from opentelemetry.sdk.trace.export import BatchSpanProcessor

                provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
            except ImportError:
                logger.warning(
                    "otlp_exporter_missing",
                    hint="pip install opentelemetry-exporter-otlp-proto-http",
                )
        _provider = provider
        return _collector


def collector() -> SpanCollector:
    setup_tracing()
    return _collector


def tracer() -> trace.Tracer:
    setup_tracing()
    if _provider is None:  # pragma: no cover - setup_tracing always sets it
        raise RuntimeError("tracing not configured")
    return _provider.get_tracer("ai_sre")


def current_investigation() -> str | None:
    return _current_investigation.get()


# ---------------------------------------------------------------------------- span helpers


@contextmanager
def investigation_span(
    investigation_id: str, question: str, *, reasoner: str, phase: str = "run"
) -> Iterator[trace.Span]:
    token = _current_investigation.set(investigation_id)
    try:
        with tracer().start_as_current_span(
            f"investigation.{phase}",
            attributes={
                KIND_ATTR: "investigation",
                INV_ATTR: investigation_id,
                "ai_sre.query": redact_text(question),
                "ai_sre.reasoner": reasoner,
                "ai_sre.phase": phase,
            },
        ) as span:
            yield span
    finally:
        _current_investigation.reset(token)


@contextmanager
def node_span(node: str, investigation_id: str | None = None) -> Iterator[trace.Span]:
    inv = investigation_id or current_investigation()
    attrs: dict[str, Any] = {KIND_ATTR: "node", "ai_sre.node": node}
    if inv:
        attrs[INV_ATTR] = inv
    # GraphInterrupt (pause for approval) propagates through this span and is not an error.
    with tracer().start_as_current_span(
        f"node.{node}", attributes=attrs, record_exception=False, set_status_on_exception=False
    ) as span:
        yield span


@contextmanager
def tool_span(
    server: str, tool: str, arguments: Mapping[str, Any], round_: int
) -> Iterator[trace.Span]:
    attrs: dict[str, Any] = {
        KIND_ATTR: "tool",
        "ai_sre.tool.server": server,
        "ai_sre.tool.name": tool,
        "ai_sre.tool.key": f"{server}.{tool}",
        "ai_sre.round": round_,
        "ai_sre.tool.arguments": _json(sanitize_args(arguments)),
    }
    inv = current_investigation()
    if inv:
        attrs[INV_ATTR] = inv
    with tracer().start_as_current_span(f"tool.{server}.{tool}", attributes=attrs) as span:
        yield span


def finish_tool_span(
    span: trace.Span,
    *,
    evidence_id: str,
    ok: bool,
    attempts: int,
    error_code: str | None = None,
    result_bytes: int | None = None,
) -> None:
    span.set_attribute("ai_sre.evidence.id", evidence_id)
    span.set_attribute("ai_sre.tool.ok", ok)
    span.set_attribute("ai_sre.tool.attempts", attempts)
    span.set_attribute("ai_sre.tool.retries", max(0, attempts - 1))
    if result_bytes is not None:
        span.set_attribute("ai_sre.tool.result_bytes", result_bytes)
    if not ok:
        span.set_attribute("ai_sre.tool.error_code", error_code or "error")
        span.set_status(Status(StatusCode.ERROR, error_code or "tool error"))


@contextmanager
def llm_span(node: str, provider: str, model: str | None) -> Iterator[trace.Span]:
    # Attribute names follow the OpenTelemetry GenAI semantic conventions where they exist.
    attrs: dict[str, Any] = {
        KIND_ATTR: "llm",
        "gen_ai.system": provider,
        "gen_ai.request.model": model or "unknown",
        "ai_sre.node": node,
    }
    inv = current_investigation()
    if inv:
        attrs[INV_ATTR] = inv
    with tracer().start_as_current_span("llm.generate", attributes=attrs) as span:
        yield span


def _json(value: Any) -> str:
    import json

    return json.dumps(value, default=str, separators=(",", ":"))[:1000]


# ---------------------------------------------------------------------------- summary


def summarize(spans: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate a trace into the per-investigation numbers shown in the UI and used by evals."""
    nodes: dict[str, dict[str, Any]] = {}
    tools: dict[str, dict[str, Any]] = {}
    llm_calls = llm_errors = llm_retries = tokens_in = tokens_out = 0
    llm_ms = llm_cost = 0.0
    models: list[str] = []
    tool_calls = tool_errors = tool_retries = 0
    tool_ms = 0.0
    rounds = 0
    run_ms = 0.0
    result: dict[str, Any] = {}
    prefix = "ai_sre.result."
    for s in spans:
        a = s.get("attributes", {})
        dur = float(s.get("duration_ms") or 0.0)
        failed = s.get("status") == "error"
        kind = s.get("kind")
        if kind == "investigation":
            run_ms += dur
            result.update({k[len(prefix) :]: v for k, v in a.items() if k.startswith(prefix)})
        elif kind == "node":
            name = str(a.get("ai_sre.node", s["name"]))
            n = nodes.setdefault(
                name, {"executions": 0, "total_ms": 0.0, "errors": 0, "retries": 0}
            )
            n["executions"] += 1
            n["total_ms"] += dur
            n["errors"] += int(failed)
            n["retries"] += max(0, int(a.get("ai_sre.node.attempts", 1)) - 1)
        elif kind == "tool":
            key = str(a.get("ai_sre.tool.key", s["name"]))
            t = tools.setdefault(key, {"calls": 0, "errors": 0, "retries": 0, "total_ms": 0.0})
            retries = int(a.get("ai_sre.tool.retries", 0))
            t["calls"] += 1
            t["total_ms"] += dur
            t["retries"] += retries
            t["errors"] += int(failed)
            tool_calls += 1
            tool_errors += int(failed)
            tool_retries += retries
            tool_ms += dur
            rounds = max(rounds, int(a.get("ai_sre.round", 0)))
        elif kind == "llm":
            llm_calls += 1
            llm_errors += int(failed)
            llm_retries += max(0, int(a.get("ai_sre.llm.attempts", 1)) - 1)
            tokens_in += int(a.get("gen_ai.usage.input_tokens", 0))
            tokens_out += int(a.get("gen_ai.usage.output_tokens", 0))
            llm_ms += dur
            llm_cost += float(a.get("ai_sre.llm.cost_usd", 0.0))
            model = a.get("gen_ai.response.model") or a.get("gen_ai.request.model")
            if model and model not in models:
                models.append(str(model))
    for v in (*nodes.values(), *tools.values()):
        v["total_ms"] = round(v["total_ms"], 1)
    return {
        "spans": len(spans),
        "duration_ms": round(run_ms, 1),
        "iterations": rounds,
        "nodes": nodes,
        "tools": {
            "calls": tool_calls,
            "errors": tool_errors,
            "retries": tool_retries,
            "evidence_ok": tool_calls - tool_errors,
            "latency_ms": round(tool_ms, 1),
            "avg_latency_ms": round(tool_ms / tool_calls, 1) if tool_calls else 0.0,
            "by_tool": dict(sorted(tools.items())),
        },
        "llm": {
            "calls": llm_calls,
            "errors": llm_errors,
            "retries": llm_retries,
            "input_tokens": tokens_in,
            "output_tokens": tokens_out,
            "total_tokens": tokens_in + tokens_out,
            "latency_ms": round(llm_ms, 1),
            "cost_usd": round(llm_cost, 6),
            "models": models,
        },
        "retries": {
            "tools": tool_retries,
            "nodes": sum(n["retries"] for n in nodes.values()),
            "llm": llm_retries,
        },
        "result": result,
    }


def set_result_attributes(span: trace.Span, record: Mapping[str, Any]) -> None:
    """Final outcome on the investigation span (status, severity, root cause, confidence)."""
    span.set_attribute("ai_sre.result.status", str(record.get("status") or "unknown"))
    if record.get("severity"):
        span.set_attribute("ai_sre.result.severity", str(record["severity"]))
    root = record.get("root_cause") or {}
    if isinstance(root, Mapping) and root:
        span.set_attribute("ai_sre.result.root_cause", redact_text(str(root.get("title", "")), 200))
        span.set_attribute("ai_sre.result.category", str(root.get("category", "")))
        span.set_attribute("ai_sre.result.component", str(root.get("component") or ""))
        span.set_attribute("ai_sre.result.confidence", float(root.get("confidence") or 0.0))
        span.set_attribute("ai_sre.result.band", str(root.get("band", "")))
    report = record.get("report") or {}
    if isinstance(report, Mapping) and report:
        span.set_attribute("ai_sre.result.stop_reason", str(report.get("stop_reason", "")))
        span.set_attribute("ai_sre.result.evidence_cited", len(report.get("evidence") or []))
