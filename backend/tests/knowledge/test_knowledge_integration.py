"""Knowledge base against PostgreSQL + pgvector (run: uv run poe test-integration).

Each test module works in a throwaway schema, so the real knowledge base is never touched.
"""

from __future__ import annotations

import secrets
import shutil
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import asyncpg
import pytest
from mcp import Client

from ai_sre.config import REPO_ROOT, get_settings
from ai_sre.knowledge.documents import discover
from ai_sre.knowledge.embeddings import FastEmbedEmbedder, HashingEmbedder
from ai_sre.knowledge.evaluation import evaluate_all
from ai_sre.knowledge.ingest import Ingestor
from ai_sre.knowledge.retrieval import Retriever
from ai_sre.knowledge.store import Filters, KnowledgeStore
from ai_sre.mcp_servers.core import ToolRuntime
from ai_sre.mcp_servers.knowledge import build_knowledge_server

pytestmark = pytest.mark.integration
KB = REPO_ROOT / "knowledge"


def _dsn() -> str:
    return get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def store() -> AsyncIterator[KnowledgeStore]:
    schema = f"kb_test_{secrets.token_hex(4)}"
    s = KnowledgeStore(_dsn(), schema=schema)
    try:
        await s.open()
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.skip(f"Postgres not reachable: {exc}")
    await s.create_schema()
    yield s
    await s.pool().execute(f'DROP SCHEMA "{schema}" CASCADE')
    await s.close()


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    dest = tmp_path / "knowledge"
    shutil.copytree(KB, dest)
    return dest


# --------------------------------------------------------------------------- ingestion lifecycle
async def test_ingest_is_idempotent(store: KnowledgeStore, corpus: Path) -> None:
    ingestor = Ingestor(store, HashingEmbedder())
    first = await ingestor.ingest_dir(corpus)
    assert first.errors == []
    assert len(first.added) == len(discover(corpus))
    counts = await store.counts()
    assert counts["documents"] == len(first.added)
    assert counts["chunks"] == first.chunks_written
    second = await ingestor.ingest_dir(corpus)
    assert second.writes == 0
    assert len(second.unchanged) == len(first.added)


async def test_update_replaces_chunks(store: KnowledgeStore, corpus: Path) -> None:
    ingestor = Ingestor(store, HashingEmbedder())
    await ingestor.ingest_dir(corpus)
    path = corpus / "runbooks" / "rb-redis-outage.md"
    path.write_text(
        path.read_text(encoding="utf-8")
        + "\n## Escalation\n\nPage the platform team via the zebracorn bridge.\n",
        encoding="utf-8",
    )
    report = await ingestor.ingest_dir(corpus)
    assert report.updated == ["rb-redis-outage"]
    assert report.writes == 1
    hits = await store.keyword("zebracorn", Filters(), 5)
    assert [h["id"] for h in hits] == ["rb-redis-outage"]
    doc = await store.document("rb-redis-outage", "Escalation")
    assert doc is not None
    assert len(doc["sections"]) == 1


async def test_duplicates_are_not_stored_twice(store: KnowledgeStore, corpus: Path) -> None:
    ingestor = Ingestor(store, HashingEmbedder())
    await ingestor.ingest_dir(corpus)
    shutil.copy(corpus / "incidents" / "INC-2026-044.md", corpus / "incidents" / "copy-of-044.md")
    report = await ingestor.ingest_dir(corpus)
    assert report.duplicates == [
        "incidents/copy-of-044.md (same content as incidents/INC-2026-044.md)"
    ]
    assert report.writes == 0


async def test_prune_and_explicit_delete(store: KnowledgeStore, corpus: Path) -> None:
    ingestor = Ingestor(store, HashingEmbedder())
    await ingestor.ingest_dir(corpus)
    before = await store.counts()
    (corpus / "runbooks" / "rb-memory-leak.md").unlink()
    report = await ingestor.ingest_dir(corpus, prune=True)
    assert report.deleted == ["rb-memory-leak"]
    assert await store.delete("rb-payments-provider")
    assert not await store.delete("rb-payments-provider")
    after = await store.counts()
    assert after["documents"] == before["documents"] - 2
    assert after["chunks"] < before["chunks"]
    orphan = await store.pool().fetchval(
        "SELECT count(*) FROM knowledge_chunks WHERE document_id IN ('rb-memory-leak', 'rb-payments-provider')"
    )
    assert orphan == 0, "chunks are deleted with their document"


async def test_invalid_documents_are_reported_not_ingested(
    store: KnowledgeStore, corpus: Path
) -> None:
    (corpus / "runbooks" / "broken.md").write_text(
        '---\ntype: "novel"\n---\ntext', encoding="utf-8"
    )
    report = await Ingestor(store, HashingEmbedder()).ingest_dir(corpus)
    assert len(report.errors) == 1
    assert "unknown document type" in report.errors[0]


