"""Incident history (knowledge base) and settings endpoints."""

from __future__ import annotations

from typing import Annotated, Any

import httpx
from fastapi import APIRouter, HTTPException, Query, Request

from ai_sre.knowledge.store import Filters
from ai_sre.services.settings_store import AgentSettings

knowledge = APIRouter(prefix="/knowledge", tags=["knowledge"])
settings_router = APIRouter(prefix="/settings", tags=["settings"])

INCIDENT_TYPES = ("incident", "root_cause_report")


async def _retriever(request: Request) -> Any:
    try:
        return await request.app.state.retriever()
    except Exception as exc:
        raise HTTPException(
            503, f"knowledge base unavailable: {type(exc).__name__}: {exc}"
        ) from exc


def _incident_row(d: dict[str, Any]) -> dict[str, Any]:
    return {
        "document_id": d["id"],
        "incident_id": d["incident_id"],
        "title": d["title"],
        "source": d["source"],
        "severity": d["severity"],
        "category": d["category"],
        "services": list(d["services"] or []),
        "tags": list(d["tags"] or []),
        "date": d["doc_timestamp"].date().isoformat() if d.get("doc_timestamp") else None,
    }


@knowledge.get("/incidents")
async def incidents(
    request: Request,
    q: Annotated[str | None, Query(max_length=300)] = None,
    service: str | None = None,
    severity: str | None = None,
    category: str | None = None,
) -> dict[str, Any]:
    r = await _retriever(request)
    filters = Filters(
        doc_types=INCIDENT_TYPES,
        services=(service,) if service else (),
        severity=(severity,) if severity else (),
    )
    if q:
        res = await r.search(q, top_k=20, filters=filters)
        rows = [
            {
                **_incident_row(
                    {
                        "id": h.document_id,
                        "incident_id": h.incident_id,
                        "title": h.title,
                        "source": h.source,
                        "severity": h.severity,
                        "category": h.category,
                        "services": h.services,
                        "tags": h.tags,
                        "doc_timestamp": h.timestamp,
                    }
                ),
                "score": round(h.score, 4),
                "section": h.section,
                "snippet": h.snippet,
            }
            for h in res.hits
        ]
        rows = [x for x in rows if not category or x["category"] == category]
        return {"query": q, "confident": res.confident, "incidents": rows}
    docs = await r.store.list_documents(filters)
    rows = [_incident_row(d) for d in docs if not category or d["category"] == category]
    return {"query": None, "confident": None, "incidents": rows}


@knowledge.get("/incidents/{document_id}")
async def incident(request: Request, document_id: str) -> dict[str, Any]:
    r = await _retriever(request)
    doc = await r.store.document(document_id, None)
    if doc is None:
        raise HTTPException(404, f"{document_id} not found in the knowledge base")
    d = doc["document"]
    text = d["title"] + " " + " ".join(s["content"] for s in doc["sections"][:3])
    similar = await r.similar_incidents(text[:800], services=tuple(d["services"] or ()), top_k=6)
    return {
        "incident": _incident_row(d),
        "sections": doc["sections"],
        "similar": [
            {
                "document_id": h.document_id,
                "incident_id": h.incident_id,
                "title": h.title,
                "category": h.category,
                "similarity": h.similarity,
                "section": h.section,
            }
            for h in similar.hits
            if h.document_id != document_id
        ][:5],
    }


@settings_router.get("")
async def get_settings_view(request: Request) -> dict[str, Any]:
    core = request.app.state.settings
    mcp = request.app.state.mcp_settings
    registry: dict[str, Any] | None = None
    try:
        async with httpx.AsyncClient(timeout=5) as http:
            resp = await http.get(
                f"{request.app.state.mcp_url}/registry",
                headers={"Authorization": f"Bearer {mcp.read_token.get_secret_value()}"},
            )
            registry = resp.json() if resp.status_code == 200 else None
    except httpx.HTTPError:
        registry = None
    return {
        "environment": {
            "name": core.environment,
            "simulated": True,
            "version": request.app.version,
        },
        "llm": {
            "provider": core.llm_provider,
            "model": core.gemini_model,
            "api_key_configured": core.llm_configured,
        },
        "agent": request.app.state.agent_settings.current.model_dump(),
        "integrations": {
            "mcp": {
                "url": request.app.state.mcp_url,
                "reachable": registry is not None,
                "servers": [
                    {
                        "name": s["name"],
                        "enabled": s["enabled"],
                        "read_only": s["read_only"],
                        "tools": len(s["tools"]),
                    }
                    for s in (registry or {}).get("servers", [])
                ],
            },
            "prometheus": mcp.prometheus_url,
            "loki": mcp.loki_url,
            "deployer": mcp.deployer_url,
            "remediation_enabled": next(
                (
                    s["enabled"]
                    for s in (registry or {}).get("servers", [])
                    if s["name"] == "remediation"
                ),
                None,
            ),
            "checkpointer": request.app.state.checkpointer_kind,
            "event_store": request.app.state.repo_kind,
        },
    }


@settings_router.put("/agent")
async def put_agent_settings(request: Request, body: AgentSettings) -> dict[str, Any]:
    if body.reasoner == "llm" and not request.app.state.settings.llm_configured:
        raise HTTPException(422, "the LLM reasoner needs an API key (AI_SRE_GEMINI_API_KEY)")
    saved: AgentSettings = await request.app.state.agent_settings.save(body)
    return saved.model_dump()
