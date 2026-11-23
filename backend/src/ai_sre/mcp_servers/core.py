"""Shared tool machinery: registration, timeouts, invocation logging, errors, size caps.

Every tool is registered through :func:`tool` which wraps the implementation so that it

* runs under a timeout (``AI_SRE_MCP_TOOL_TIMEOUT_S`` or a per-tool override),
* logs one structured ``mcp_tool_call`` line per invocation (tool, server, redacted arguments,
  duration, outcome, error code, result size),
* turns failures into MCP tool errors (``isError: true``) whose text is a JSON object
  ``{"code", "message", "hint"}``, so a model can self-correct,
* returns a typed pydantic model (MCP ``structuredContent`` + ``outputSchema``), trimmed to
  ``AI_SRE_MCP_MAX_RESULT_BYTES`` with ``truncated: true`` if it would be larger.

Input validation comes from the type hints: MCP publishes the JSON Schema and rejects invalid
arguments before the tool runs.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import json
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, ParamSpec, TypeVar

import structlog
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import BaseModel, Field

from ai_sre.mcp_servers.backends import BackendError

logger = structlog.get_logger("mcp.tool")

P = ParamSpec("P")
R = TypeVar("R", bound="ToolResult")

AppService = Literal["api-gateway", "orders-service", "fulfilment-worker"]
ObservedService = Literal[
    "api-gateway", "orders-service", "fulfilment-worker", "deployer", "postgres", "redis"
]
APP_SERVICES: tuple[str, ...] = ("api-gateway", "orders-service", "fulfilment-worker")
HTTP_SERVICES: tuple[str, ...] = ("api-gateway", "orders-service")

_SECRET_ARGS = frozenset({"approval_token", "token", "password"})


class ToolResult(BaseModel):
    """Fields common to every tool result."""

    tool: str = Field(default="", description="Tool that produced this result")
    summary: str = Field(description="One-line human-readable summary")
    truncated: bool = Field(default=False, description="True if items were dropped to fit limits")


class ToolFailure(Exception):  # noqa: N818 - reads better at call sites
    """An expected failure with a machine-readable code and a hint for the caller."""

    def __init__(self, code: str, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.hint = hint


def _error(code: str, message: str, hint: str | None = None) -> ToolError:
    return ToolError(json.dumps({"code": code, "message": message, "hint": hint}))


def _redact(kwargs: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in kwargs.items():
        if key in _SECRET_ARGS and value:
            out[key] = "[REDACTED]"
        elif isinstance(value, str) and len(value) > 200:
            out[key] = value[:200] + "…"
        else:
            out[key] = value
    return out


def cap_result(result: ToolResult, max_bytes: int) -> ToolResult:
    """Drop items from the largest list fields until the JSON fits ``max_bytes``."""
    size = len(result.model_dump_json())
    while size > max_bytes:
        lists = [
            (name, value)
            for name, value in result.__dict__.items()
            if isinstance(value, list) and len(value) > 1
        ]
        if not lists:
            break
        name, longest = max(lists, key=lambda item: len(json.dumps(item[1], default=str)))
        keep = max(1, len(longest) * 3 // 4)
        setattr(result, name, longest[:keep])
        result.truncated = True
        size = len(result.model_dump_json())
    return result


class ToolRuntime:
    """Per-app settings the wrapper needs (set once by the app factory)."""

    def __init__(self, timeout_s: float, max_result_bytes: int) -> None:
        self.timeout_s = timeout_s
        self.max_result_bytes = max_result_bytes


def tool(
    server: MCPServer,
    runtime: ToolRuntime,
    *,
    read_only: bool,
    destructive: bool = False,
    idempotent: bool = True,
    timeout_s: float | None = None,
    max_bytes: int | None = None,
    title: str | None = None,
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """Register ``fn`` as an MCP tool on ``server`` with the standard wrapper.

    The function's docstring is the tool description; its signature (with pydantic ``Field``
    constraints via ``Annotated``) is the input schema; its return annotation is the output
    schema.
    """

    def decorate(fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        name = fn.__name__
        limit = timeout_s or runtime.timeout_s
        size_cap = max_bytes or runtime.max_result_bytes

        @functools.wraps(fn)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            started = time.perf_counter()
            outcome, code, size, truncated = "ok", None, 0, False
            try:
                async with asyncio.timeout(limit):
                    result = await fn(*args, **kwargs)
                result.tool = name
                result = cap_result(result, size_cap)  # type: ignore[assignment]
                size, truncated = len(result.model_dump_json()), result.truncated
                return result
            except ToolFailure as exc:
                outcome, code = "error", exc.code
                raise _error(exc.code, str(exc), exc.hint) from exc
            except TimeoutError as exc:
                outcome, code = "error", "timeout"
                raise _error(
                    "timeout",
                    f"{name} did not finish within {limit:.0f}s",
                    "narrow the time range or lower the limit",
                ) from exc
            except BackendError as exc:
                outcome, code = "error", "backend_unavailable"
                raise _error("backend_unavailable", str(exc), f"{exc.backend} may be down") from exc
            except ToolError:
                outcome, code = "error", "tool_error"
                raise
            except Exception as exc:
                outcome, code = "error", "internal_error"
                logger.exception("mcp_tool_crash", tool=name, server=server.name)
                raise _error("internal_error", f"{type(exc).__name__}: {exc}") from exc
            finally:
                logger.info(
                    "mcp_tool_call",
                    tool=name,
                    server=server.name,
                    read_only=read_only,
                    arguments=_redact(dict(kwargs)),
                    outcome=outcome,
                    error_code=code,
                    duration_ms=round((time.perf_counter() - started) * 1000, 1),
                    result_bytes=size,
                    truncated=truncated,
                )

        server.tool(
            name=name,
            title=title,
            description=inspect.getdoc(fn),
            annotations=ToolAnnotations(
                title=title,
                read_only_hint=read_only,
                destructive_hint=destructive,
                idempotent_hint=idempotent,
                open_world_hint=False,
            ),
        )(wrapper)
        return fn

    return decorate


# --------------------------------------------------------------------------- time helpers
def utcnow() -> datetime:
    return datetime.now(UTC)


def window(since_minutes: int, end: datetime | None = None) -> tuple[datetime, datetime]:
    stop = (
        end.astimezone(UTC)
        if end and end.tzinfo
        else (end.replace(tzinfo=UTC) if end else utcnow())
    )
    return stop - timedelta(minutes=since_minutes), stop


def iso(ts: datetime) -> str:
    return ts.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def step_for(start: datetime, end: datetime, max_points: int = 60) -> int:
    """Query step (seconds, min 5 s) that keeps a range query at or below ``max_points``."""
    return max(5, int((end - start).total_seconds() // max_points) + 1)


def clip(text: str | None, limit: int) -> str | None:
    if text is None or len(text) <= limit:
        return text
    return text[: limit - 1] + "…"
