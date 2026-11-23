"""Retrieval evaluation: Recall@K, Precision@K, MRR, nDCG@K (graded relevance), abstention on
no-match queries, conflict ordering; per retrieval mode, plus similarity-threshold calibration."""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ai_sre.knowledge.retrieval import Retriever
from ai_sre.knowledge.store import Filters

K = 5
MODES: tuple[tuple[str, str, bool], ...] = (
    ("keyword", "keyword", False),
    ("semantic", "semantic", False),
    ("hybrid", "hybrid", False),
    ("hybrid+rerank", "hybrid", True),
)


@dataclass
class QueryCase:
    id: str
    kind: str
    query: str
    relevant: dict[str, int]
    filters: dict[str, Any] = field(default_factory=dict)
    prefer_services: list[str] = field(default_factory=list)
    must_rank_above: list[list[str]] = field(default_factory=list)


def load_cases(path: Path) -> list[QueryCase]:
    return [
        QueryCase(**{**c, "relevant": c.get("relevant") or {}})
        for c in yaml.safe_load(path.read_text(encoding="utf-8"))
    ]


def recall_at_k(ranked: list[str], relevant: dict[str, int], k: int = K) -> float:
    rel = {d for d, g in relevant.items() if g > 0}
    return len(rel & set(ranked[:k])) / len(rel) if rel else 1.0


def precision_at_k(ranked: list[str], relevant: dict[str, int], k: int = K) -> float:
    rel = {d for d, g in relevant.items() if g > 0}
    top = ranked[:k]
    return len(rel & set(top)) / len(top) if top else 0.0


def reciprocal_rank(ranked: list[str], relevant: dict[str, int]) -> float:
    return next((1.0 / (i + 1) for i, d in enumerate(ranked) if relevant.get(d, 0) > 0), 0.0)


def ndcg_at_k(ranked: list[str], relevant: dict[str, int], k: int = K) -> float:
    dcg = sum((2 ** relevant.get(d, 0) - 1) / math.log2(i + 2) for i, d in enumerate(ranked[:k]))
    ideal = sorted(relevant.values(), reverse=True)[:k]
    idcg = sum((2**g - 1) / math.log2(i + 2) for i, g in enumerate(ideal))
    return dcg / idcg if idcg else 1.0


@dataclass
class ModeResult:
    mode: str
    recall: float
    precision: float
    mrr: float
    ndcg: float
    conflicts_ok: float
    abstention_ok: float | None
    per_query: list[dict[str, Any]]


async def evaluate_mode(
    retriever: Retriever, cases: list[QueryCase], label: str, mode: str, rerank: bool
) -> ModeResult:
    rows: list[dict[str, Any]] = []
    for c in cases:
        f = c.filters
        filters = Filters(
            doc_types=tuple(f.get("doc_types", ())), services=tuple(f.get("services", ()))
        )
        res = await retriever.search(
            c.query,
            top_k=K,
            mode=mode,  # type: ignore[arg-type]
            filters=filters,
            rerank=rerank,
            prefer_services=tuple(c.prefer_services),
        )
        ranked = [h.document_id for h in res.hits]
        conflict_ok = all(
            (a in ranked) and (b not in ranked or ranked.index(a) < ranked.index(b))
            for a, b in c.must_rank_above
        )
        rows.append(
            {
                "id": c.id,
                "kind": c.kind,
                "ranked": ranked,
                "confident": res.confident,
                "best_similarity": res.best_similarity,
                "recall": recall_at_k(ranked, c.relevant),
                "precision": precision_at_k(ranked, c.relevant),
                "rr": reciprocal_rank(ranked, c.relevant),
                "ndcg": ndcg_at_k(ranked, c.relevant),
                "conflict_ok": conflict_ok,
            }
        )
    scored = [r for r in rows if r["kind"] != "no_match"]
    nomatch = [r for r in rows if r["kind"] == "no_match"]
    conflicts = [r for r in rows if r["kind"] == "conflict"]
    return ModeResult(
        mode=label,
        recall=statistics.fmean(r["recall"] for r in scored),
        precision=statistics.fmean(r["precision"] for r in scored),
        mrr=statistics.fmean(r["rr"] for r in scored),
        ndcg=statistics.fmean(r["ndcg"] for r in scored),
        conflicts_ok=statistics.fmean(1.0 if r["conflict_ok"] else 0.0 for r in conflicts)
        if conflicts
        else 1.0,
        abstention_ok=(
            statistics.fmean(0.0 if r["confident"] else 1.0 for r in nomatch)
            if nomatch and mode != "keyword"
            else None
        ),
        per_query=rows,
    )


