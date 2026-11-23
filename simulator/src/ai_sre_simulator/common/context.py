"""Request-scoped context (trace id, request id, endpoint) and W3C ``traceparent`` helpers."""

from __future__ import annotations

import re
import secrets
from contextvars import ContextVar

trace_id_var: ContextVar[str | None] = ContextVar("trace_id", default=None)
span_id_var: ContextVar[str | None] = ContextVar("span_id", default=None)
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
endpoint_var: ContextVar[str | None] = ContextVar("endpoint", default=None)
# Set when a request is served by a different build than the process default (canary).
version_override_var: ContextVar[str | None] = ContextVar("version_override", default=None)

_TRACEPARENT = re.compile(r"^00-([0-9a-f]{32})-([0-9a-f]{16})-[0-9a-f]{2}$")


def new_trace_id() -> str:
    return secrets.token_hex(16)


def new_span_id() -> str:
    return secrets.token_hex(8)


def new_request_id() -> str:
    return secrets.token_hex(8)


def parse_traceparent(header: str | None) -> tuple[str, str] | None:
    """Return ``(trace_id, parent_span_id)`` for a valid header, else ``None``."""
    if not header:
        return None
    match = _TRACEPARENT.match(header.strip().lower())
    if not match or match.group(1) == "0" * 32:
        return None
    return match.group(1), match.group(2)


def format_traceparent(trace_id: str, span_id: str) -> str:
    return f"00-{trace_id}-{span_id}-01"


def outbound_headers() -> dict[str, str]:
    """Propagation headers for a call made while handling the current request or job."""
    headers: dict[str, str] = {}
    trace_id = trace_id_var.get()
    if trace_id:
        headers["traceparent"] = format_traceparent(trace_id, new_span_id())
    request_id = request_id_var.get()
    if request_id:
        headers["X-Request-ID"] = request_id
    return headers