async def test_metadata_filters(store: KnowledgeStore, corpus: Path) -> None:
    await Ingestor(store, HashingEmbedder()).ingest_dir(corpus)
    vec = HashingEmbedder().embed_query("incident")

    async def ids(f: Filters) -> set[str]:
        return {r["id"] for r in await store.semantic(vec, f, 200)}

    assert await ids(Filters(doc_types=("runbook",))) >= {
        "rb-redis-outage",
        "rb-db-pool-exhaustion",
    }
    assert all(i.startswith("rb-") for i in await ids(Filters(doc_types=("runbook",))))
    assert await ids(Filters(services=("payments-provider",), doc_types=("runbook",))) == set()
    assert "svc-fulfilment-worker" in await ids(
        Filters(services=("fulfilment-worker",), doc_types=("service_architecture",))
    )
    assert await ids(Filters(incident_id="INC-2026-058")) == {"INC-2026-058"}
    assert await ids(Filters(severity=("SEV3",), doc_types=("incident",))) == {
        "INC-2026-052",
        "INC-2026-071",
        "INC-2026-085",
        "INC-2025-140",
    }
    assert "rb-worker-backlog" in await ids(Filters(tags=("dead-letter",)))
    assert "rb-db-pool-exhaustion-2024" not in await ids(
        Filters(since=datetime(2025, 1, 1, tzinfo=UTC))
    )
    assert "rb-db-pool-exhaustion-2024" not in await ids(Filters(include_superseded=False))


# --------------------------------------------------------------------------- retrieval quality
@pytest.fixture
async def semantic_store(store: KnowledgeStore, corpus: Path) -> KnowledgeStore:
    report = await Ingestor(store, FastEmbedEmbedder()).ingest_dir(corpus)
    assert report.errors == []
    return store


async def test_retrieval_quality_gates(semantic_store: KnowledgeStore) -> None:
    out = await evaluate_all(
        Retriever(semantic_store, FastEmbedEmbedder()),
        REPO_ROOT / "evals" / "retrieval" / "queries.yaml",
    )
    best = next(r for r in out["results"] if r.mode == "hybrid+rerank")
    assert best.recall >= 0.9
    assert best.mrr >= 0.9
    assert best.ndcg >= 0.9
    assert best.conflicts_ok == 1.0, "superseded runbook must never outrank its replacement"
    assert best.abstention_ok is not None
    assert best.abstention_ok >= 0.75
    semantic = next(r for r in out["results"] if r.mode == "semantic")
    keyword = next(r for r in out["results"] if r.mode == "keyword")
    assert best.mrr >= max(semantic.mrr, keyword.mrr), (
        "hybrid+rerank is at least as good as either alone"
    )


async def test_similar_incidents_service_boost_and_no_match(semantic_store: KnowledgeStore) -> None:
    r = Retriever(semantic_store, FastEmbedEmbedder())
    res = await r.similar_incidents(
        "pool exhausted with sessions idle in transaction after a feature flag change",
        services=("orders-service",),
    )
    assert res.confident
    assert res.hits[0].incident_id == "INC-2026-063"
    assert all(h.doc_type == "incident" for h in res.hits)
    none = await r.similar_incidents("kafka consumer lag in the analytics cluster")
    assert not none.confident


# --------------------------------------------------------------------------- MCP tools (protocol)
async def test_knowledge_mcp_tools(semantic_store: KnowledgeStore) -> None:
    retriever = Retriever(semantic_store, FastEmbedEmbedder())

    async def factory() -> Retriever:
        return retriever

    server = build_knowledge_server(factory, ToolRuntime(30, 16000))
    async with Client(server) as client:
        res = (
            await client.call_tool(
                "search_knowledge", {"query": "redis down what to do", "doc_types": ["runbook"]}
            )
        ).structured_content
        assert res is not None
        top = res["hits"][0]
        assert top["citation"] == {
            "document_id": "rb-redis-outage",
            "source": "runbooks/rb-redis-outage.md",
            "section": top["citation"]["section"],
            "incident_id": None,
            "title": "Runbook: Redis unavailable",
        }
        assert top["citation"]["section"].startswith("Runbook: Redis (shop-cache) unavailable")

        sim = (
            await client.call_tool(
                "find_similar_incidents",
                {
                    "description": "payment provider returns 503, orders fail",
                    "services": ["orders-service"],
                },
            )
        ).structured_content
        assert sim is not None
        assert sim["hits"][0]["citation"]["incident_id"] == "INC-2026-058"

        doc = (
            await client.call_tool(
                "get_knowledge_document",
                {"document_id": "rb-db-pool-exhaustion", "section": "Remediation"},
            )
        ).structured_content
        assert doc is not None
        assert "roll back" in doc["sections"][0]["content"]
        assert doc["citation"]["source"] == "runbooks/rb-db-pool-exhaustion.md"

        cmp = (
            await client.call_tool(
                "compare_with_incident",
                {
                    "incident_id": "INC-2026-031",
                    "observations": [
                        "connection pool saturated with requests waiting",
                        "kafka partitions rebalanced",
                    ],
                    "services": ["orders-service"],
                },
            )
        ).structured_content
        assert cmp is not None
        assert [m["supported"] for m in cmp["matches"]] == [True, False]
        assert cmp["shared_services"] == ["orders-service"]

        missing = await client.call_tool("get_knowledge_document", {"document_id": "nope"})
        assert missing.is_error
        assert "not_found" in missing.content[0].text
