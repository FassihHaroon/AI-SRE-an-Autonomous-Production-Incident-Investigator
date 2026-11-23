"""Run, pause, resume and cancel investigations (checkpointed by LangGraph).

Checkpoints go to PostgreSQL (``AsyncPostgresSaver``, the platform database) so an
investigation interrupted for approval can be resumed later from another process. Tests use the
in-memory saver.
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from pydantic import BaseModel

from ai_sre.agent import models
from ai_sre.agent.gateway import ToolGateway, http_client_factory
from ai_sre.agent.graph import Deps, build_graph
from ai_sre.agent.models import Decision, Limits, Report
from ai_sre.agent.reasoner import LLMReasoner, Reasoner, RuleBasedReasoner


class RunResult(BaseModel):
    investigation_id: str
    status: str  # "completed" | "awaiting_approval"
    report: Report | None = None
    approval: dict[str, Any] | None = None


class Investigator:
    def __init__(self, graph: CompiledStateGraph[Any, Any, Any, Any]) -> None:
        self.graph = graph

    @staticmethod
    def config(investigation_id: str) -> RunnableConfig:
        return {"configurable": {"thread_id": investigation_id}, "recursion_limit": 80}

    async def start(
        self, question: str, *, limits: Limits | None = None, investigation_id: str | None = None
    ) -> RunResult:
        inv_id = investigation_id or f"inv-{datetime.now(UTC):%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"
        state: dict[str, Any] = {
            "investigation_id": inv_id,
            "question": question,
            "started_at": datetime.now(UTC),
            "limits": limits or Limits(),
            "cancel_requested": False,
        }
        await self.graph.ainvoke(state, self.config(inv_id))
        return await self.result(inv_id)

    async def resume(self, investigation_id: str, decisions: list[Decision]) -> RunResult:
        payload: list[dict[str, Any]] = [d.model_dump() for d in decisions]
        await self.graph.ainvoke(Command(resume=payload), self.config(investigation_id))
        return await self.result(investigation_id)

    async def cancel(self, investigation_id: str) -> None:
        await self.graph.aupdate_state(self.config(investigation_id), {"cancel_requested": True})

    async def result(self, investigation_id: str) -> RunResult:
        snap = await self.graph.aget_state(self.config(investigation_id))
        interrupts = [i.value for task in snap.tasks for i in task.interrupts]
        if interrupts:
            return RunResult(
                investigation_id=investigation_id,
                status="awaiting_approval",
                approval=interrupts[0],
            )
        report = snap.values.get("report")
        return RunResult(
            investigation_id=investigation_id,
            status="completed" if report else "unknown",
            report=report,
        )


def serializer() -> JsonPlusSerializer:
    """Checkpoint serializer that explicitly allows the agent's own state models."""
    allowed = [
        ("ai_sre.agent.models", name)
        for name, obj in vars(models).items()
        if isinstance(obj, type)
        and issubclass(obj, BaseModel)
        and obj.__module__ == models.__name__
    ]
    return JsonPlusSerializer(allowed_msgpack_modules=allowed)


def make_reasoner(kind: str) -> Reasoner:
    if kind == "llm":
        from ai_sre.config import get_settings
        from ai_sre.integrations.llm.factory import create_llm_provider

        return LLMReasoner(create_llm_provider(get_settings()))
    return RuleBasedReasoner()


@asynccontextmanager
async def investigator(
    *,
    mcp_url: str,
    read_token: str,
    remediation_token: str,
    reasoner: Reasoner,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    postgres_dsn: str | None = None,
) -> AsyncIterator[Investigator]:
    deps = Deps(
        gateway=ToolGateway(http_client_factory(mcp_url, read_token, remediation_token)),
        reasoner=reasoner,
    )
    graph = build_graph(deps)
    if checkpointer is not None:
        yield Investigator(graph.compile(checkpointer=checkpointer))
        return
    if postgres_dsn:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        async with AsyncPostgresSaver.from_conn_string(postgres_dsn, serde=serializer()) as saver:
            await saver.setup()
            yield Investigator(graph.compile(checkpointer=saver))
        return
    yield Investigator(graph.compile(checkpointer=InMemorySaver(serde=serializer())))
