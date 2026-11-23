"""Request logging middleware (pure ASGI, so it is safe for streaming/SSE responses)."""

from __future__ import annotations

import re
import time
import uuid

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ai_sre.observability.logging import get_logger

REQUEST_ID_HEADER = "x-request-id"
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_QUIET_PATHS = frozenset({"/healthz", "/readyz"})

log = get_logger("ai_sre.http")


class RequestContextMiddleware:
    """Assigns a request id, binds it to the log context and logs one line per request."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _incoming_request_id(scope) or uuid.uuid4().hex
        path: str = scope.get("path", "")
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        status_code = 500
        started = time.perf_counter()

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = list(message.get("headers", []))
                headers.append((REQUEST_ID_HEADER.encode(), request_id.encode()))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            level = "debug" if path in _QUIET_PATHS and status_code < 400 else "info"
            getattr(log, level)(
                "http.request",
                method=scope.get("method"),
                path=path,
                status=status_code,
                duration_ms=duration_ms,
            )
            structlog.contextvars.clear_contextvars()


def _incoming_request_id(scope: Scope) -> str | None:
    for key, value in scope.get("headers", []):
        if key.decode("latin-1").lower() == REQUEST_ID_HEADER:
            candidate = value.decode("latin-1")
            # Only accept well-formed ids, so header injection cannot pollute logs.
            return candidate if _VALID_REQUEST_ID.match(candidate) else None
    return None
