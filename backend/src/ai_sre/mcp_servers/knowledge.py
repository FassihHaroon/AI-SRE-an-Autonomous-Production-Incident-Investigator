"""knowledge MCP server: hybrid retrieval over the incident knowledge base (read-only).

Every result carries its citation metadata (document id, source file, section, incident id), so
reports can cite what was actually retrieved. Retrieval runs on PostgreSQL/pgvector with local
embeddings: no external API calls.
"""

import re
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, Field

from ai_sre.knowledge.retrieval import Hit, Retriever, SearchResult
from ai_sre.knowledge.store import Filters
from ai_sre.mcp_servers.backends import BackendError
from ai_sre.mcp_servers.core import ToolFailure, ToolResult, ToolRuntime, clip, iso, tool

DocType = Literal[
    "incident",
    "root_cause_report",
    "runbook",
    "troubleshooting",
    "service_architecture",
    "architecture",
    "procedure",
    "operational",
]
_WORD = re.compile(r"[a-z0-9][a-z0-9_\-]{2,}")
_STOP = frozenset(
    [
        "the",
        "and",
        "for",
        "with",
        "from",
        "that",
        "this",
        "was",
        "were",
        "are",
        "has",
        "have",
        "not",
        "but",
        "its",
        "into",
        "after",
        "before",
    ]
)


class Citation(BaseModel):
    document_id: str
    source: str = Field(description="File in the knowledge base")
    section: str = Field(description="Section path inside the document")
    incident_id: str | None = None
    title: str


class KnowledgeHit(BaseModel):
    citation: Citation
    doc_type: str
    snippet: str
    score: float
    similarity: float | None
    services: list[str]
    severity: str | None
    category: str | None
    date: str | None
    tags: list[str]
    superseded_by: str | None = Field(
        description="If set, a newer document replaces this one: prefer it"
    )
    matched_by: list[str]


class KnowledgeSearchResult(ToolResult):
    query: str
    confident: bool = Field(
        description="False when nothing in the knowledge base is clearly relevant"
    )
    best_similarity: float | None
    term_coverage: float | None
    hits: list[KnowledgeHit]


class DocumentSection(BaseModel):
    section: str
    content: str


class KnowledgeDocumentResult(ToolResult):
    citation: Citation
    doc_type: str
    services: list[str]
    superseded_by: str | None
    sections: list[DocumentSection]


class ObservationMatch(BaseModel):
    observation: str
    supported: bool
    overlap: list[str]


class IncidentComparisonResult(ToolResult):
    incident: Citation
    category: str | None
    shared_services: list[str]
    matches: list[ObservationMatch]
    match_ratio: float


def _hit(h: Hit) -> KnowledgeHit:
    return KnowledgeHit(
        citation=Citation(**h.citation()),
        doc_type=h.doc_type,
        snippet=clip(h.snippet, 300) or "",
        score=round(h.score, 5),
        similarity=round(h.similarity, 4) if h.similarity is not None else None,
        services=h.services,
        severity=h.severity,
        category=h.category,
        date=iso(h.timestamp)[:10] if h.timestamp else None,
        tags=h.tags,
        superseded_by=h.superseded_by,
        matched_by=h.matched_by,
    )


def _result(res: SearchResult, summary: str) -> KnowledgeSearchResult:
    return KnowledgeSearchResult(
        summary=summary,
        query=res.query,
        confident=res.confident,
        best_similarity=res.best_similarity,
        term_coverage=res.term_coverage,
        hits=[_hit(h) for h in res.hits],
    )


def _terms(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP}


