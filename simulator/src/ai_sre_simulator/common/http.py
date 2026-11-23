"""HTTP plumbing shared by the simulated services.

* ``instrument_app`` adds the request middleware (trace/request ids, endpoint metrics, access
  log, in-app fault hooks), error handlers (mapping infrastructure failures to 5xx with a
  logged error), ``/healthz`` and ``/metrics``.
* ``ServiceClient`` makes outbound calls with ``traceparent``/``X-Request-ID`` propagation and
  client-side metrics.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import asyncpg
import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.routing import BaseRoute, Match
from starlette.types import Scope

from ai_sre_simulator.common import context, metrics
from ai_sre_simulator.common.cache import CacheUnavailableError
from ai_sre_simulator.common.db import DatabaseUnavailableError, PoolTimeoutError
from ai_sre_simulator.common.determinism import sampler
from ai_sre_simulator.common.faults import FaultType
from ai_sre_simulator.common.logging import log
from ai_sre_simulator.common.runtime import Runtime

logger = logging.getLogger("http")
access_logger = logging.getLogger("http.access")

_UNINSTRUMENTED_PREFIXES = ("/healthz", "/metrics", "/internal/")

LeakHook = Callable[[str], Awaitable[None]]


class AppError(Exception):
    def __init__(
        self, message: str, *, status_code: int = 500, code: str = "internal_error"
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class UpstreamError(Exception):
    def __init__(
        self, peer: str, message: str, *, status: int | None = None, timeout: bool = False
    ):
        super().__init__(message)
        self.peer = peer
        self.status = status
        self.timeout = timeout


class InjectedError(Exception):
    """Raised by the ``http_errors`` fault hook (looks like any unhandled server error)."""


class CanaryError(Exception):
    """A request routed to a broken canary replica (left behind by a failed rollout)."""

    def __init__(self, version: str, message: str) -> None:
        super().__init__(message)
        self.version = version


def _match_template(routes: Sequence[BaseRoute], scope: Scope) -> str | None:
    for route in routes:
        # FastAPI wraps included routers; their original routes carry the full path template.
        inner = getattr(route, "original_router", None)
        if inner is not None:
            found = _match_template(inner.routes, scope)
            if found is not None:
                return found
            continue
        match, _ = route.matches(scope)
        if match == Match.FULL:
            path = getattr(route, "path", None)
            return str(path) if path is not None else None
    return None


def _route_template(app: FastAPI, request: Request) -> str:
    """The route template (``/orders/{order_id}``), resolved before the handler runs, so that
    metric labels stay bounded and fault-hook errors carry the right endpoint."""
    return _match_template(app.router.routes, request.scope) or "unmatched"


def _error_body(code: str, message: str) -> dict[str, Any]:
    return {"error": code, "message": message, "request_id": context.request_id_var.get()}


def instrument_app(
    app: FastAPI,
    runtime: Runtime | None,
    *,
    leak_hook: LeakHook | None = None,
) -> None:
    """Attach middleware, error handlers and the health/metrics endpoints to ``app``."""

    async def _apply_faults(path: str) -> None:
        if runtime is None:
            return
        state = runtime.state
        canary = state.flag("canary")
        if canary and sampler.chance(state.seed, "canary", float(canary["fraction"])):
            raise CanaryError(str(canary["version"]), str(canary["error"]))
        for fault in state.faults_of(FaultType.API_LATENCY):
            p = fault.params
            if path.startswith(p["route_prefix"]) and sampler.chance(
                fault.seed, f"{fault.id}:p", p["probability"]
            ):
                delay = p["delay_ms"] + sampler.jitter(fault.seed, f"{fault.id}:j", p["jitter_ms"])
                await asyncio.sleep(max(delay, 0) / 1000)
        if leak_hook is not None:
            for fault in state.faults_of(FaultType.DB_POOL_EXHAUSTION):
                p = fault.params
                if path.startswith(p["route_prefix"]) and sampler.chance(
                    fault.seed, fault.id, p["leak_probability"]
                ):
                    await leak_hook(fault.id)
        for fault in state.faults_of(FaultType.HTTP_ERRORS):
            p = fault.params
            if path.startswith(p["route_prefix"]) and sampler.chance(
                fault.seed, fault.id, p["rate"]
            ):
                raise InjectedError(f"unhandled exception while processing {path}")

    @app.middleware("http")
    async def observe(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path
        if path.startswith(_UNINSTRUMENTED_PREFIXES):
            return await call_next(request)

        parent = context.parse_traceparent(request.headers.get("traceparent"))
        trace_id = parent[0] if parent else context.new_trace_id()
        request_id = request.headers.get("x-request-id") or context.new_request_id()
        route = _route_template(app, request)
        endpoint = f"{request.method} {route}"
        tokens = (
            context.trace_id_var.set(trace_id),
            context.span_id_var.set(context.new_span_id()),
            context.request_id_var.set(request_id),
            context.endpoint_var.set(endpoint),
        )
        override_token = context.version_override_var.set(None)
        metrics.HTTP_IN_FLIGHT.inc()
        started = time.perf_counter()
        status = 500
        try:
            try:
                await _apply_faults(path)
                response = await call_next(request)
            except CanaryError as exc:
                context.version_override_var.set(exc.version)
                log(
                    logger,
                    logging.ERROR,
                    f"unhandled error: {exc}",
                    error={"type": "ConfigError", "message": str(exc)},
                )
                response = JSONResponse(_error_body("internal_error", "Internal Server Error"), 500)
                response.headers["X-Served-Version"] = exc.version
            except InjectedError as exc:
                log(logger, logging.ERROR, f"unhandled error: {exc}", exc_info=exc)
                response = JSONResponse(_error_body("internal_error", "Internal Server Error"), 500)
            except Exception as exc:
                log(
                    logger,
                    logging.ERROR,
                    f"unhandled error: {type(exc).__name__}: {exc}",
                    exc_info=exc,
                )
                response = JSONResponse(_error_body("internal_error", "Internal Server Error"), 500)
            status = response.status_code
            response.headers["X-Request-ID"] = request_id
            response.headers["traceresponse"] = context.format_traceparent(
                trace_id, context.span_id_var.get() or context.new_span_id()
            )
            response.headers["X-Simulated-Environment"] = "true"
            return response
        finally:
            elapsed = time.perf_counter() - started
            metrics.HTTP_IN_FLIGHT.dec()
            metrics.HTTP_REQUESTS.labels(request.method, route, str(status)).inc()
            metrics.HTTP_DURATION.labels(request.method, route).observe(elapsed)
            level = (
                logging.ERROR
                if status >= 500
                else logging.WARNING
                if status >= 400
                else logging.INFO
            )
            log(
                access_logger,
                level,
                f"{request.method} {path} {status}",
                http_method=request.method,
                path=path,
                status_code=status,
                duration_ms=round(elapsed * 1000, 1),
            )
            context.version_override_var.reset(override_token)
            for var, token in zip(
                (
                    context.trace_id_var,
                    context.span_id_var,
                    context.request_id_var,
                    context.endpoint_var,
                ),
                tokens,
                strict=True,
            ):
                var.reset(token)

    def _handler(status: int, code: str) -> Callable[[Request, Exception], Awaitable[Response]]:
        async def handle(_request: Request, exc: Exception) -> Response:
            log(
                logger,
                logging.ERROR,
                str(exc),
                error={"type": type(exc).__name__, "message": str(exc)},
            )
            return JSONResponse(_error_body(code, str(exc)), status)

        return handle

    app.add_exception_handler(PoolTimeoutError, _handler(503, "db_pool_exhausted"))
    app.add_exception_handler(DatabaseUnavailableError, _handler(503, "database_unavailable"))
    app.add_exception_handler(CacheUnavailableError, _handler(503, "cache_unavailable"))
    app.add_exception_handler(asyncpg.PostgresError, _handler(500, "database_error"))

    async def upstream_handler(_request: Request, exc: Exception) -> Response:
        if not isinstance(exc, UpstreamError):
            raise exc
        status = 504 if exc.timeout else 502
        log(
            logger,
            logging.ERROR,
            str(exc),
            peer=exc.peer,
            upstream_status=exc.status,
            error={"type": type(exc).__name__, "message": str(exc)},
        )
        return JSONResponse(_error_body("upstream_error", str(exc)), status)

    async def app_error_handler(_request: Request, exc: Exception) -> Response:
        if not isinstance(exc, AppError):
            raise exc
        level = logging.ERROR if exc.status_code >= 500 else logging.WARNING
        log(logger, level, str(exc), error={"type": exc.code, "message": str(exc)})
        return JSONResponse(_error_body(exc.code, str(exc)), exc.status_code)

    app.add_exception_handler(UpstreamError, upstream_handler)
    app.add_exception_handler(AppError, app_error_handler)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "version": runtime.version if runtime else "n/a"}

    @app.get("/metrics", include_in_schema=False)
    async def prometheus_metrics() -> Response:
        return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)


class ServiceClient:
    """Outbound HTTP client with trace propagation and client-side metrics."""

    def __init__(self, peer: str, base_url: str, timeout_s: float) -> None:
        self.peer = peer
        self._timeout = timeout_s
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout_s,
            limits=httpx.Limits(max_connections=200, max_keepalive_connections=50),
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        started = time.perf_counter()
        status = "error"
        try:
            response = await self._client.request(
                method, path, json=json, params=params, headers=context.outbound_headers()
            )
            status = str(response.status_code)
            return response
        except httpx.TimeoutException as exc:
            raise UpstreamError(
                self.peer,
                f"{self.peer} did not respond within {self._timeout:.1f}s ({method} {path})",
                timeout=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise UpstreamError(
                self.peer,
                f"could not reach {self.peer} ({method} {path}): {type(exc).__name__}: {exc}",
            ) from exc
        finally:
            metrics.CLIENT_REQUESTS.labels(self.peer, method, status).inc()
            metrics.CLIENT_DURATION.labels(self.peer, method).observe(time.perf_counter() - started)
