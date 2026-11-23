"""Hybrid retrieval: semantic (pgvector cosine) + keyword (full-text) fused with RRF, then a
small metadata-aware rerank.

Why these choices (see docs/knowledge.md for the evaluation):

* **Hybrid + RRF**: semantic search finds paraphrases ("pool exhausted" ≈ "no free connections");
  keyword search nails exact identifiers (``idle in transaction``, ``order_id::text``, ``d-408``).
  Reciprocal-rank fusion needs no score calibration between the two.
* **Rerank (metadata, not a cross-encoder)**: on a corpus of this size a cross-encoder adds a
  ~100 MB model and latency for little gain; what the evaluation does show matters is (a)
  demoting superseded documents and (b) preferring the services in the question. Both are cheap
  and explainable.
* **Abstention**: RRF scores are rank-based and bge similarities sit high even for unrelated
  text, so "nothing relevant" combines the best *semantic similarity* with *vocabulary
  coverage* (share of the query's terms that occur anywhere in the corpus). Out-of-domain
  questions name things the knowledge base never mentions. Result: ``confident=False``.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Literal

from ai_sre.knowledge.embeddings import Embedder
from ai_sre.knowledge.store import Filters, KnowledgeStore

Mode = Literal["semantic", "keyword", "hybrid"]
RRF_K = 60
DEFAULT_MIN_SIMILARITY = 0.62
HIGH_SIMILARITY = 0.75  # confident regardless of vocabulary coverage
MIN_COVERAGE = 0.6  # share of query terms that must exist somewhere in the corpus
SUPERSEDED_PENALTY = 0.5
SERVICE_BOOST = 1.15


@dataclass
class Hit:
    document_id: str
    source: str
    title: str
    doc_type: str
    section: str
    snippet: str
    score: float
    similarity: float | None
    services: list[str]
    incident_id: str | None
    severity: str | None
    category: str | None
    tags: list[str]
    timestamp: datetime | None
    superseded_by: str | None
    supersedes: str | None
    matched_by: list[str] = field(default_factory=list)

    def citation(self) -> dict[str, Any]:
        """What a report may cite: only fields that came from the store."""
        return {
            "document_id": self.document_id,
            "source": self.source,
            "section": self.section,
            "incident_id": self.incident_id,
            "title": self.title,
        }


@dataclass
class SearchResult:
    query: str
    mode: Mode
    hits: list[Hit]
    best_similarity: float | None
    confident: bool
    term_coverage: float | None = None


def _hit(row: dict[str, Any]) -> Hit:
    return Hit(
        document_id=row["id"],
        source=row["source"],
        title=row["title"],
        doc_type=row["doc_type"],
        section=row["section"],
        snippet=row["content"][:400],
        score=0.0,
        similarity=float(row["similarity"]) if row.get("similarity") is not None else None,
        services=list(row["services"] or []),
        incident_id=row["incident_id"],
        severity=row["severity"],
        category=row["category"],
        tags=list(row["tags"] or []),
        timestamp=row["doc_timestamp"],
        superseded_by=row["superseded_by"],
        supersedes=row["supersedes"],
    )


def rrf(rankings: list[list[str]], k: int = RRF_K) -> dict[str, float]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, key in enumerate(ranking):
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
    return scores


class Retriever:
    def __init__(
        self,
        store: KnowledgeStore,
        embedder: Embedder,
        *,
        min_similarity: float = DEFAULT_MIN_SIMILARITY,
    ) -> None:
        self.store = store
        self.embedder = embedder
        self.min_similarity = min_similarity

    async def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        mode: Mode = "hybrid",
        filters: Filters | None = None,
        rerank: bool = True,
        prefer_services: tuple[str, ...] = (),
        per_document: bool = True,
        candidates: int = 40,
    ) -> SearchResult:
        filters = filters or Filters()
        by_chunk: dict[int, Hit] = {}
        rankings: list[list[str]] = []
        best_sim: float | None = None
        if mode in ("semantic", "hybrid"):
            rows = await self.store.semantic(self.embedder.embed_query(query), filters, candidates)
            for r in rows:
                by_chunk.setdefault(r["chunk_id"], _hit(r)).matched_by.append("semantic")
            rankings.append([str(r["chunk_id"]) for r in rows])
            best_sim = max((float(r["similarity"]) for r in rows), default=None)
        if mode in ("keyword", "hybrid"):
            rows = await self.store.keyword(query, filters, candidates)
            for r in rows:
                by_chunk.setdefault(r["chunk_id"], _hit(r)).matched_by.append("keyword")
            rankings.append([str(r["chunk_id"]) for r in rows])
        fused = rrf(rankings)
        hits = [replace(h, score=fused[str(cid)]) for cid, h in by_chunk.items()]
        if rerank:
            for h in hits:
                if h.superseded_by:
                    h.score *= SUPERSEDED_PENALTY
                if prefer_services and set(prefer_services) & set(h.services):
                    h.score *= SERVICE_BOOST
        hits.sort(
            key=lambda h: (h.score, h.timestamp.timestamp() if h.timestamp else 0.0), reverse=True
        )
        if per_document:
            seen: set[str] = set()
            unique = []
            for h in hits:
                if h.document_id not in seen:
                    seen.add(h.document_id)
                    unique.append(h)
            hits = unique
        known, total = await self.store.vocabulary_coverage(query)
        coverage = known / total if total else 0.0
        if mode == "keyword":
            confident = bool(hits) and coverage >= MIN_COVERAGE
        else:
            sim = best_sim or 0.0
            confident = sim >= HIGH_SIMILARITY or (
                sim >= self.min_similarity and coverage >= MIN_COVERAGE
            )
        return SearchResult(
            query=query,
            mode=mode,
            hits=hits[:top_k],
            best_similarity=best_sim,
            confident=confident,
            term_coverage=round(coverage, 2),
        )

    async def similar_incidents(
        self, query: str, *, services: tuple[str, ...] = (), top_k: int = 5
    ) -> SearchResult:
        """Past incidents (incident and root-cause documents) similar to the description. Services
        boost rather than filter: a similar failure in a neighbouring service is still useful."""
        return await self.search(
            query,
            top_k=top_k,
            filters=Filters(doc_types=("incident", "root_cause_report")),
            prefer_services=services,
        )
