"""incidents MCP server: historical incident (postmortem) lookup and keyword search.

Postmortems are Markdown files with YAML front matter in ``knowledge/incidents``. Search is
weighted keyword matching for now; Phase 6 replaces it with hybrid retrieval (pgvector + FTS)
behind the same tool contract.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

import yaml
from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, Field

from ai_sre.mcp_servers.core import ToolFailure, ToolResult, ToolRuntime, clip, tool

_WORD = re.compile(r"[a-z0-9][a-z0-9+_-]*")
_STOP = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "at",
        "be",
        "by",
        "for",
        "from",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "the",
        "to",
        "was",
        "were",
        "with",
        "why",
        "our",
        "has",
        "have",
    ]
)


@dataclass(frozen=True)
class Incident:
    id: str
    title: str
    date: str
    severity: str
    services: list[str]
    category: str
    duration_minutes: int
    tags: list[str]
    body: str

    def section(self, heading: str) -> str:
        match = re.search(rf"## {heading}\n\n(.*?)(?:\n## |\Z)", self.body, re.S)
        return match.group(1).strip() if match else ""


def load_incidents(directory: Path) -> dict[str, Incident]:
    incidents: dict[str, Incident] = {}
    for path in sorted(directory.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        _, front, body = text.split("---", 2)
        meta: dict[str, Any] = yaml.safe_load(front)
        incidents[meta["id"]] = Incident(
            id=meta["id"],
            title=meta["title"],
            date=str(meta["date"]),
            severity=meta["severity"],
            services=[str(s) for s in meta["services"]],
            category=meta["category"],
            duration_minutes=int(meta["duration_minutes"]),
            tags=[str(t) for t in meta.get("tags", [])],
            body=body.strip(),
        )
    return incidents


def tokens(text: str) -> list[str]:
    return [t for t in _WORD.findall(text.lower()) if t not in _STOP]


def score(incident: Incident, query: list[str]) -> float:
    fields = (
        (incident.title, 3.0),
        (" ".join(incident.tags), 3.0),
        (incident.category.replace("_", " "), 2.0),
        (" ".join(incident.services), 2.0),
        (incident.body, 1.0),
    )
    total = 0.0
    for text, weight in fields:
        words = tokens(text)
        if words:
            total += (
                weight * sum(min(words.count(q), 3) for q in query) / (1 + len(words) ** 0.5 / 10)
            )
    return round(total, 3)


class IncidentSummary(BaseModel):
    id: str
    title: str
    date: str
    severity: str
    services: list[str]
    category: str
    duration_minutes: int
    tags: list[str]


class IncidentListResult(ToolResult):
    total: int
    offset: int
    incidents: list[IncidentSummary]
    next_offset: int | None


class IncidentDetailsResult(ToolResult):
    incident: IncidentSummary
    what_happened: str
    detection: str
    resolution: str


class IncidentHit(BaseModel):
    incident: IncidentSummary
    score: float
    snippet: str


class IncidentSearchResult(ToolResult):
    query: str
    hits: list[IncidentHit]


def _summary(i: Incident) -> IncidentSummary:
    return IncidentSummary(
        id=i.id,
        title=i.title,
        date=i.date,
        severity=i.severity,
        services=i.services,
        category=i.category,
        duration_minutes=i.duration_minutes,
        tags=i.tags,
    )


def build_incidents_server(directory: Path, runtime: ToolRuntime) -> MCPServer:
    server = MCPServer("incidents", instructions="Read-only historical incidents (postmortems).")
    incidents = load_incidents(directory)

    @tool(server, runtime, read_only=True, title="List historical incidents")
    async def list_historical_incidents(
        service: Annotated[
            str | None, Field(max_length=60, description="Only incidents involving this service")
        ] = None,
        category: Annotated[str | None, Field(max_length=40, description="e.g. bad_deploy")] = None,
        limit: Annotated[int, Field(ge=1, le=50)] = 20,
        offset: Annotated[int, Field(ge=0, le=10_000)] = 0,
    ) -> IncidentListResult:
        """Past incidents, newest first, optionally filtered by service or category (paginated)."""
        rows = sorted(incidents.values(), key=lambda i: i.date, reverse=True)
        if service:
            rows = [i for i in rows if service in i.services]
        if category:
            rows = [i for i in rows if i.category == category]
        page = rows[offset : offset + limit]
        return IncidentListResult(
            summary=f"{len(page)} of {len(rows)} incidents",
            total=len(rows),
            offset=offset,
            incidents=[_summary(i) for i in page],
            next_offset=offset + limit if offset + limit < len(rows) else None,
        )

    @tool(server, runtime, read_only=True, title="Get incident details")
    async def get_incident_details(
        incident_id: Annotated[
            str, Field(pattern=r"^INC-\d{4}-\d{3}$", description="e.g. INC-2026-031")
        ],
    ) -> IncidentDetailsResult:
        """Full postmortem of one past incident: what happened, how it was diagnosed, resolution."""
        incident = incidents.get(incident_id)
        if incident is None:
            raise ToolFailure(
                "not_found",
                f"incident {incident_id} does not exist",
                f"known ids: {', '.join(sorted(incidents))}",
            )
        return IncidentDetailsResult(
            summary=f"{incident.id} ({incident.date}, {incident.severity}): {incident.title}",
            incident=_summary(incident),
            what_happened=clip(incident.section("What happened"), 2000) or "",
            detection=clip(incident.section("How it was detected and diagnosed"), 2000) or "",
            resolution=clip(incident.section("Resolution"), 2000) or "",
        )

    @tool(server, runtime, read_only=True, title="Search incident history")
    async def search_incident_history(
        query: Annotated[
            str, Field(min_length=2, max_length=300, description="Symptoms or keywords")
        ],
        service: Annotated[str | None, Field(max_length=60)] = None,
        top_k: Annotated[int, Field(ge=1, le=10)] = 5,
    ) -> IncidentSearchResult:
        """Find similar past incidents by symptoms (e.g. 'connection pool timeouts after deploy')."""
        q = tokens(query)
        if not q:
            raise ToolFailure("invalid_argument", "query has no searchable words")
        candidates = [i for i in incidents.values() if not service or service in i.services]
        ranked = sorted(((score(i, q), i) for i in candidates), key=lambda x: x[0], reverse=True)
        hits = [
            IncidentHit(
                incident=_summary(i), score=s, snippet=clip(i.section("What happened"), 300) or ""
            )
            for s, i in ranked[:top_k]
            if s > 0
        ]
        best = f"; best: {hits[0].incident.id} {hits[0].incident.title!r}" if hits else ""
        return IncidentSearchResult(
            summary=f"{len(hits)} matching incidents{best}", query=query, hits=hits
        )

    return server
