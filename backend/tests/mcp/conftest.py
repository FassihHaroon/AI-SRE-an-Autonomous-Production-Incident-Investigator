"""Shared fixtures for MCP tests: settings, fake backends, in-process servers."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from ai_sre.config import REPO_ROOT
from ai_sre.mcp_servers.app import build_entries
from ai_sre.mcp_servers.approvals import ApprovalStore
from ai_sre.mcp_servers.backends import Backends, Database, Deployer, Loki, Orchestrator, Prometheus
from ai_sre.mcp_servers.registry import ServerEntry
from ai_sre.mcp_servers.settings import McpSettings

Handler = Callable[[httpx.Request], httpx.Response]


def make_settings(**overrides: Any) -> McpSettings:
    base: dict[str, Any] = {
        "read_token": SecretStr("t-read"),
        "remediation_token": SecretStr("t-remediate"),
        "admin_token": SecretStr("t-admin"),
        "approval_secret": SecretStr("test-approval-secret"),
        "incidents_dir": REPO_ROOT / "knowledge" / "incidents",
        "tool_timeout_s": 5.0,
        "backend_timeout_s": 2.0,
    }
    base.update(overrides)
    return McpSettings(**base)


def fake_backends(handler: Handler) -> Backends:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return Backends(
        http=http,
        prometheus=Prometheus(http, "http://prometheus"),
        loki=Loki(http, "http://loki"),
        deployer=Deployer(http, "http://deployer"),
        orchestrator=Orchestrator(http, "http://orchestrator"),
        database=Database("postgresql://nobody:x@127.0.0.1:9/none", 0.5),
    )


def empty_prometheus(request: httpx.Request) -> httpx.Response:
    if request.url.host == "prometheus":
        if request.url.path.endswith("/alerts"):
            return httpx.Response(200, json={"data": {"alerts": []}})
        return httpx.Response(200, json={"status": "success", "data": {"result": []}})
    return httpx.Response(503, text="unavailable")


class Built(dict[str, ServerEntry]):
    """Servers by name, plus the approval store they share (a human's view of approvals)."""

    approvals: ApprovalStore


@pytest.fixture
def entries_factory() -> Callable[..., Built]:
    def build(handler: Handler = empty_prometheus, **overrides: Any) -> Built:
        settings = make_settings(**overrides)
        approvals = ApprovalStore(b"test-approval-secret", settings.approval_ttl_s)
        built = Built(
            {e.name: e for e in build_entries(settings, fake_backends(handler), approvals)}
        )
        built.approvals = approvals
        return built

    return build


@pytest.fixture
def incidents_dir() -> Path:
    return REPO_ROOT / "knowledge" / "incidents"
