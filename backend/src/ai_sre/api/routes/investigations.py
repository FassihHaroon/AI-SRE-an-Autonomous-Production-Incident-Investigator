"""Investigations: start, list, inspect, stream progress (SSE), approve/reject, cancel, follow up."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ai_sre.services.investigations import TERMINAL, InvestigationService

router = APIRouter(prefix="/investigations", tags=["investigations"])
HEARTBEAT_S = 15.0


def _svc(request: Request) -> InvestigationService:
    service: InvestigationService = request.app.state.investigations
    return service


class StartRequest(BaseModel):
    question: str = Field(min_length=8, max_length=1000)


class FollowUpRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)


class DecisionRequest(BaseModel):
    proposal_id: str
    decision: Literal["approve", "reject"]
    approver: str = Field(min_length=2, max_length=80)
    comment: str | None = Field(default=None, max_length=500)
    confirm: bool = Field(
        description="Must be true: the user confirmed the action in a second step"
    )


async def _record(svc: InvestigationService, inv_id: str) -> dict[str, Any]:
    record = await svc.repo.get(inv_id)
    if record is None:
        raise HTTPException(404, f"investigation {inv_id} not found")
    return record


@router.post("", status_code=202)
async def start(request: Request, body: StartRequest) -> dict[str, Any]:
    svc = _svc(request)
    inv_id = await svc.start(
        body.question.strip(), limits=request.app.state.agent_settings.current.limits()
    )
    return {"id": inv_id, "status": "running"}


@router.get("")
async def list_investigations(
    request: Request, limit: Annotated[int, Query(ge=1, le=200)] = 50
) -> dict[str, Any]:
    rows = await _svc(request).repo.list_recent(limit)
    return {
        "investigations": [
            {
                k: r[k]
                for k in (
                    "id",
                    "question",
                    "status",
                    "severity",
                    "parent_id",
                    "reasoner",
                    "created_at",
                    "updated_at",
                )
            }
            | {"root_cause": _summary(r.get("root_cause"))}
            for r in rows
        ]
    }


def _summary(rc: dict[str, Any] | None) -> dict[str, Any] | None:
    if not rc:
        return None
    return {
        k: rc.get(k)
        for k in ("title", "category", "component", "band", "confidence", "trigger_ref")
    }


@router.get("/{inv_id}")
async def get_investigation(request: Request, inv_id: str) -> dict[str, Any]:
    svc = _svc(request)
    record = await _record(svc, inv_id)
    return {"investigation": record, "state": await svc.state(inv_id)}


@router.get("/{inv_id}/tool-calls/{evidence_id}")
async def tool_call(request: Request, inv_id: str, evidence_id: str) -> dict[str, Any]:
    svc = _svc(request)
    await _record(svc, inv_id)
    call = await svc.tool_call(inv_id, evidence_id)
    if call is None:
        raise HTTPException(404, f"tool call {evidence_id} not found in {inv_id}")
    return call


@router.post("/{inv_id}/approval")
async def decide(request: Request, inv_id: str, body: DecisionRequest) -> dict[str, Any]:
    if not body.confirm:
        raise HTTPException(
            422, "confirmation required: resend with confirm=true after the user confirmed"
        )
    svc = _svc(request)
    await _record(svc, inv_id)
    try:
        await svc.decide(
            inv_id,
            proposal_id=body.proposal_id,
            approve=body.decision == "approve",
            approver=body.approver.strip(),
            comment=body.comment,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"id": inv_id, "status": "running"}


@router.post("/{inv_id}/cancel")
async def cancel(request: Request, inv_id: str) -> dict[str, Any]:
    svc = _svc(request)
    await _record(svc, inv_id)
    await svc.cancel(inv_id)
    return {"id": inv_id, "cancel_requested": True}


@router.post("/{inv_id}/follow-ups", status_code=202)
async def follow_up(request: Request, inv_id: str, body: FollowUpRequest) -> dict[str, Any]:
    """A follow-up question starts a linked investigation with the parent's conclusion as context."""
    svc = _svc(request)
    parent = await _record(svc, inv_id)
    rc = parent.get("root_cause") or {}
    context = f"Follow-up to investigation {inv_id} ('{parent['question']}'"
    context += f"; concluded: {rc.get('title')}, {rc.get('band')} confidence)" if rc else ")"
    question = f"{body.question.strip()}\n\n[{context}]"
    new_id = await svc.start(
        question, limits=request.app.state.agent_settings.current.limits(), parent_id=inv_id
    )
    return {"id": new_id, "status": "running", "parent_id": inv_id}


@router.get("/{inv_id}/events")
async def events(
    request: Request,
    inv_id: str,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    after: Annotated[int | None, Query(ge=0)] = None,
) -> StreamingResponse:
    """Server-Sent Events. Reconnects resume after ``Last-Event-ID`` (or ``?after=``)."""
    svc = _svc(request)
    await _record(svc, inv_id)
    start = (
        after
        if after is not None
        else (int(last_event_id) if last_event_id and last_event_id.isdigit() else 0)
    )

    async def stream() -> AsyncIterator[str]:
        seq = start
        yield "retry: 3000\n\n"
        while True:
            for event in await svc.repo.events_after(inv_id, seq):
                seq = event["seq"]
                payload = {
                    "type": event["type"],
                    "data": event["payload"],
                    "at": event["created_at"].isoformat(),
                }
                yield f"id: {seq}\nevent: {event['type']}\ndata: {json.dumps(payload, default=str)}\n\n"
            record = await svc.repo.get(inv_id)
            if (
                record
                and record["status"] in (*TERMINAL, "awaiting_approval")
                and not svc.is_running(inv_id)
            ):
                pending = await svc.repo.events_after(inv_id, seq)
                if not pending:
                    yield f"event: end\ndata: {json.dumps({'status': record['status']})}\n\n"
                    return
                continue
            if await request.is_disconnected():
                return
            waiter = svc.waiter(inv_id)
            try:
                await asyncio.wait_for(waiter.wait(), timeout=HEARTBEAT_S)
            except TimeoutError:
                yield ": keep-alive\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
