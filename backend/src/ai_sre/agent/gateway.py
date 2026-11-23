"""Tool gateway: the single choke point between the graph and the MCP servers.

* Speaks real MCP (official client): Streamable HTTP to the MCP app, or in-process to
  ``MCPServer`` objects in tests.
* Allow-list: investigation nodes may call read-only tools only; remediation tools are callable
  only through :meth:`call_remediation` (used by the execution and approval nodes).
* Retries transient failures (timeouts, backend outages, transport errors) once with backoff.
* Turns every call into an :class:`Evidence` record (bounded data) that reports cite by id.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import structlog
from mcp import Client

from ai_sre.agent.models import Evidence

logger = structlog.get_logger("agent.gateway")

READ_SERVERS = frozenset(
    {
        "logs",
        "metrics",
        "deployments",
        "database",
        "infrastructure",
        "incidents",
        "knowledge",
        "registry",
    }
)
TRANSIENT_CODES = frozenset({"timeout", "backend_unavailable"})
MAX_EVIDENCE_BYTES = 6000

ClientFactory = Callable[[str], AbstractAsyncContextManager[Client]]


class ToolNotAllowedError(Exception):
    pass


def http_client_factory(base_url: str, read_token: str, remediation_token: str) -> ClientFactory:
    """Real MCP over Streamable HTTP with per-scope bearer tokens."""
    import httpx2
    from mcp.client.streamable_http import streamable_http_client

    @asynccontextmanager
    async def connect(server: str) -> AsyncIterator[Client]:
        token = remediation_token if server == "remediation" else read_token
        http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=60)
        url = f"{base_url.rstrip('/')}/mcp/{server}/"
        async with Client(streamable_http_client(url, http_client=http)) as client:
            yield client

    return connect


def inprocess_client_factory(servers: dict[str, Any]) -> ClientFactory:
    """In-process MCP (same protocol, memory transport) for tests."""

    @asynccontextmanager
    async def connect(server: str) -> AsyncIterator[Client]:
        async with Client(servers[server]) as client:
            yield client

    return connect


def _shrink(data: Any, budget: int = MAX_EVIDENCE_BYTES) -> Any:
    """Keep evidence payloads small in graph state (lists trimmed until under budget)."""
    if not isinstance(data, dict):
        return data
    out = dict(data)
    while len(json.dumps(out, default=str)) > budget:
        lists = [(k, v) for k, v in out.items() if isinstance(v, list) and len(v) > 1]
        if not lists:
            break
        key, longest = max(lists, key=lambda kv: len(json.dumps(kv[1], default=str)))
        out[key] = longest[: max(1, len(longest) // 2)]
        out["_trimmed"] = True
    return out


class ToolGateway:
    def __init__(self, connect: ClientFactory, *, retries: int = 1, backoff_s: float = 1.0) -> None:
        self._connect = connect
        self._retries = retries
        self._backoff = backoff_s
        self._counter = 0

    def next_id(self) -> str:
        self._counter += 1
        return f"ev-{self._counter:03d}"

    def resume_numbering(self, existing: int) -> None:
        self._counter = max(self._counter, existing)

    async def call(
        self, server: str, tool: str, arguments: dict[str, Any], round_: int
    ) -> Evidence:
        if server not in READ_SERVERS:
            raise ToolNotAllowedError(f"{server}.{tool} is not a read-only investigation tool")
        return await self._call(server, tool, arguments, round_)

    async def call_remediation(self, tool: str, arguments: dict[str, Any], round_: int) -> Evidence:
        return await self._call("remediation", tool, arguments, round_, retries=0)

    async def registry(self) -> dict[str, Any] | None:
        """The tool registry, or None if the MCP servers are unreachable."""
        try:
            async with self._connect("registry") as client:
                result = await client.call_tool("list_available_tools", {})
        except Exception as exc:
            logger.warning("registry_unreachable", error=f"{type(exc).__name__}: {exc}")
            return None
        return None if result.is_error else (result.structured_content or {})

    async def _call(
        self,
        server: str,
        tool: str,
        arguments: dict[str, Any],
        round_: int,
        retries: int | None = None,
    ) -> Evidence:
        attempts = (self._retries if retries is None else retries) + 1
        started = time.perf_counter()
        last_code, last_message = "transport_error", "no attempt made"
        for attempt in range(attempts):
            try:
                async with self._connect(server) as client:
                    result = await client.call_tool(tool, arguments)
                if not result.is_error:
                    data = result.structured_content or {}
                    return self._evidence(server, tool, arguments, round_, started, data=data)
                first = result.content[0] if result.content else None
                text = getattr(first, "text", None) or "unknown error"
                last_code, last_message = _parse_error(text)
            except Exception as exc:  # transport / protocol failure
                last_code, last_message = "transport_error", f"{type(exc).__name__}: {exc}"
            transient = last_code in TRANSIENT_CODES or last_code == "transport_error"
            if not transient or attempt == attempts - 1:
                break
            await asyncio.sleep(self._backoff * (2**attempt))
        logger.warning(
            "tool_call_failed", server=server, tool=tool, code=last_code, error=last_message[:300]
        )
        return self._evidence(
            server, tool, arguments, round_, started, code=last_code, message=last_message
        )

    def _evidence(
        self,
        server: str,
        tool: str,
        arguments: dict[str, Any],
        round_: int,
        started: float,
        *,
        data: dict[str, Any] | None = None,
        code: str | None = None,
        message: str | None = None,
    ) -> Evidence:
        ok = code is None
        redacted = {
            k: ("[REDACTED]" if k == "approval_token" and v else v) for k, v in arguments.items()
        }
        return Evidence(
            id=self.next_id(),
            round=round_,
            server=server,
            tool=tool,
            arguments=redacted,
            ok=ok,
            summary=str((data or {}).get("summary", "")) if ok else f"FAILED ({code}): {message}",
            data=_shrink(data) if ok else None,
            error_code=code,
            error_message=message,
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
            at=datetime.now(UTC),
        )


def _parse_error(text: str) -> tuple[str, str]:
    """MCP tool errors carry JSON {code, message, hint}; validation errors are plain text."""
    start = text.find("{")
    if start >= 0:
        try:
            payload = json.loads(text[start:])
            return str(payload.get("code", "tool_error")), str(payload.get("message", text))
        except ValueError:
            pass
    if "validation error" in text:
        return "invalid_argument", text[:500]
    return "tool_error", text[:500]