def calibrate(per_query: list[dict[str, Any]]) -> dict[str, Any]:
    """Best-similarity of the top semantic hit for answerable vs no-match queries."""
    pos = [
        r["best_similarity"]
        for r in per_query
        if r["kind"] != "no_match" and r["best_similarity"] is not None
    ]
    neg = [
        r["best_similarity"]
        for r in per_query
        if r["kind"] == "no_match" and r["best_similarity"] is not None
    ]
    best = (0.0, 0.0)
    for t in sorted({*pos, *neg}):
        acc = (sum(p >= t for p in pos) + sum(n < t for n in neg)) / (len(pos) + len(neg))
        if acc > best[1]:
            best = (t, acc)
    return {
        "answerable": sorted(pos),
        "no_match": sorted(neg),
        "best_threshold": best[0],
        "accuracy": best[1],
    }


async def evaluate_all(retriever: Retriever, dataset: Path) -> dict[str, Any]:
    cases = load_cases(dataset)
    results = [
        await evaluate_mode(retriever, cases, label, mode, rerank) for label, mode, rerank in MODES
    ]
    hybrid = next(r for r in results if r.mode == "hybrid+rerank")
    answerable = [q for q in hybrid.per_query if q["kind"] != "no_match"]
    return {
        "cases": len(cases),
        "results": results,
        "calibration": calibrate(hybrid.per_query),
        "min_similarity": retriever.min_similarity,
        "answerable_confident": f"{sum(q['confident'] for q in answerable)}/{len(answerable)}",
    }


def render(out: dict[str, Any]) -> str:
    cal = out["calibration"]
    lines = [
        "# Retrieval evaluation",
        "",
        f"Dataset: `evals/retrieval/queries.yaml` ({out['cases']} queries). K = {K}. Embeddings: BAAI/bge-small-en-v1.5 (local).",
        "",
        "| Mode | Recall@5 | Precision@5 | MRR | nDCG@5 | Conflicts resolved | No-match abstention |",
        "|------|---------:|------------:|----:|-------:|-------------------:|--------------------:|",
    ]
    for r in out["results"]:
        abst = "n/a" if r.abstention_ok is None else f"{r.abstention_ok:.2f}"
        lines.append(
            f"| {r.mode} | {r.recall:.3f} | {r.precision:.3f} | {r.mrr:.3f} | {r.ndcg:.3f} | {r.conflicts_ok:.2f} | {abst} |"
        )
    lines += [
        "",
        "Precision@5 is bounded by the number of relevant documents per query (1-3 of 5 slots).",
        "",
        "## Abstention threshold",
        "",
        f"Top semantic similarity, answerable queries: min {min(cal['answerable']):.3f}, median "
        f"{statistics.median(cal['answerable']):.3f}; no-match queries: max {max(cal['no_match']):.3f}.",
        f"Best separating threshold on this set: {cal['best_threshold']:.3f} (accuracy {cal['accuracy']:.2f}). "
        f"Configured `min_similarity`: {out['min_similarity']}.",
        "Similarity alone cannot separate the classes, so `confident` also requires that most query terms occur in "
        "the corpus (or similarity >= 0.75).",
        f"Answerable queries flagged confident (hybrid+rerank): {out['answerable_confident']}.",
        "",
        "## Per query (hybrid+rerank)",
        "",
        "| Query | Kind | Top 3 | RR | Confident |",
        "|---|---|---|---:|---|",
    ]
    hybrid = next(r for r in out["results"] if r.mode == "hybrid+rerank")
    for q in hybrid.per_query:
        lines.append(
            f"| {q['id']} | {q['kind']} | {', '.join(q['ranked'][:3]) or '-'} | {q['rr']:.2f} | {q['confident']} |"
        )
    return "\n".join(lines) + "\n"
