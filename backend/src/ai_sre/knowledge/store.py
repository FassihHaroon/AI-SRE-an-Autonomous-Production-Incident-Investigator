"""PostgreSQL + pgvector storage for knowledge documents and chunks."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import asyncpg

from ai_sre.knowledge import KNOWLEDGE_DDL
from ai_sre.knowledge.chunking import Chunk
from ai_sre.knowledge.documents import Document


@dataclass(frozen=True)
class Filters:
    doc_types: tuple[str, ...] = ()
    services: tuple[str, ...] = ()  # document mentions ANY of these services
    environment: str | None = None
    severity: tuple[str, ...] = ()
    incident_id: str | None = None
    tags: tuple[str, ...] = ()  # ANY of these tags
    since: datetime | None = None
    include_superseded: bool = True

    def sql(self, start: int) -> tuple[str, list[Any]]:
        """WHERE fragment over alias ``d`` with positional args starting at ``$start``."""
        clauses: list[str] = []
        args: list[Any] = []

        def arg(value: Any) -> str:
            args.append(value)
            return f"${start + len(args) - 1}"

        if self.doc_types:
            clauses.append(f"d.doc_type = ANY({arg(list(self.doc_types))}::text[])")
        if self.services:
            clauses.append(f"d.services && {arg(list(self.services))}::text[]")
        if self.environment:
            clauses.append(f"d.environment = {arg(self.environment)}")
        if self.severity:
            clauses.append(f"d.severity = ANY({arg(list(self.severity))}::text[])")
        if self.incident_id:
            clauses.append(f"d.incident_id = {arg(self.incident_id)}")
        if self.tags:
            clauses.append(f"d.tags && {arg(list(self.tags))}::text[]")
        if self.since:
            clauses.append(f"d.doc_timestamp >= {arg(self.since)}")
        if not self.include_superseded:
            clauses.append(
                "NOT EXISTS (SELECT 1 FROM knowledge_documents s WHERE s.supersedes = d.id)"
            )
        return (" AND ".join(clauses) or "TRUE"), args


def _vec(values: list[float]) -> str:
    return "[" + ",".join(f"{v:.7f}" for v in values) + "]"


DOC_COLUMNS = (
    "d.id, d.source, d.doc_type, d.title, d.services, d.environment, d.incident_id, d.severity, "
    "d.category, d.tags, d.doc_timestamp, d.supersedes, "
    "(SELECT s.id FROM knowledge_documents s WHERE s.supersedes = d.id LIMIT 1) AS superseded_by"
)


class KnowledgeStore:
    def __init__(self, dsn: str, *, schema: str | None = None) -> None:
        self._dsn = dsn
        self._schema = schema
        self._pool: asyncpg.Pool | None = None

    async def open(self) -> KnowledgeStore:
        settings = {"search_path": f"{self._schema}, public"} if self._schema else None
        self._pool = await asyncpg.create_pool(
            self._dsn, min_size=1, max_size=4, server_settings=settings
        )
        return self

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()

    def pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("KnowledgeStore is not open")
        return self._pool

    async def create_schema(self) -> None:
        """Create tables in the current search path (tests; production uses Alembic)."""
        async with self.pool().acquire() as conn:
            if self._schema:
                await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{self._schema}"')
            await conn.execute(KNOWLEDGE_DDL)

    # ------------------------------------------------------------------ documents
    async def get_by_source(self, source: str) -> dict[str, Any] | None:
        row = await self.pool().fetchrow(
            "SELECT id, content_hash, embedding_model FROM knowledge_documents WHERE source = $1",
            source,
        )
        return dict(row) if row else None

    async def get_by_id(self, doc_id: str) -> dict[str, Any] | None:
        row = await self.pool().fetchrow(
            "SELECT id, source, content_hash FROM knowledge_documents WHERE id = $1", doc_id
        )
        return dict(row) if row else None

    async def find_by_hash(self, content_hash: str) -> str | None:
        value: str | None = await self.pool().fetchval(
            "SELECT source FROM knowledge_documents WHERE content_hash = $1", content_hash
        )
        return value

    async def sources(self) -> dict[str, str]:
        rows = await self.pool().fetch("SELECT source, id FROM knowledge_documents")
        return {r["source"]: r["id"] for r in rows}

    async def upsert(
        self, doc: Document, chunks: list[Chunk], vectors: list[list[float]], model: str
    ) -> None:
        async with self.pool().acquire() as conn, conn.transaction():
            await conn.execute(
                "DELETE FROM knowledge_documents WHERE id = $1 OR source = $2", doc.id, doc.source
            )
            await conn.execute(
                "INSERT INTO knowledge_documents (id, source, doc_type, title, services, environment, incident_id, "
                "severity, category, tags, doc_timestamp, supersedes, content_hash, embedding_model, metadata) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)",
                doc.id,
                doc.source,
                doc.doc_type,
                doc.title,
                doc.services,
                doc.environment,
                doc.incident_id,
                doc.severity,
                doc.category,
                doc.tags,
                doc.timestamp,
                doc.supersedes,
                doc.content_hash,
                model,
                json.dumps(doc.extra, default=str),
            )
            await conn.executemany(
                "INSERT INTO knowledge_chunks (document_id, chunk_index, section, content, embedding) "
                "VALUES ($1, $2, $3, $4, $5::vector)",
                [
                    (doc.id, c.index, c.section, c.content, _vec(v))
                    for c, v in zip(chunks, vectors, strict=True)
                ],
            )

    async def delete(self, doc_id: str) -> bool:
        result: str = await self.pool().execute(
            "DELETE FROM knowledge_documents WHERE id = $1", doc_id
        )
        return result.endswith(" 1")

    async def counts(self) -> dict[str, int]:
        docs = await self.pool().fetchval("SELECT count(*) FROM knowledge_documents")
        chunks = await self.pool().fetchval("SELECT count(*) FROM knowledge_chunks")
        return {"documents": int(docs), "chunks": int(chunks)}

    # ------------------------------------------------------------------ search
    async def semantic(
        self, query_vec: list[float], filters: Filters, limit: int
    ) -> list[dict[str, Any]]:
        where, args = filters.sql(3)
        rows = await self.pool().fetch(
            f"SELECT c.id AS chunk_id, c.section, c.content, 1 - (c.embedding <=> $1::vector) AS similarity, "  # noqa: S608
            f"{DOC_COLUMNS} FROM knowledge_chunks c JOIN knowledge_documents d ON d.id = c.document_id "
            f"WHERE {where} ORDER BY c.embedding <=> $1::vector LIMIT $2",
            _vec(query_vec),
            limit,
            *args,
        )
        return [dict(r) for r in rows]

    async def keyword(self, query: str, filters: Filters, limit: int) -> list[dict[str, Any]]:
        where, args = filters.sql(3)
        # OR-semantics over the query's terms: incident questions rarely contain every word.
        rows = await self.pool().fetch(
            "WITH q AS (SELECT to_tsquery('english', string_agg(lexeme, ' | ')) AS tsq "  # noqa: S608
            "           FROM unnest(to_tsvector('english', $1)) AS t(lexeme, positions, weights)) "
            f"SELECT c.id AS chunk_id, c.section, c.content, ts_rank_cd(c.tsv, q.tsq) AS rank, {DOC_COLUMNS} "
            "FROM knowledge_chunks c JOIN knowledge_documents d ON d.id = c.document_id, q "
            f"WHERE q.tsq IS NOT NULL AND c.tsv @@ q.tsq AND {where} ORDER BY rank DESC LIMIT $2",
            query,
            limit,
            *args,
        )
        return [dict(r) for r in rows]

    async def list_documents(self, filters: Filters, limit: int = 200) -> list[dict[str, Any]]:
        where, args = filters.sql(2)
        rows = await self.pool().fetch(
            f"SELECT {DOC_COLUMNS}, d.metadata FROM knowledge_documents d WHERE {where} "  # noqa: S608 - parameterised
            "ORDER BY d.doc_timestamp DESC NULLS LAST LIMIT $1",
            limit,
            *args,
        )
        return [dict(r) for r in rows]

    async def vocabulary_coverage(self, query: str) -> tuple[int, int]:
        """(query terms present anywhere in the corpus, query terms). Out-of-domain questions
        mention things the knowledge base never talks about (e.g. 'kafka', 'certificate')."""
        rows = await self.pool().fetch(
            "SELECT t.lexeme, EXISTS (SELECT 1 FROM knowledge_chunks c WHERE c.tsv @@ quote_literal(t.lexeme)::tsquery) AS known "
            "FROM unnest(to_tsvector('english', $1)) AS t(lexeme, positions, weights)",
            query,
        )
        return sum(1 for r in rows if r["known"]), len(rows)

    async def document(self, doc_id: str, section: str | None) -> dict[str, Any] | None:
        doc = await self.pool().fetchrow(
            f"SELECT {DOC_COLUMNS} FROM knowledge_documents d WHERE d.id = $1",  # noqa: S608 - constant columns
            doc_id,
        )
        if doc is None:
            return None
        rows = await self.pool().fetch(
            "SELECT section, content FROM knowledge_chunks WHERE document_id = $1 "
            "AND ($2::text IS NULL OR section ILIKE '%' || $2 || '%') ORDER BY chunk_index",
            doc_id,
            section,
        )
        return {"document": dict(doc), "sections": [dict(r) for r in rows]}
