"""Evaluation results (read-only): runs written by ``python -m evaluation.run``.

Results live as files (``evals/results/<run_id>/results.json`` and ``index.json``) so a run is
reproducible from the repository and reviewable in git. Set ``AI_SRE_EVAL_RESULTS_DIR`` to read
them from elsewhere (for example a mounted volume in Docker).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/evaluations", tags=["evaluations"])

_RUN_ID = re.compile(r"^[0-9]{8}-[0-9]{6}$")


def results_dir() -> Path:
    env = os.environ.get("AI_SRE_EVAL_RESULTS_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[5] / "evals" / "results"


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(500, f"cannot read {path.name}: {type(exc).__name__}") from exc


@router.get("")
async def list_runs() -> dict[str, Any]:
    index = results_dir() / "index.json"
    if not index.exists():
        return {"runs": [], "results_dir_found": results_dir().exists()}
    data = _load(index)
    return {"runs": data.get("runs", []), "results_dir_found": True}


@router.get("/{run_id}")
async def get_run(run_id: str) -> dict[str, Any]:
    if not _RUN_ID.match(run_id):  # also prevents path traversal
        raise HTTPException(404, f"evaluation run {run_id} not found")
    path = results_dir() / run_id / "results.json"
    if not path.exists():
        raise HTTPException(404, f"evaluation run {run_id} not found")
    data: dict[str, Any] = _load(path)
    return data
