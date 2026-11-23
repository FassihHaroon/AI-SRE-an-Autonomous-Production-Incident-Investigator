"""Application services for the API: MCP gateway, platform reads, investigation runner, settings.

Built at startup (``build_runtime``). Degrades gracefully: without the database the event log and
settings live in memory; without an async-capable Postgres checkpointer the graph checkpoints in
memory (paused investigations then do not survive an API restart). Settings → Integrations shows
which is in use.
"""

from __future__ import annotations

import os
from contextlib import AsyncExitStack
from typing import Any

import structlog
from fastapi import FastAPI
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver

from ai_sre.agent.gateway import ClientFactory, ToolGateway, http_client_factory
from ai_sre.agent.graph import Deps, build_graph
from ai_sre.agent.reasoner import LLMReasoner, RuleBasedReasoner
from ai_sre.agent.runner import serializer
from ai_sre.config import Settings
from ai_sre.mcp_servers.app import LazyRetriever
from ai_sre.mcp_servers.settings import McpSettings, get_mcp_settings
from ai_sre.services.investigations import InvestigationService
from ai_sre.services.platform import PlatformService
from ai_sre.services.repository import MemoryRepository, PostgresRepository, Repository
from ai_sre.services.settings_store import SettingsStore

logger = structlog.get_logger("api.runtime")


def mcp_url(mcp: McpSettings) -> str:
    return os.environ.get("AI_SRE_MCP_URL", f"http://127.0.0.1:{mcp.port}")


async def build_runtime(
    app: FastAPI,
    settings: Settings,
    stack: AsyncExitStack,
    *,
    client_factory: ClientFactory | None = None,
    repo: Repository | None = None,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    retriever: Any = None,
) -> None:
    mcp = get_mcp_settings()
    url = mcp_url(mcp)
    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")

    if repo is None:
        try:
            repo = await PostgresRepository.connect(dsn)
            app.state.repo_kind = "postgres"
        except Exception as exc:
            logger.warning(
                "investigation_store_fallback_memory", error=f"{type(exc).__name__}: {exc}"
            )
            repo = MemoryRepository()
            app.state.repo_kind = "memory"
    else:
        app.state.repo_kind = type(repo).__name__
    stack.push_async_callback(repo.close)

    if checkpointer is None:
        try:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

            saver = await stack.enter_async_context(
                AsyncPostgresSaver.from_conn_string(dsn, serde=serializer())
            )
            await saver.setup()
            checkpointer, app.state.checkpointer_kind = saver, "postgres"
        except Exception as exc:  # e.g. Windows Proactor loop, database down
            logger.warning("checkpointer_fallback_memory", error=f"{type(exc).__name__}: {exc}")
            checkpointer, app.state.checkpointer_kind = InMemorySaver(serde=serializer()), "memory"
    else:
        app.state.checkpointer_kind = type(checkpointer).__name__

    factory = client_factory or http_client_factory(
        url, mcp.read_token.get_secret_value(), mcp.remediation_token.get_secret_value()
    )
    gateway = ToolGateway(factory)
    graphs = {
        "rules": build_graph(Deps(gateway=gateway, reasoner=RuleBasedReasoner())).compile(
            checkpointer=checkpointer
        )
    }
    if settings.llm_configured:
        from ai_sre.integrations.llm.factory import create_llm_provider

        llm = LLMReasoner(create_llm_provider(settings))
        graphs["llm"] = build_graph(Deps(gateway=gateway, reasoner=llm)).compile(
            checkpointer=checkpointer
        )

    agent_settings = SettingsStore(repo)
    await agent_settings.load()
    investigations = InvestigationService(
        repo,
        graphs,
        mcp_url=url,
        admin_token=mcp.admin_token.get_secret_value(),
        reasoner=lambda: agent_settings.current.reasoner,
        default_limits=lambda: agent_settings.current.limits(),
    )
    stack.push_async_callback(investigations.shutdown)

    app.state.mcp_settings = mcp
    app.state.mcp_url = url
    app.state.platform = PlatformService(ToolGateway(factory, retries=0))
    app.state.investigations = investigations
    app.state.agent_settings = agent_settings
    app.state.retriever = retriever or LazyRetriever(mcp)
