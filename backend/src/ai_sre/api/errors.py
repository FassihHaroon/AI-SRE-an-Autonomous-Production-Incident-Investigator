"""Error responses in RFC 9457 problem-details format."""

from __future__ import annotations

from typing import cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ai_sre.observability.logging import get_logger

log = get_logger(__name__)

PROBLEM_JSON = "application/problem+json"


def problem(status: int, title: str, detail: str | None = None, **extra: object) -> JSONResponse:
    body: dict[str, object] = {"type": "about:blank", "title": title, "status": status}
    if detail:
        body["detail"] = detail
    body.update(extra)
    return JSONResponse(body, status_code=status, media_type=PROBLEM_JSON)


async def _http_exception(_request: Request, exc: Exception) -> JSONResponse:
    http_exc = cast(StarletteHTTPException, exc)  # registered for this type only
    return problem(http_exc.status_code, title=str(http_exc.detail))


async def _validation_exception(_request: Request, exc: Exception) -> JSONResponse:
    validation_exc = cast(RequestValidationError, exc)
    return problem(422, title="Request validation failed", errors=validation_exc.errors())


async def _unhandled_exception(_request: Request, exc: Exception) -> JSONResponse:
    log.exception("http.unhandled_exception", error=type(exc).__name__)
    return problem(500, title="Internal Server Error")


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, _http_exception)
    app.add_exception_handler(RequestValidationError, _validation_exception)
    app.add_exception_handler(Exception, _unhandled_exception)
