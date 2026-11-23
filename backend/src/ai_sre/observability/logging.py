"""Structured logging.

structlog renders every record, including stdlib records from uvicorn, SQLAlchemy and
third-party libraries, as JSON (production/containers) or coloured key-value pairs
(local development). Request-scoped fields such as ``request_id`` are bound via
``structlog.contextvars`` and appear on every line logged while handling that request.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from ai_sre.config import LogFormat

_SENSITIVE_KEYS = frozenset(
    {"authorization", "password", "secret", "token", "api_key", "cookie", "set-cookie"}
)


def _drop_sensitive_keys(
    _logger: Any, _method: str, event_dict: structlog.typing.EventDict
) -> structlog.typing.EventDict:
    """Defence in depth: never emit values for keys that look like credentials."""
    for key in list(event_dict):
        if key.lower() in _SENSITIVE_KEYS:
            event_dict[key] = "[REDACTED]"
    return event_dict


def configure_logging(level: str = "INFO", fmt: LogFormat = "json") -> None:
    shared_processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _drop_sensitive_keys,
    ]

    renderer: structlog.typing.Processor
    if fmt == "json":
        renderer = structlog.processors.JSONRenderer()
        shared_processors.append(structlog.processors.dict_tracebacks)
    else:
        renderer = structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared_processors,
            processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, renderer],
        )
    )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # Uvicorn installs its own handlers; route its records through ours instead.
    # Access logs are disabled because the request middleware logs every request.
    for name in ("uvicorn", "uvicorn.error"):
        uv_logger = logging.getLogger(name)
        uv_logger.handlers.clear()
        uv_logger.propagate = True
    access = logging.getLogger("uvicorn.access")
    access.handlers.clear()
    access.propagate = False

    for noisy in ("httpx", "httpcore", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.stdlib.get_logger(name)
    return logger
