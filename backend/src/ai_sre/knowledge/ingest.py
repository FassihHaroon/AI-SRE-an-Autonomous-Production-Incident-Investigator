"""Ingestion pipeline: load → parse → metadata → chunk → embed → store.

Idempotent and incremental:

* unchanged documents (same source, same content hash, same embedding model) → skipped, 0 writes;
* changed documents → their chunks are replaced in one transaction;
* the same content under another path → reported as a duplicate and not stored twice;
* ``prune=True`` deletes documents whose source file disappeared; ``delete(doc_id)`` removes one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import structlog

from ai_sre.knowledge.chunking import chunk_document
from ai_sre.knowledge.documents import DocumentError, discover, load_document
from ai_sre.knowledge.embeddings import Embedder
from ai_sre.knowledge.store import KnowledgeStore

logger = structlog.get_logger("knowledge.ingest")


@dataclass
class IngestReport:
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    chunks_written: int = 0

    @property
    def writes(self) -> int:
        return len(self.added) + len(self.updated) + len(self.deleted)


class Ingestor:
    def __init__(self, store: KnowledgeStore, embedder: Embedder, *, max_chars: int = 900) -> None:
        self.store = store
        self.embedder = embedder
        self.max_chars = max_chars

    async def ingest_dir(self, root: Path, *, prune: bool = False) -> IngestReport:
        report = IngestReport()
        seen: set[str] = set()
        for path in discover(root):
            rel = path.relative_to(root).as_posix()
            seen.add(rel)
            try:
                doc = load_document(path, root)
            except DocumentError as exc:
                report.errors.append(str(exc))
                logger.warning("knowledge_document_rejected", error=str(exc))
                continue
            existing = await self.store.get_by_source(rel)
            if (
                existing
                and existing["content_hash"] == doc.content_hash
                and existing["embedding_model"] == self.embedder.name
            ):
                report.unchanged.append(doc.id)
                continue
            duplicate_of = await self.store.find_by_hash(doc.content_hash)
            if duplicate_of and duplicate_of != rel:
                report.duplicates.append(f"{rel} (same content as {duplicate_of})")
                continue
            clash = await self.store.get_by_id(doc.id)
            if clash and clash["source"] != rel:
                report.errors.append(
                    f"{rel}: document id {doc.id!r} already used by {clash['source']}"
                )
                continue
            chunks = chunk_document(doc.title, doc.body, self.max_chars)
            vectors = self.embedder.embed_documents([c.embedding_text(doc.title) for c in chunks])
            await self.store.upsert(doc, chunks, vectors, self.embedder.name)
            (report.updated if existing else report.added).append(doc.id)
            report.chunks_written += len(chunks)
        if prune:
            for source, doc_id in (await self.store.sources()).items():
                if source not in seen:
                    await self.store.delete(doc_id)
                    report.deleted.append(doc_id)
        logger.info(
            "knowledge_ingested",
            added=len(report.added),
            updated=len(report.updated),
            unchanged=len(report.unchanged),
            duplicates=len(report.duplicates),
            deleted=len(report.deleted),
            errors=len(report.errors),
        )
        return report


logging.getLogger("fastembed").setLevel(logging.WARNING)
