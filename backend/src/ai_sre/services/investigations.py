"""Run investigations in the API process and publish their progress as events.

The LangGraph engine (ai_sre.agent) runs as a background task per investigation. Every graph
step is translated into one or more *events* (plan created, tool call finished, hypotheses
updated, approval required, report ready…), appended to the event log (PostgreSQL) and pushed to
SSE subscribers. Clients that reconnect replay from their last event id. Nothing is synthesised:
every event is derived from real graph state.
"""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from ai_sre.agent.models import Decision, Limits
from ai_sre.services.repository import Repository

logger = structlog.get_logger("investigations")

TERMINAL = {"completed", "failed", "cancelled"}
NODE_LABELS = {
    "intake": "Understanding the question",
    "planning": "Planning the investigation",
    "tool_selection": "Selecting tools",
    "tool_execution": "Gathering evidence",
    "evidence_analysis": "Analysing evidence",
    "hypothesis_evaluation": "Evaluating hypotheses",
    "replanning": "Deciding next step",
    "root_cause": "Determining root cause",
    "remediation_proposal": "Proposing remediation",
    "approval": "Waiting for approval",
    "remediation_execution": "Executing approved action",
    "verification": "Verifying the fix",
    "report": "Writing the report",
}


def _dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_dump(v) for v in value]
    if isinstance(value, dict):
        return {k: _dump(v) for k, v in value.items()}
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def events_from_update(node: str, update: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Translate one node's state update into UI events."""
    out: list[tuple[str, dict[str, Any]]] = [
        ("node_completed", {"node": node, "label": NODE_LABELS.get(node, node)})
    ]
    if not update:
        return out
    if node == "intake" and update.get("scope"):
        out.append(("scope", _dump(update["scope"])))
    if node == "planning" and update.get("plan"):
        plan = _dump(update["plan"])
        out.append(
            (
                "plan",
                {"round": plan["round"], "rationale": plan["rationale"], "calls": plan["calls"]},
            )
        )
    if node == "tool_execution":
        for ev in update.get("evidence", []):
            e = _dump(ev)
            out.append(
                (
                    "tool_call",
                    {
                        k: e[k]
                        for k in (
                            "id",
                            "round",
                            "server",
                            "tool",
                            "arguments",
                            "ok",
                            "summary",
                            "error_code",
                            "error_message",
                            "duration_ms",
                            "at",
                        )
                    },
                )
            )
    if node == "evidence_analysis":
        out.append(
            (
                "evidence_analyzed",
                {
                    "findings": len(update.get("findings", [])),
                    "timeline": _dump(update.get("timeline", [])),
                },
            )
        )
    if node == "hypothesis_evaluation":
        hyps = _dump(update.get("hypotheses", []))
        out.append(
            (
                "hypotheses",
                {
                    "hypotheses": [
                        {
                            k: h[k]
                            for k in (
                                "id",
                                "category",
                                "component",
                                "title",
                                "trigger_ref",
                                "confidence",
                                "band",
                                "temporal",
                                "mechanism",
                                "breakdown",
                            )
                        }
                        | {
                            "supporting": len(h["supporting"]),
                            "contradicting": len(h["contradicting"]),
                        }
                        for h in hyps[:5]
                    ]
                },
            )
        )
    if node == "replanning" and update.get("stop_reason"):
        out.append(("decision", {"stop_reason": update["stop_reason"]}))
    if node == "root_cause":
        out.append(
            (
                "root_cause",
                {
                    "root_cause": _dump(update.get("root_cause")),
                    "alternatives": _dump(update.get("alternatives", [])),
                },
            )
        )
    if (
        node in ("remediation_proposal", "approval", "remediation_execution")
        and update.get("proposals") is not None
    ):
        out.append(("proposals", {"proposals": _dump(update["proposals"])}))
    if node == "verification" and update.get("verification"):
        out.append(("verification", _dump(update["verification"])))
    if node == "report" and update.get("report"):
        out.append(("report_ready", {"summary": update["report"].summary}))
    for err in update.get("errors", []) or []:
        out.append(("error", _dump(err)))
    return out


class InvestigationService:
    def __init__(
        self,
        repo: Repository,
        graphs: dict[str, CompiledStateGraph[Any, Any, Any, Any]],
        *,
        mcp_url: str,
        admin_token: str,
        reasoner: Callable[[], str] = lambda: "rules",
        default_limits: Callable[[], Limits] | None = None,
    ) -> None:
        """``graphs`` maps reasoner name → compiled graph; all share one checkpointer."""
        self.repo = repo
        self.graphs = graphs
        self.graph = graphs["rules"]  # any graph can read state (shared checkpointer)
        self.mcp_url = mcp_url.rstrip("/")
        self.admin_token = admin_token
        self._reasoner = reasoner
        self._default_limits = default_limits or Limits
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._waiters: dict[str, asyncio.Event] = {}

    # ------------------------------------------------------------------ events
    def waiter(self, inv_id: str) -> asyncio.Event:
        return self._waiters.setdefault(inv_id, asyncio.Event())

    async def emit(self, inv_id: str, type_: str, payload: dict[str, Any]) -> None:
        await self.repo.append_event(inv_id, type_, payload)
        event = self._waiters.pop(inv_id, None)
        if event:
            event.set()

    @staticmethod
    def config(inv_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": inv_id}, "recursion_limit": 80}

    # ------------------------------------------------------------------ lifecycle
    async def start(
        self, question: str, *, limits: Limits | None = None, parent_id: str | None = None
    ) -> str:
        inv_id = f"inv-{datetime.now(UTC):%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"
        limits = limits or self._default_limits()
        reasoner = self._reasoner() if self._reasoner() in self.graphs else "rules"
        await self.repo.create(
            {
                "id": inv_id,
                "question": question,
                "status": "running",
                "parent_id": parent_id,
                "reasoner": reasoner,
                "limits": limits.model_dump(),
            }
        )
        await self.emit(inv_id, "status", {"status": "running"})
        state = {
            "investigation_id": inv_id,
            "question": question,
            "started_at": datetime.now(UTC),
            "limits": limits,
            "cancel_requested": False,
        }
        self._launch(inv_id, state, reasoner)
        return inv_id

    def _launch(self, inv_id: str, graph_input: Any, reasoner: str) -> None:
        graph = self.graphs.get(reasoner, self.graph)
        task = asyncio.create_task(
            self._run(inv_id, graph_input, graph), name=f"investigation-{inv_id}"
        )
        self._tasks[inv_id] = task
        task.add_done_callback(lambda _t: self._tasks.pop(inv_id, None))

    async def _run(
        self, inv_id: str, graph_input: Any, graph: CompiledStateGraph[Any, Any, Any, Any]
    ) -> None:
        try:
            async for mode, chunk in graph.astream(  # type: ignore[call-overload]
                graph_input,
                self.config(inv_id),
                stream_mode=["tasks", "updates"],
            ):
                if (
                    mode == "tasks"
                    and isinstance(chunk, dict)
                    and "result" not in chunk
                    and chunk.get("name") in NODE_LABELS
                ):
                    await self.emit(
                        inv_id,
                        "node_started",
                        {"node": chunk["name"], "label": NODE_LABELS[chunk["name"]]},
                    )
                elif mode == "updates" and isinstance(chunk, dict):
                    for node, update in chunk.items():
                        if node == "__interrupt__":
                            continue
                        for type_, payload in events_from_update(node, update or {}):
                            await self.emit(inv_id, type_, payload)
            await self._finish(inv_id)
        except Exception as exc:
            logger.exception("investigation_failed", investigation=inv_id)
            await self.repo.update(inv_id, status="failed", error=f"{type(exc).__name__}: {exc}")
            await self.emit(
                inv_id, "status", {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
            )

    async def _finish(self, inv_id: str) -> None:
        snap = await self.graph.aget_state(self.config(inv_id))  # type: ignore[arg-type]
        values = snap.values
        interrupts = [i.value for task in snap.tasks for i in task.interrupts]
        scope = values.get("scope")
        common = {
            "severity": scope.severity if scope else None,
            "root_cause": _dump(values.get("root_cause")),
        }
        if interrupts:
            approval = _dump(interrupts[0])
            await self.repo.update(inv_id, status="awaiting_approval", approval=approval, **common)
            await self.emit(inv_id, "approval_required", approval)
            await self.emit(inv_id, "status", {"status": "awaiting_approval"})
            return
        report = values.get("report")
        status = "cancelled" if values.get("stop_reason") == "cancelled" else "completed"
        severity = report.severity if report else common["severity"]
        await self.repo.update(
            inv_id,
            status=status,
            report=_dump(report),
            approval=None,
            severity=severity,
            root_cause=common["root_cause"],
        )
        await self.emit(inv_id, "status", {"status": status})

    async def decide(
        self, inv_id: str, *, proposal_id: str, approve: bool, approver: str, comment: str | None
    ) -> None:
        record = await self.repo.get(inv_id)
        if record is None or record["status"] != "awaiting_approval":
            raise ValueError("investigation is not waiting for approval")
        proposal = next(
            (p for p in record["approval"]["proposals"] if p["id"] == proposal_id), None
        )
        if proposal is None:
            raise ValueError(f"unknown proposal {proposal_id}")
        token = (
            await self.mint_token(proposal["approval_request_id"], approver) if approve else None
        )
        decision = Decision(
            proposal_id=proposal_id,
            approved=approve,
            approver=approver,
            approval_token=token,
            comment=comment,
        )
        await self.repo.update(inv_id, status="running")
        await self.emit(
            inv_id,
            "approval_decision",
            {
                "proposal_id": proposal_id,
                "approved": approve,
                "approver": approver,
                "comment": comment,
            },
        )
        await self.emit(inv_id, "status", {"status": "running"})
        self._launch(
            inv_id, Command(resume=[decision.model_dump()]), record.get("reasoner") or "rules"
        )

    async def mint_token(self, approval_request_id: str, approver: str) -> str:
        """Approve the MCP approval request as the human ``approver`` (admin endpoint)."""
        async with httpx.AsyncClient(timeout=20) as http:
            r = await http.post(
                f"{self.mcp_url}/approvals/{approval_request_id}/approve",
                json={"approver": approver},
                headers={"Authorization": f"Bearer {self.admin_token}"},
            )
        if r.status_code != 200:
            raise ValueError(f"the MCP server refused the approval: {r.text[:300]}")
        token: str = r.json()["approval_token"]
        return token

    async def cancel(self, inv_id: str) -> None:
        record = await self.repo.get(inv_id)
        if record is None:
            raise ValueError("unknown investigation")
        if record["status"] in TERMINAL:
            return
        await self.graph.aupdate_state(self.config(inv_id), {"cancel_requested": True})  # type: ignore[arg-type]
        if record["status"] == "awaiting_approval":  # resume with no decision → rejected → report
            await self.repo.update(inv_id, status="running")
            self._launch(inv_id, Command(resume=[]), record.get("reasoner") or "rules")
        await self.emit(inv_id, "cancel_requested", {})

    async def state(self, inv_id: str) -> dict[str, Any]:
        """Live graph state (plan, hypotheses, evidence, proposals…) for the workspace."""
        try:
            snap = await self.graph.aget_state(self.config(inv_id))  # type: ignore[arg-type]
        except Exception:
            return {}
        v = snap.values or {}
        return {
            "scope": _dump(v.get("scope")),
            "round": v.get("round"),
            "plan": _dump(v.get("plan")),
            "hypotheses": _dump(v.get("hypotheses", [])),
            "evidence": [
                {
                    k: e[k]
                    for k in (
                        "id",
                        "round",
                        "server",
                        "tool",
                        "ok",
                        "summary",
                        "error_code",
                        "duration_ms",
                        "at",
                    )
                }
                for e in _dump(v.get("evidence", []))
            ],
            "timeline": _dump(v.get("timeline", [])),
            "root_cause": _dump(v.get("root_cause")),
            "alternatives": _dump(v.get("alternatives", [])),
            "proposals": _dump(v.get("proposals", [])),
            "verification": _dump(v.get("verification")),
            "stop_reason": v.get("stop_reason"),
            "limitations": list(dict.fromkeys(v.get("limitations", []))),
            "report": _dump(v.get("report")),
        }

    async def tool_call(self, inv_id: str, evidence_id: str) -> dict[str, Any] | None:
        snap = await self.graph.aget_state(self.config(inv_id))  # type: ignore[arg-type]
        for ev in (snap.values or {}).get("evidence", []):
            if ev.id == evidence_id:
                result: dict[str, Any] = _dump(ev)
                return result
        return None

    def is_running(self, inv_id: str) -> bool:
        return inv_id in self._tasks

    async def shutdown(self) -> None:
        for task in list(self._tasks.values()):
            task.cancel()
