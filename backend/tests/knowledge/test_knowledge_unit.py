"""Knowledge pipeline units: loaders, metadata, chunking, embeddings, fusion, metrics."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_sre.config import REPO_ROOT
from ai_sre.knowledge.chunking import chunk_document
from ai_sre.knowledge.documents import DocumentError, discover, load_document
from ai_sre.knowledge.embeddings import HashingEmbedder
from ai_sre.knowledge.evaluation import ndcg_at_k, precision_at_k, recall_at_k, reciprocal_rank
from ai_sre.knowledge.retrieval import rrf

KB = REPO_ROOT / "knowledge"


def test_corpus_loads_every_supported_format() -> None:
    docs = [load_document(p, KB) for p in discover(KB)]
    assert {Path(d.source).suffix for d in docs} == {".md", ".json", ".txt", ".pdf"}
    types = {d.doc_type for d in docs}
    assert {
        "incident",
        "runbook",
        "procedure",
        "service_architecture",
        "architecture",
        "troubleshooting",
        "operational",
    } <= types
    assert len({d.id for d in docs}) == len(docs), "document ids are unique"


def test_markdown_metadata() -> None:
    doc = load_document(KB / "runbooks" / "rb-db-pool-exhaustion.md", KB)
    assert doc.id == "rb-db-pool-exhaustion"
    assert doc.doc_type == "runbook"
    assert "orders-service" in doc.services
    assert doc.environment == "simulated-prod"
    assert doc.supersedes == "rb-db-pool-exhaustion-2024"
    assert doc.timestamp is not None
    assert doc.timestamp.year == 2026


def test_incident_markdown_and_json_records() -> None:
    md = load_document(KB / "incidents" / "INC-2026-031.md", KB)
    assert (md.doc_type, md.incident_id, md.severity, md.category) == (
        "incident",
        "INC-2026-031",
        "SEV2",
        "bad_deploy",
    )
    js = load_document(KB / "incidents" / "INC-2025-112.json", KB)
    assert (js.incident_id, js.category, js.services) == (
        "INC-2025-112",
        "database_degradation",
        ["orders-service"],
    )
    assert "## Root cause" in js.body
    assert "## Resolution" in js.body


def test_text_and_pdf_metadata() -> None:
    txt = load_document(KB / "troubleshooting" / "latency-triage.txt", KB)
    assert txt.doc_type == "troubleshooting"
    assert txt.services == ["api-gateway", "orders-service"]
    assert txt.title == "Latency triage guide (shop)"
    pdf = load_document(KB / "operations" / "postgres-operations-guide.pdf", KB)
    assert pdf.title == "PostgreSQL operations guide"
    assert pdf.services == ["orders-service", "api-gateway"]
    assert "idle in transaction" in pdf.body.lower()


def test_invalid_documents_are_rejected(tmp_path: Path) -> None:
    (tmp_path / "bad.md").write_text('---\ntype: "poem"\n---\nhello', encoding="utf-8")
    with pytest.raises(DocumentError, match="unknown document type"):
        load_document(tmp_path / "bad.md", tmp_path)
    (tmp_path / "x.json").write_text(json.dumps({"title": "no id"}), encoding="utf-8")
    with pytest.raises(DocumentError, match="incident_id"):
        load_document(tmp_path / "x.json", tmp_path)
    (tmp_path / "empty.md").write_text("---\ntitle: x\n---\n", encoding="utf-8")
    with pytest.raises(DocumentError, match="empty"):
        load_document(tmp_path / "empty.md", tmp_path)


def test_content_hash_changes_with_content_or_metadata(tmp_path: Path) -> None:
    p = tmp_path / "runbooks" / "r.md"
    p.parent.mkdir()
    p.write_text('---\nid: "r"\ntags: ["a"]\n---\n# R\n\nbody', encoding="utf-8")
    h1 = load_document(p, tmp_path).content_hash
    p.write_text('---\nid: "r"\ntags: ["b"]\n---\n# R\n\nbody', encoding="utf-8")
    h2 = load_document(p, tmp_path).content_hash
    p.write_text('---\nid: "r"\ntags: ["b"]\n---\n# R\n\nbody changed', encoding="utf-8")
    assert len({h1, h2, load_document(p, tmp_path).content_hash}) == 3


def test_chunks_follow_headings_and_respect_size() -> None:
    body = "# Title\n\nintro\n\n## Symptoms\n\n" + "\n\n".join(
        f"paragraph {i} " + "x" * 300 for i in range(6)
    )
    body += "\n\n## Remediation\n\nroll back"
    chunks = chunk_document("Title", body, max_chars=700)
    sections = [c.section for c in chunks]
    assert sections[0] == "Title"
    assert "Title > Symptoms" in sections
    assert sections[-1] == "Title > Remediation"
    assert all(len(c.content) <= 700 for c in chunks)
    assert [c.index for c in chunks] == list(range(len(chunks)))
    assert chunks[-1].embedding_text("Title").startswith("Title. Title > Remediation.")


def test_hashing_embedder_is_deterministic_and_normalised() -> None:
    e = HashingEmbedder()
    a, b = e.embed_documents(["pool exhausted", "pool exhausted"])
    assert a == b
    assert len(a) == 384
    assert abs(sum(x * x for x in a) - 1) < 1e-9


def test_rrf_rewards_agreement() -> None:
    scores = rrf([["a", "b", "c"], ["b", "a", "d"]])
    assert scores["a"] == scores["b"] > scores["c"] > 0
    assert scores["d"] < scores["a"]


def test_ranking_metrics() -> None:
    rel = {"a": 2, "b": 1}
    assert recall_at_k(["x", "a", "y", "b"], rel) == 1.0
    assert recall_at_k(["x", "a"], rel) == 0.5
    assert precision_at_k(["a", "x", "b", "y"], rel) == 0.5
    assert reciprocal_rank(["x", "y", "a"], rel) == pytest.approx(1 / 3)
    assert ndcg_at_k(["a", "b"], rel) == pytest.approx(1.0)
    assert ndcg_at_k(["b", "a"], rel) < 1.0
    assert recall_at_k(["x"], {}) == 1.0
