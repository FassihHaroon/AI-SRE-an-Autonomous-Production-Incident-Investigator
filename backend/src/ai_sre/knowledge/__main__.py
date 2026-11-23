"""Knowledge base CLI.

    python -m ai_sre.knowledge ingest [--path knowledge] [--prune]
    python -m ai_sre.knowledge delete <document-id>
    python -m ai_sre.knowledge search "pool exhausted after deploy" [--type runbook] [--service orders-service]
                                      [--mode hybrid|semantic|keyword] [--top-k 5]
    python -m ai_sre.knowledge stats
    python -m ai_sre.knowledge evaluate [--output docs/eval-results/retrieval.md]

Uses the platform database (AI_SRE_DATABASE_URL; run ``uv run poe migrate`` first) and the local
embedding model (downloaded once into .cache/fastembed; no API calls).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from ai_sre.config import REPO_ROOT, get_settings
from ai_sre.knowledge.embeddings import FastEmbedEmbedder
from ai_sre.knowledge.ingest import Ingestor
from ai_sre.knowledge.retrieval import Retriever
from ai_sre.knowledge.store import Filters, KnowledgeStore


def dsn() -> str:
    return get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


async def run(args: argparse.Namespace) -> int:
    store = await KnowledgeStore(dsn()).open()
    embedder = FastEmbedEmbedder()
    try:
        if args.cmd == "ingest":
            report = await Ingestor(store, embedder).ingest_dir(Path(args.path), prune=args.prune)
            print(json.dumps(report.__dict__ | {"writes": report.writes}, indent=2))
            return 1 if report.errors else 0
        if args.cmd == "delete":
            print("deleted" if await store.delete(args.document_id) else "not found")
            return 0
        if args.cmd == "stats":
            print(json.dumps(await store.counts()))
            return 0
        if args.cmd == "search":
            filters = Filters(doc_types=tuple(args.type or ()), services=tuple(args.service or ()))
            result = await Retriever(store, embedder).search(
                args.query, top_k=args.top_k, mode=args.mode, filters=filters
            )
            print(f"confident={result.confident} best_similarity={result.best_similarity}")
            for h in result.hits:
                flag = f" [SUPERSEDED by {h.superseded_by}]" if h.superseded_by else ""
                print(
                    f"{h.score:.4f}  {h.document_id:<28} {h.doc_type:<20} {h.source} > {h.section}{flag}"
                )
            return 0
        if args.cmd == "evaluate":
            from ai_sre.knowledge.evaluation import evaluate_all, render

            results = await evaluate_all(Retriever(store, embedder), Path(args.dataset))
            text = render(results)
            print(text)
            if args.output:
                await asyncio.to_thread(_write, Path(args.output), text)
            return 0
    finally:
        await store.close()
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ai_sre.knowledge")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("ingest")
    p.add_argument("--path", default=str(REPO_ROOT / "knowledge"))
    p.add_argument(
        "--prune", action="store_true", help="delete documents whose source file is gone"
    )
    sub.add_parser("delete").add_argument("document_id")
    sub.add_parser("stats")
    p = sub.add_parser("search")
    p.add_argument("query")
    p.add_argument("--type", action="append")
    p.add_argument("--service", action="append")
    p.add_argument("--mode", choices=["hybrid", "semantic", "keyword"], default="hybrid")
    p.add_argument("--top-k", type=int, default=5)
    p = sub.add_parser("evaluate")
    p.add_argument("--dataset", default=str(REPO_ROOT / "evals" / "retrieval" / "queries.yaml"))
    p.add_argument("--output")
    args = parser.parse_args(argv)
    if os.name == "nt":
        return asyncio.run(run(args), loop_factory=asyncio.SelectorEventLoop)
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
