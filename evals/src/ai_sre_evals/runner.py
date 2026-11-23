"""Run agent configurations over the dataset and score every investigation."""

from __future__ import annotations

import asyncio
import platform
import subprocess
import time
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

import structlog
from langgraph.checkpoint.memory import InMemorySaver

from ai_sre.agent.graph import Deps, build_graph
from ai_sre.agent.models import Evidence, Report
from ai_sre.agent.reasoner import LLMReasoner, Reasoner, RuleBasedReasoner
from ai_sre.agent.runner import Investigator, serializer
from ai_sre.integrations.llm.base import LLMProvider
from ai_sre.observability.tracing import collector, summarize
from ai_sre_evals.configs import AgentConfig
from ai_sre_evals.dataset import Dataset, Scenario
from ai_sre_evals.judge import judge
from ai_sre_evals.scoring import aggregate, score
from ai_sre_evals.world import WorldGateway

logger = structlog.get_logger("evals.runner")


def llm_provider() -> LLMProvider:
    from ai_sre.config import get_settings
    from ai_sre.integrations.llm.factory import create_llm_provider

    return create_llm_provider(get_settings())


def _reasoner(config: AgentConfig, provider: LLMProvider | None) -> Reasoner:
    if config.uses_llm:
        if provider is None:
            raise RuntimeError(f"{config.name} needs an LLM provider")
        return LLMReasoner(provider, prompt=config.prompt)
    return RuleBasedReasoner()


async def run_one(
    scenario: Scenario,
    config: AgentConfig,
    *,
    run_id: str,
    provider: LLMProvider | None = None,
    judge_provider: LLMProvider | None = None,
    timeout_s: float = 300,
) -> dict[str, Any]:
    gateway = WorldGateway(scenario.world_spec(), disabled_tools=set(config.disabled_tools))
    reasoner = _reasoner(config, provider)
    graph = build_graph(Deps(gateway=gateway, reasoner=reasoner)).compile(  # type: ignore[arg-type]
        checkpointer=InMemorySaver(serde=serializer())
    )
    investigator = Investigator(graph, reasoner.name)
    inv_id = f"eval-{run_id}-{config.name}-{scenario.id}"
    error: str | None = None
    started = time.perf_counter()
    try:
        result = await asyncio.wait_for(
            investigator.start(scenario.query, limits=config.limits, investigation_id=inv_id),
            timeout_s,
        )
        if result.status == "awaiting_approval":  # never execute: proposals are only scored
            await investigator.resume(inv_id, [])
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        logger.warning(
            "eval_investigation_failed", scenario=scenario.id, config=config.name, error=error
        )
    wall_ms = (time.perf_counter() - started) * 1000
    values = (await graph.aget_state(Investigator.config(inv_id))).values
    report: Report | None = values.get("report")
    evidence: list[Evidence] = list(values.get("evidence", []))
    trace = summarize(collector().drain(inv_id))
    trace["duration_ms"] = trace.get("duration_ms") or round(wall_ms, 1)
    completed = error is None and report is not None and report.stop_reason != "error"
    row = score(scenario, report, evidence, trace, completed=completed)
    row.update(
        {
            "config": config.name,
            "investigation_id": inv_id,
            "error": error,
            "summary": report.summary if report else None,
            "tools_called": [f"{e.server}.{e.tool}" for e in evidence],
            "node_ms": {k: v["total_ms"] for k, v in trace.get("nodes", {}).items()},
        }
    )
    if judge_provider is not None:
        verdict = await judge(judge_provider, scenario, report)
        row["judge"] = verdict.model_dump() if verdict else None
    return row


def _git_sha() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607 - developer tool, fixed args
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        sha = out.stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],  # noqa: S607 - developer tool, fixed args
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        ).stdout.strip()
        return f"{sha}-dirty" if sha and dirty else sha or None
    except OSError:
        return None