def build_knowledge_server(retriever_factory: Any, runtime: ToolRuntime) -> MCPServer:
    """``retriever_factory`` is an async callable returning a ready :class:`Retriever`."""
    server = MCPServer(
        "knowledge",
        instructions="Read-only knowledge base: past incidents, runbooks, procedures, "
        "service and architecture docs. Cite results by their citation.",
    )

    async def retriever() -> Retriever:
        try:
            r: Retriever = await retriever_factory()
        except Exception as exc:  # connection / model loading
            raise BackendError("knowledge", f"{type(exc).__name__}: {exc}") from exc
        return r

    @tool(server, runtime, read_only=True, title="Search knowledge base", timeout_s=40)
    async def search_knowledge(
        query: Annotated[str, Field(min_length=3, max_length=500)],
        doc_types: Annotated[
            list[DocType] | None, Field(description="Only these document types")
        ] = None,
        services: Annotated[
            list[str] | None, Field(max_length=5, description="Only documents about these services")
        ] = None,
        top_k: Annotated[int, Field(ge=1, le=10)] = 5,
        mode: Literal["hybrid", "semantic", "keyword"] = "hybrid",
    ) -> KnowledgeSearchResult:
        """Hybrid (semantic + keyword) search over runbooks, procedures, service/architecture docs,
        troubleshooting guides and incidents. Superseded documents are demoted and flagged."""
        r = await retriever()
        res = await r.search(
            query,
            top_k=top_k,
            mode=mode,
            filters=Filters(doc_types=tuple(doc_types or ()), services=tuple(services or ())),
            prefer_services=tuple(services or ()),
        )
        top = f"; top: {res.hits[0].document_id} > {res.hits[0].section}" if res.hits else ""
        return _result(res, f"{len(res.hits)} results (confident={res.confident}){top}")

    @tool(server, runtime, read_only=True, title="Find similar incidents", timeout_s=40)
    async def find_similar_incidents(
        description: Annotated[
            str,
            Field(
                min_length=3,
                max_length=800,
                description="Symptoms / evidence of the current incident",
            ),
        ],
        services: Annotated[
            list[str] | None,
            Field(max_length=5, description="Affected services (boosted, not filtered)"),
        ] = None,
        top_k: Annotated[int, Field(ge=1, le=10)] = 5,
    ) -> KnowledgeSearchResult:
        """Past incidents most similar to the current one, with incident id, category, severity and
        the matching section. `confident=false` means no past incident is clearly similar."""
        r = await retriever()
        res = await r.similar_incidents(description, services=tuple(services or ()), top_k=top_k)
        top = f"; best: {res.hits[0].incident_id} ({res.hits[0].category})" if res.hits else ""
        return _result(res, f"{len(res.hits)} similar incidents (confident={res.confident}){top}")

    @tool(server, runtime, read_only=True, title="Get knowledge document")
    async def get_knowledge_document(
        document_id: Annotated[str, Field(min_length=2, max_length=120)],
        section: Annotated[
            str | None, Field(max_length=120, description="Substring of the section path")
        ] = None,
    ) -> KnowledgeDocumentResult:
        """Full text of a knowledge document (or the matching sections), with its citation."""
        r = await retriever()
        doc = await r.store.document(document_id, section)
        if doc is None:
            raise ToolFailure(
                "not_found",
                f"document {document_id} is not in the knowledge base",
                "use search_knowledge to find document ids",
            )
        d = doc["document"]
        if not doc["sections"]:
            raise ToolFailure("not_found", f"no section matching {section!r} in {document_id}")
        return KnowledgeDocumentResult(
            summary=f"{d['id']} ({d['doc_type']}): {d['title']}, {len(doc['sections'])} section(s)",
            citation=Citation(
                document_id=d["id"],
                source=d["source"],
                section=section or "(all)",
                incident_id=d["incident_id"],
                title=d["title"],
            ),
            doc_type=d["doc_type"],
            services=list(d["services"] or []),
            superseded_by=d["superseded_by"],
            sections=[
                DocumentSection(section=s["section"], content=clip(s["content"], 1500) or "")
                for s in doc["sections"]
            ],
        )

    @tool(server, runtime, read_only=True, title="Compare with past incident")
    async def compare_with_incident(
        incident_id: Annotated[str, Field(min_length=3, max_length=40)],
        observations: Annotated[
            list[Annotated[str, Field(max_length=300)]],
            Field(min_length=1, max_length=15, description="Current evidence statements"),
        ],
        services: Annotated[list[str] | None, Field(max_length=5)] = None,
    ) -> IncidentComparisonResult:
        """Which current observations are also described in a past incident (term overlap) and
        which services they share: similarities and differences, with the incident's citation."""
        r = await retriever()
        rows = await r.store.pool().fetch(
            "SELECT id FROM knowledge_documents WHERE incident_id = $1", incident_id
        )
        if not rows:
            raise ToolFailure("not_found", f"incident {incident_id} is not in the knowledge base")
        doc = await r.store.document(rows[0]["id"], None)
        if doc is None:
            raise ToolFailure("not_found", f"incident {incident_id} was removed during the call")
        d = doc["document"]
        text_terms = _terms(d["title"] + " " + " ".join(s["content"] for s in doc["sections"]))
        matches = []
        for obs in observations:
            terms = _terms(obs)
            overlap = sorted(terms & text_terms)
            matches.append(
                ObservationMatch(
                    observation=obs,
                    supported=bool(terms) and len(overlap) / len(terms) >= 0.4,
                    overlap=overlap[:10],
                )
            )
        ratio = sum(m.supported for m in matches) / len(matches)
        shared = sorted(set(services or []) & set(d["services"] or []))
        return IncidentComparisonResult(
            summary=f"{incident_id}: {sum(m.supported for m in matches)}/{len(matches)} observations also described; "
            f"shared services: {', '.join(shared) or 'none'}",
            incident=Citation(
                document_id=d["id"],
                source=d["source"],
                section="(whole document)",
                incident_id=incident_id,
                title=d["title"],
            ),
            category=d["category"],
            shared_services=shared,
            matches=matches,
            match_ratio=round(ratio, 2),
        )

    return server
