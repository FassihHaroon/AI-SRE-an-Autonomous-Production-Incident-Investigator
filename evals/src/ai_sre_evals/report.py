"""Write evaluation results: results.json (machine) + report.md (human) + runs index."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

HEADLINE = [
    ("root_cause_accuracy", "RC acc", "pct"),
    ("root_cause_top3", "RC top-3", "pct"),
    ("evidence_precision", "Ev. prec", "pct"),
    ("evidence_recall", "Ev. recall", "pct"),
    ("tool_selection_accuracy", "Tool sel.", "pct"),
    ("tool_call_efficiency", "Tool eff.", "pct"),
    ("severity_accuracy", "Severity", "pct"),
    ("remediation_accuracy", "Remediation", "pct"),
    ("hallucination_rate", "Halluc.", "pct"),
    ("completion_rate", "Complete", "pct"),
    ("avg_tool_calls", "Calls", "num"),
    ("avg_latency_ms", "Latency", "ms"),
    ("avg_tokens", "Tokens", "num"),
    ("llm_fallback_rate", "LLM fallback", "pct"),
    ("total_cost_usd", "Cost", "usd"),
]


def fmt(value: Any, kind: str) -> str:
    if value is None:
        return "—"
    if kind == "pct":
        return f"{value * 100:.0f}%"
    if kind == "ms":
        return f"{value:,.0f} ms"
    if kind == "usd":
        return f"${value:.4f}"
    return f"{value:,.1f}" if isinstance(value, float) else str(value)


def _table(headers: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(out)


def render_markdown(results: dict[str, Any]) -> str:
    ds = results["dataset"]
    lines = [
        f"# Evaluation run {results['run_id']}",
        "",
        f"- **Dataset:** {ds['name']} v{ds['version']} ({ds['evaluated']} of {ds['scenarios']} scenarios)",
        f"- **Mode:** {results['mode']} (deterministic scenario worlds; see docs/evaluation.md)",
        f"- **Commit:** {results['environment'].get('git_sha') or 'unknown'} · Python {results['environment']['python']}",
        f"- **Created:** {results['created_at']}",
        "",
        "## Configurations compared",
        "",
        _table(
            ["Config", "n", *[h for _, h, _ in HEADLINE]],
            [
                [
                    f"`{c['name']}`",
                    str(c["metrics"].get("n", 0)),
                    *[fmt(c["metrics"].get(k), kind) for k, _, kind in HEADLINE],
                ]
                for c in results["configs"]
            ],
        ),
        "",
        "Latency is wall-clock time of the agent; in replay mode tool calls are answered in-process, "
        "so it excludes telemetry-backend latency. Cost is estimated from token counts and list "
        "prices (`ai_sre.observability.tracing.PRICES_PER_MTOK`).",
        "",
    ]
    for c in results["configs"]:
        lines += [f"- `{c['name']}`: {c['description']}"]
    lines.append("")
    h2h = results.get("head_to_head")
    if h2h:
        lines += [
            f"### Head to head on the {len(h2h['scenarios'])} scenarios every configuration ran",
            "",
            "LLM configurations run on a subset (token budget); compare them only here: "
            + ", ".join(f"`{s}`" for s in h2h["scenarios"]),
            "",
            _table(
                ["Config", "n", *[h for _, h, _ in HEADLINE]],
                [
                    [
                        f"`{name}`",
                        str(m.get("n", 0)),
                        *[fmt(m.get(k), kind) for k, _, kind in HEADLINE],
                    ]
                    for name, m in h2h["configs"].items()
                ],
            ),
            "",
        ]

    base = results["configs"][0]
    lines += [
        f"## By scenario type (`{base['name']}`)",
        "",
        _table(
            ["Tag", "n", "RC acc", "Severity", "Remediation", "Ev. recall", "Halluc."],
            [
                [
                    t,
                    str(m["n"]),
                    fmt(m["root_cause_accuracy"], "pct"),
                    fmt(m["severity_accuracy"], "pct"),
                    fmt(m["remediation_accuracy"], "pct"),
                    fmt(m["evidence_recall"], "pct"),
                    fmt(m["hallucination_rate"], "pct"),
                ]
                for t, m in base["by_tag"].items()
            ],
        ),
        "",
        f"## Per scenario (`{base['name']}`)",
        "",
    ]
    expected = {s["id"]: s["expected"] for s in results["scenarios"]}
    rows = [r for r in results["rows"] if r["config"] == base["name"]]
    table = []
    for r in rows:
        e = expected[r["scenario"]]
        exp_rc = (
            f"{e['root_cause']['category']}:{e['root_cause']['component']}"
            if e["root_cause"]
            else "none (abstain)"
        )
        p = r["predicted"]
        got = (
            f"{p['category']}:{p['component']} ({p['confidence']:.2f})" if p["category"] else "none"
        )
        table.append(
            [
                r["scenario"],
                exp_rc,
                got,
                fmt(r["root_cause_score"], "num"),
                f"{r['severity']}/{r['severity_expected']}",
                "✓" if r["remediation_correct"] else "✗",
                str(r["tool_calls"]),
                r["root_cause_note"],
            ]
        )
    lines += [
        _table(
            ["Scenario", "Expected", "Predicted", "RC", "Sev (got/exp)", "Fix", "Calls", "Note"],
            table,
        ),
        "",
    ]

    failures = [
        r
        for r in rows
        if r["root_cause_score"] < 1 or not r["remediation_correct"] or not r["severity_correct"]
    ]
    if failures:
        lines += ["## Misses to investigate", ""]
        for r in failures:
            notes = [r["root_cause_note"]] if r["root_cause_score"] < 1 else []
            if not r["remediation_correct"]:
                notes.append(f"remediation: {r['remediation_note']}")
            if not r["severity_correct"]:
                notes.append(f"severity {r['severity']} vs {r['severity_expected']}")
            lines.append(f"- **{r['scenario']}**: {'; '.join(notes)}")
        lines.append("")

    judged = [c for c in results["configs"] if c.get("judge")]
    if judged:
        lines += [
            "## Model-based judge",
            "",
            _table(
                [
                    "Config",
                    "n",
                    "Agreement with program",
                    "Explanation (1-5)",
                    "Actionability (1-5)",
                    "Unsupported statements",
                ],
                [
                    [
                        c["name"],
                        str(c["judge"]["n"]),
                        fmt(c["judge"]["agreement_with_program"], "pct"),
                        f"{c['judge']['avg_explanation_quality']:.1f}",
                        f"{c['judge']['avg_actionability']:.1f}",
                        str(c["judge"]["unsupported_statements"]),
                    ]
                    for c in judged
                ],
            ),
            "",
        ]

    lines += [
        "## Limitations",
        "",
        "- Scenario worlds are synthetic telemetry derived from injected faults. They are "
        "deterministic and comparable run to run, but simpler than production noise.",
        "- The rule-based agent was developed alongside the simulator scenarios that several "
        "dataset entries are modelled on; scores on those are optimistic. The edge-case scenarios "
        "(missing/conflicting evidence, multiple failures, no root cause) were written for this "
        "evaluation and were not used to tune the agent.",
        "- LLM configurations, when included, run on a subset (free-tier token limits) with one "
        "sample per scenario; differences of one or two scenarios are within noise.",
        "- Hallucination is measured as claims in the report not traceable to tool results "
        "(components, deployment ids, cited evidence, citations, affected services). It does not "
        "judge free-text wording; the optional model-based judge covers that, subjectively.",
        "",
    ]
    return "\n".join(lines)


def write_results(results: dict[str, Any], out_dir: str | Path) -> Path:
    root = Path(out_dir)
    run_dir = root / str(results["run_id"])
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "results.json").write_text(
        json.dumps(results, indent=2, default=str), encoding="utf-8"
    )
    (run_dir / "report.md").write_text(render_markdown(results), encoding="utf-8")
    index_path = root / "index.json"
    index = (
        json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {"runs": []}
    )
    index["runs"] = [r for r in index["runs"] if r["run_id"] != results["run_id"]]
    index["runs"].insert(
        0,
        {
            "run_id": results["run_id"],
            "created_at": results["created_at"],
            "mode": results["mode"],
            "dataset": results["dataset"],
            "configs": [{"name": c["name"], "metrics": c["metrics"]} for c in results["configs"]],
        },
    )
    index_path.write_text(json.dumps(index, indent=2, default=str), encoding="utf-8")
    return run_dir
