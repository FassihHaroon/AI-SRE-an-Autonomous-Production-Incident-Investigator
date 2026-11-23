"""Structured JSON logging for the simulated services.

Every line is one JSON object with a fixed core schema, so log queries (LogQL ``| json``)
work the same way for every service:

``timestamp`` (RFC 3339, UTC, ms) · ``severity`` · ``service`` · ``environment`` ·
``simulated`` (always ``true``) · ``version`` · ``trace_id`` · ``span_id`` · ``request_id`` ·
``endpoint`` · ``logger`` · ``message`` · ``error`` (``{type, message, stack}``, only when
there is one) · plus event-specific fields (``status_code``, ``duration_ms``, ``order_id``…).
"""

from __future__ import annotations

import json
import logging
import sys
import traceback
from datetime import UTC, datetime
from typing import Any

from ai_sre_simulator.common import context

_MAX_STACK_CHARS = 2000
_service_version = {"value": "0.0.0"}


def set_service_version(version: str) -> None:
    """Called by the runtime whenever the deployed version changes."""
    _service_version["value"] = version


_RESERVED = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
    "fields",
    "color_message",  # uvicorn: ANSI-coloured duplicate of the message
}


def _format_error(exc_info: Any) -> dict[str, str] | None:
    if not exc_info or exc_info[1] is None:
        return None
    exc = exc_info[1]
    stack = "".join(traceback.format_exception(exc))
    return {
        "type": type(exc).__name__,
        "message": str(exc) or type(exc).__name__,
        "stack": stack[-_MAX_STACK_CHARS:],
    }


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str, environment: str) -> None:
        super().__init__()
        self._service = service
        self._environment = environment

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "severity": record.levelname,
            "service": self._service,
            "environment": self._environment,
            "simulated": True,
            "version": context.version_override_var.get() or _service_version["value"],
            "trace_id": context.trace_id_var.get(),
            "span_id": context.span_id_var.get(),
            "request_id": context.request_id_var.get(),
            "endpoint": context.endpoint_var.get(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        fields: dict[str, Any] = getattr(record, "fields", None) or {}
        payload.update(fields)
        # Plain `extra=` keys from third-party loggers.
        for key, value in record.__dict__.items():
            if key not in _RESERVED and key not in payload and not key.startswith("_"):
                payload[key] = value
        error = _format_error(record.exc_info)
        if error is not None:
            payload["error"] = error
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(service: str, environment: str, level: str, version: str) -> None:
    set_service_version(version)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service, environment))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers.clear()
        logging.getLogger(name).propagate = True
    for name in ("uvicorn.access", "httpx", "httpcore", "asyncio"):
        logging.getLogger(name).setLevel(logging.WARNING)


def log(logger: logging.Logger, level: int, message: str, /, **fields: Any) -> None:
    """Log ``message`` with structured ``fields`` merged into the JSON payload."""
    exc_info = fields.pop("exc_info", None)
    logger.log(level, message, extra={"fields": fields}, exc_info=exc_info)


def error_fields(exc: BaseException) -> dict[str, Any]:
    """An ``error`` object for failures that are handled (no stack trace needed)."""
    return {"error": {"type": type(exc).__name__, "message": str(exc) or type(exc).__name__}}
