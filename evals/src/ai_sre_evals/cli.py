"""Command line: ``python -m evaluation.run --dataset evals/datasets/incidents.json``."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from ai_sre_evals.configs import CONFIGS, DEFAULT_CONFIGS
from ai_sre_evals.dataset import load_dataset
from ai_sre_evals.report import fmt, write_results
from ai_sre_evals.runner import run_eval

REPO = Path(__file__).resolve().parents[3]


def _split(value: str | None) -> list[str] | None:
    return [x.strip() for x in value.split(",") if x.strip()] if value else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m evaluation.run",
        description="Evaluate the AI SRE investigation agent on incident scenarios.",
    )
    parser.add_argument("--dataset", default=str(REPO / "evals/datasets/incidents.json"))
    parser.add_argument(
        "--config",
        action="append",
        dest="configs",
        help=f"Agent configuration (repeatable). Default: {', '.join(DEFAULT_CONFIGS)}. "
        f"Available: {', '.join(CONFIGS)}",
    )
    parser.add_argument(
        "--scenario", action="append", dest="scenarios", help="Only these scenario ids"
    )
    parser.add_argument(
        "--llm-scenarios",
        help="Comma-separated scenario ids for LLM configurations (token budget). Default: all selected",
    )
    parser.add_argument(
        "--judge", action="store_true", help="Add the model-based judge (uses LLM tokens)"
    )
    parser.add_argument(
        "--judge-scenarios", help="Comma-separated scenario ids to judge (default: all)"
    )
    parser.add_argument(
        "--judge-configs", help="Comma-separated configurations to judge (default: all)"
    )
    parser.add_argument(
        "--llm-delay", type=float, default=4.0, help="Seconds between LLM investigations"
    )
    parser.add_argument("--out", default=str(REPO / "evals/results"), help="Results directory")
    parser.add_argument(
        "--list", action="store_true", help="List scenarios and configurations, then exit"
    )
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    from ai_sre.observability.logging import configure_logging

    configure_logging("WARNING", "console")
    dataset = load_dataset(args.dataset)
    if args.list:
        for s in dataset.scenarios:
            print(f"{s.id:<40} {','.join(s.tags)}")
        print()
        for c in CONFIGS.values():
            print(f"{c.name:<32} {c.description}")
        return 0

    names = args.configs or DEFAULT_CONFIGS
    unknown = [n for n in names if n not in CONFIGS]
    if unknown:
        parser.error(f"unknown config(s): {unknown}")
    configs = [CONFIGS[n] for n in names]

    print(
        f"Evaluating {len(configs)} configuration(s) on {dataset.name} v{dataset.version}",
        flush=True,
    )
    results = asyncio.run(
        run_eval(
            dataset,
            configs,
            scenario_ids=args.scenarios,
            llm_scenario_ids=_split(args.llm_scenarios),
            use_judge=args.judge,
            judge_scenario_ids=_split(args.judge_scenarios),
            judge_configs=_split(args.judge_configs),
            llm_delay_s=args.llm_delay,
            progress=not args.quiet,
        )
    )
    run_dir = write_results(results, args.out)
    print()
    for c in results["configs"]:
        m = c["metrics"]
        print(
            f"{c['name']:<32} n={m.get('n', 0):>2} rc={fmt(m.get('root_cause_accuracy'), 'pct'):>4} "
            f"sev={fmt(m.get('severity_accuracy'), 'pct'):>4} fix={fmt(m.get('remediation_accuracy'), 'pct'):>4} "
            f"ev_recall={fmt(m.get('evidence_recall'), 'pct'):>4} halluc={fmt(m.get('hallucination_rate'), 'pct'):>4} "
            f"calls={fmt(m.get('avg_tool_calls'), 'num'):>5} tokens={m.get('total_tokens', 0)}"
        )
    print(f"\nResults: {run_dir / 'results.json'}\nReport:  {run_dir / 'report.md'}")
    print(json.dumps({"run_id": results["run_id"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
