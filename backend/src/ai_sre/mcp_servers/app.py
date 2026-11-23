"""ASGI app hosting all MCP servers (Streamable HTTP) plus registry and approval endpoints.

Routes:

* ``/mcp/<server>/``           MCP endpoint per server (bearer token; see auth below)
* ``GET /registry``            tool registry (read token)
* ``GET /approvals``           approval requests (admin token)
* ``POST /approvals/{id}/approve`` / ``/reject``  human decision (admin token), body ``{"approver"}``
* ``GET /healthz``             liveness (no auth)

Auth (``Authorization: Bearer <token>``): read-only servers and the registry accept the read or
the remediation token; ``/mcp/remediation`` accepts only the remediation token; approvals only
the admin token. Tokens are compared in constant time.
"""

from __future__ import annotations

import contextlib
import hmac
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass

import structlog
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route

from ai_sre.mcp_servers.approvals import ApprovalError, ApprovalStore
from ai_sre.mcp_servers.backends import Backends
from ai_sre.mcp_servers.core import ToolRuntime
from ai_sre.mcp_servers.database import build_database_server
from ai_sre.mcp_servers.deployments import build_deployments_server
from ai_sre.mcp_servers.incidents import build_incidents_server
from ai_sre.mcp_servers.infrastructure import build_infrastructure_server
from ai_sre.mcp_servers.logs import build_logs_server
from ai_sre.mcp_servers.metrics import build_metrics_server
from ai_sre.mcp_servers.registry import ServerEntry, build_registry, build_registry_server
from ai_sre.mcp_servers.remediation import build_remediation_server
from ai_sre.mcp_servers.settings import McpSettings

logger = structlog.get_logger("mcp.app")


@dataclass
class McpApp:
    asgi: Starlette
    entries: list[ServerEntry]
    approvals: ApprovalStore
    backends: Backends


def build_entries(
    settings: McpSettings, backends: Backends, approvals: ApprovalStore
) -> list[ServerEntry]:
    runtime = ToolRuntime(settings.tool_timeout_s, settings.max_result_bytes)
    entries = [
        ServerEntry(
            "logs", build_logs_server(backends, runtime), True, "Loki log search and aggregation"
        ),
        ServerEntry("metrics", build_metrics_server(backends, runtime), True, "Prometheus metrics"),
        ServerEntry(
            "deployments", build_deployments_server(backends, runtime), True, "Deployment history"
        ),
        ServerEntry(
            "database", build_database_server(backends, runtime), True, "PostgreSQL diagnostics"
        ),
        ServerEntry(
            "infrastructure",
            build_infrastructure_server(backends, runtime),
            True,
            "Service, container, resource and dependency status",
        ),
        ServerEntry(
            "incidents",
            build_incidents_server(settings.incidents_dir, runtime),
            True,
            "Historical incidents (postmortems)",
        ),
        ServerEntry(
            "remediation",
            build_remediation_server(backends, runtime, settings, approvals),
            False,
            "Approval-gated actions on the SIMULATED environment",
        ),
    ]
    registry = build_registry_server(
        entries, runtime, remediation_enabled=settings.remediation_enabled
    )
    entries.insert(0, ServerEntry("registry", registry, True, "Discover servers and tools"))
    return entries


def create_app(settings: McpSettings) -> McpApp:
    backends = Backends.from_settings(settings)
    approvals = ApprovalStore(
        settings.approval_secret.get_secret_value().encode(), settings.approval_ttl_s
    )
    entries = build_entries(settings, backends, approvals)
    apps = {
        e.name: e.server.streamable_http_app(
            streamable_http_path="/", stateless_http=True, json_response=True
        )
        for e in entries
    }

    @contextlib.asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        async with contextlib.AsyncExitStack() as stack:
            for sub in apps.values():
                await stack.enter_async_context(sub.router.lifespan_context(sub))
            logger.info(
                "mcp_servers_started",
                servers=[e.path for e in entries],
                remediation_enabled=settings.remediation_enabled,
            )
            yield
        await backends.close()

    async def healthz(_request: Request) -> Response:
        return JSONResponse({"status": "ok", "servers": [e.name for e in entries]})

    async def registry(request: Request) -> Response:
        result = await build_registry(
            entries,
            remediation_enabled=settings.remediation_enabled,
            include_schemas=request.query_params.get("schemas", "false") == "true",
        )
        return JSONResponse(result.model_dump())

    async def list_approvals(_request: Request) -> Response:
        return JSONResponse([r.public() for r in approvals.list()])

    async def decide(request: Request) -> Response:
        request_id = request.path_params["request_id"]
        decision = request.path_params["decision"]
        body = await request.json()
        approver = str(body.get("approver", "")).strip()
        try:
            if decision == "approve":
                token = approvals.approve(request_id, approver)
                logger.info("approval_granted", request_id=request_id, approver=approver)
                return JSONResponse(
                    {"request": approvals.get(request_id).public(), "approval_token": token}
                )
            if decision == "reject":
                req = approvals.reject(request_id, approver)
                logger.info("approval_rejected", request_id=request_id, approver=approver)
                return JSONResponse({"request": req.public()})
        except ApprovalError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        return JSONResponse({"error": "decision must be approve or reject"}, status_code=404)

    routes = [
        Route("/healthz", healthz),
        Route("/registry", registry),
        Route("/approvals", list_approvals),
        Route("/approvals/{request_id}/{decision}", decide, methods=["POST"]),
        *[Mount(f"/mcp/{name}", app=sub) for name, sub in apps.items()],
    ]
    asgi = Starlette(routes=routes, lifespan=lifespan)
    asgi.add_middleware(BearerAuth, settings=settings)
    return McpApp(asgi=asgi, entries=entries, approvals=approvals, backends=backends)


class BearerAuth(BaseHTTPMiddleware):
    def __init__(self, app: Callable[..., Awaitable[None]], settings: McpSettings) -> None:
        super().__init__(app)
        self._read = settings.read_token.get_secret_value()
        self._remediation = settings.remediation_token.get_secret_value()
        self._admin = settings.admin_token.get_secret_value()

    def _allowed(self, path: str) -> list[str]:
        if path.startswith("/mcp/remediation"):
            return [self._remediation]
        if path.startswith("/approvals"):
            return [self._admin]
        return [self._read, self._remediation]

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path
        if path == "/healthz":
            return await call_next(request)
        header = request.headers.get("authorization", "")
        token = header[7:] if header.lower().startswith("bearer ") else ""
        if not token:
            return JSONResponse(
                {"error": "missing bearer token"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
        if not any(hmac.compare_digest(token, allowed) for allowed in self._allowed(path)):
            logger.warning("mcp_auth_denied", path=path)
            return JSONResponse({"error": "token not allowed for this endpoint"}, status_code=403)
        return await call_next(request)