async def run_eval(
    dataset: Dataset,
    configs: list[AgentConfig],
    *,
    scenario_ids: list[str] | None = None,
    llm_scenario_ids: list[str] | None = None,
    use_judge: bool = False,
    judge_scenario_ids: list[str] | None = None,
    judge_configs: list[str] | None = None,
    llm_delay_s: float = 4.0,
    progress: bool = True,
) -> dict[str, Any]:
    run_id = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    scenarios = [s for s in dataset.scenarios if not scenario_ids or s.id in scenario_ids]
    needs_llm = use_judge or any(c.uses_llm for c in configs)
    provider = llm_provider() if needs_llm else None
    rows: list[dict[str, Any]] = []
    for config in configs:
        subset = (
            [s for s in scenarios if not llm_scenario_ids or s.id in llm_scenario_ids]
            if config.uses_llm
            else scenarios
        )
        for scenario in subset:
            judge_this = (
                use_judge
                and (not judge_scenario_ids or scenario.id in judge_scenario_ids)
                and (not judge_configs or config.name in judge_configs)
            )
            row = await run_one(
                scenario,
                config,
                run_id=run_id,
                provider=provider,
                judge_provider=provider if judge_this else None,
            )
            rows.append(row)
            if progress:
                print(
                    f"  {config.name:<30} {scenario.id:<36} rc={row['root_cause_score']:.1f} "
                    f"sev={'ok' if row['severity_correct'] else 'x '} "
                    f"fix={'ok' if row['remediation_correct'] else 'x '} calls={row['tool_calls']:>2} "
                    f"{row['latency_ms']:>7.0f} ms tok={row['tokens']}",
                    flush=True,
                )
            if (config.uses_llm or judge_this) and llm_delay_s:
                await asyncio.sleep(llm_delay_s)  # stay under free-tier requests/minute
    return build_results(run_id, dataset, configs, rows, scenarios)


def build_results(
    run_id: str,
    dataset: Dataset,
    configs: list[AgentConfig],
    rows: list[dict[str, Any]],
    scenarios: list[Scenario],
) -> dict[str, Any]:
    by_config: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_config[r["config"]].append(r)
    summaries = []
    for c in configs:
        cr = by_config.get(c.name, [])
        tags: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in cr:
            for t in r["tags"]:
                tags[t].append(r)
        judged = [r for r in cr if r.get("judge")]
        judge_summary = None
        if judged:
            agree = [
                (r["judge"]["verdict"] in ("correct", "abstained"))
                == (r["root_cause_score"] == 1.0)
                for r in judged
            ]
            judge_summary = {
                "n": len(judged),
                "agreement_with_program": round(sum(agree) / len(agree), 3),
                "avg_explanation_quality": round(
                    sum(r["judge"]["explanation_quality"] for r in judged) / len(judged), 2
                ),
                "avg_actionability": round(
                    sum(r["judge"]["actionability"] for r in judged) / len(judged), 2
                ),
                "unsupported_statements": sum(r["judge"]["unsupported_statements"] for r in judged),
            }
        summaries.append(
            {
                "name": c.name,
                "description": c.description,
                "reasoner": c.reasoner,
                "prompt": c.prompt,
                "limits": c.limits.model_dump(),
                "disabled_tools": sorted(c.disabled_tools),
                "metrics": aggregate(cr),
                "by_tag": {t: aggregate(v) for t, v in sorted(tags.items())},
                "judge": judge_summary,
            }
        )
    # Configurations evaluated on fewer scenarios (LLM runs on a subset) are only comparable on
    # the scenarios every configuration ran: report those head-to-head numbers separately.
    covered = [{r["scenario"] for r in by_config.get(c.name, [])} for c in configs]
    common = set.intersection(*covered) if covered else set()
    head_to_head = None
    if covered and any(len(c) != len(common) for c in covered) and common:
        head_to_head = {
            "scenarios": sorted(common),
            "configs": {
                c.name: aggregate([r for r in by_config.get(c.name, []) if r["scenario"] in common])
                for c in configs
            },
        }
    return {
        "run_id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "head_to_head": head_to_head,
        "mode": "replay",
        "dataset": {
            "name": dataset.name,
            "version": dataset.version,
            "scenarios": len(dataset.scenarios),
            "evaluated": len(scenarios),
        },
        "environment": {"git_sha": _git_sha(), "python": platform.python_version()},
        "configs": summaries,
        "scenarios": [
            {"id": s.id, "title": s.title, "tags": s.tags, "expected": s.expected.model_dump()}
            for s in scenarios
        ],
        "rows": rows,
    }
