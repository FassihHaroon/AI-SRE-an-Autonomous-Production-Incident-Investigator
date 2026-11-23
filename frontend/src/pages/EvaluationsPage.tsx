import { useMemo, useState } from "react";
import { useSearchParams } from "react-router";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { PageHeader } from "@/components/layout/PageHeader";
import {
  Badge,
  Card,
  CardBody,
  CardHeader,
  EmptyState,
  KeyValue,
  LoadingBlock,
  QueryState,
  Select,
} from "@/components/ui";
import { useEvalRun, useEvalRuns } from "@/features/evaluations/api";
import { SERIES_COLORS } from "@/lib/chart";
import { cn } from "@/lib/cn";
import { titleCase } from "@/lib/format";
import type { EvalMetrics, EvalRun, EvalRunSummary } from "@/lib/types";

type Kind = "pct" | "ms" | "num" | "usd";
const COLUMNS: { key: keyof EvalMetrics; label: string; kind: Kind; lowerIsBetter?: boolean }[] = [
  { key: "root_cause_accuracy", label: "Root cause", kind: "pct" },
  { key: "root_cause_top3", label: "RC top-3", kind: "pct" },
  { key: "evidence_precision", label: "Evidence precision", kind: "pct" },
  { key: "evidence_recall", label: "Evidence recall", kind: "pct" },
  { key: "tool_selection_accuracy", label: "Tool selection", kind: "pct" },
  { key: "tool_call_efficiency", label: "Tool efficiency", kind: "pct" },
  { key: "severity_accuracy", label: "Severity", kind: "pct" },
  { key: "remediation_accuracy", label: "Remediation", kind: "pct" },
  { key: "hallucination_rate", label: "Hallucination", kind: "pct", lowerIsBetter: true },
  { key: "completion_rate", label: "Completion", kind: "pct" },
  { key: "avg_tool_calls", label: "Tool calls", kind: "num", lowerIsBetter: true },
  { key: "avg_latency_ms", label: "Avg latency", kind: "ms", lowerIsBetter: true },
  { key: "avg_tokens", label: "Avg tokens", kind: "num", lowerIsBetter: true },
  { key: "llm_fallback_rate", label: "LLM fallback", kind: "pct", lowerIsBetter: true },
  { key: "total_cost_usd", label: "Est. cost", kind: "usd", lowerIsBetter: true },
];
const CHART_METRICS: (keyof EvalMetrics)[] = [
  "root_cause_accuracy",
  "evidence_recall",
  "tool_selection_accuracy",
  "severity_accuracy",
  "remediation_accuracy",
];

function fmt(value: unknown, kind: Kind): string {
  if (typeof value !== "number") return "—";
  if (kind === "pct") return `${Math.round(value * 100)}%`;
  if (kind === "ms") return `${value.toFixed(0)} ms`;
  if (kind === "usd") return `$${value.toFixed(4)}`;
  return value % 1 ? value.toFixed(1) : String(value);
}

function scoreTone(score: number): string {
  if (score >= 1) return "bg-ok-soft text-ok";
  if (score > 0) return "bg-warn-soft text-warn";
  return "bg-danger-soft text-danger";
}

const tooltipStyle = {
  contentStyle: {
    background: "#161a22",
    border: "1px solid #2e3445",
    borderRadius: 6,
    fontSize: 12,
  },
  labelStyle: { color: "#8a93a6" },
};

interface ComparisonRow {
  name: string;
  description?: string;
  metrics: EvalMetrics;
}

function Comparison({ rows, label }: { rows: ComparisonRow[]; label: string }) {
  const best = useMemo(() => {
    const out: Partial<Record<keyof EvalMetrics, number>> = {};
    for (const col of COLUMNS) {
      const vals = rows
        .map((c) => c.metrics[col.key])
        .filter((v): v is number => typeof v === "number");
      if (vals.length > 1) out[col.key] = col.lowerIsBetter ? Math.min(...vals) : Math.max(...vals);
    }
    return out;
  }, [rows]);

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm" aria-label={label}>
        <thead>
          <tr className="border-b border-line text-left text-xs text-muted">
            <th className="py-2 pr-3 font-medium">Configuration</th>
            <th className="py-2 pr-3 text-right font-medium">n</th>
            {COLUMNS.map((c) => (
              <th key={c.key} className="py-2 pr-3 text-right font-medium whitespace-nowrap">
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="tabular">
          {rows.map((cfg) => (
            <tr key={cfg.name} className="border-b border-line/60 last:border-0">
              <td className="min-w-[14rem] py-2 pr-3">
                <div className="font-mono text-xs whitespace-nowrap text-ink">{cfg.name}</div>
                {cfg.description ? (
                  <div className="max-w-xs text-[11px] text-muted">{cfg.description}</div>
                ) : null}
              </td>
              <td className="py-2 pr-3 text-right text-muted">{cfg.metrics.n}</td>
              {COLUMNS.map((c) => {
                const v = cfg.metrics[c.key];
                const isBest = typeof v === "number" && best[c.key] === v;
                return (
                  <td
                    key={c.key}
                    className={cn(
                      "py-2 pr-3 text-right",
                      isBest ? "font-semibold text-ok" : "text-ink/90",
                    )}
                  >
                    {fmt(v, c.kind)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function MetricChart({ run }: { run: EvalRun }) {
  const data = CHART_METRICS.map((m) => ({
    metric: COLUMNS.find((c) => c.key === m)?.label ?? m,
    ...Object.fromEntries(
      run.configs.map((c) => [c.name, Math.round(((c.metrics[m] as number | null) ?? 0) * 100)]),
    ),
  }));
  return (
    <div style={{ height: 260 }} data-testid="eval-chart">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -12 }}>
          <CartesianGrid stroke="#222734" strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="metric" stroke="#5c6478" tick={{ fontSize: 11 }} tickLine={false} />
          <YAxis
            stroke="#5c6478"
            tick={{ fontSize: 11 }}
            domain={[0, 100]}
            unit="%"
            tickLine={false}
            axisLine={false}
          />
          <Tooltip {...tooltipStyle} formatter={(v) => `${String(v)}%`} />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          {run.configs.map((c, i) => (
            <Bar
              key={c.name}
              dataKey={c.name}
              fill={SERIES_COLORS[i % SERIES_COLORS.length] ?? "#6d7dff"}
              radius={[2, 2, 0, 0]}
              isAnimationActive={false}
            />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

function Trend({ runs }: { runs: EvalRunSummary[] }) {
  const config = "rules-baseline";
  const data = [...runs]
    .reverse()
    .map((r) => ({ run: r.run_id, ...(r.configs.find((c) => c.name === config)?.metrics ?? {}) }))
    .filter((d) => "n" in d)
    .map((d) => ({
      run: d.run.slice(4, 13).replace("-", " "),
      "Root cause": Math.round((d.root_cause_accuracy ?? 0) * 100),
      Severity: Math.round((d.severity_accuracy ?? 0) * 100),
      Remediation: Math.round((d.remediation_accuracy ?? 0) * 100),
      "Evidence recall": Math.round((d.evidence_recall ?? 0) * 100),
    }));
  if (data.length < 2) {
    return (
      <EmptyState
        title="One run so far"
        description="The trend appears once there are two or more runs."
      />
    );
  }
  return (
    <div style={{ height: 220 }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -12 }}>
          <CartesianGrid stroke="#222734" strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="run" stroke="#5c6478" tick={{ fontSize: 11 }} tickLine={false} />
          <YAxis
            stroke="#5c6478"
            tick={{ fontSize: 11 }}
            domain={[0, 100]}
            unit="%"
            tickLine={false}
            axisLine={false}
          />
          <Tooltip {...tooltipStyle} formatter={(v) => `${String(v)}%`} />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          {["Root cause", "Severity", "Remediation", "Evidence recall"].map((k, i) => (
            <Line
              key={k}
              dataKey={k}
              stroke={SERIES_COLORS[i] ?? "#6d7dff"}
              dot
              isAnimationActive={false}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function ScenarioMatrix({ run }: { run: EvalRun }) {
  const [selected, setSelected] = useState<string | null>(null);
  const cell = new Map(run.rows.map((r) => [`${r.scenario}|${r.config}`, r]));
  const scenario = run.scenarios.find((s) => s.id === selected);
  const rows = run.rows.filter((r) => r.scenario === selected);
  return (
    <div className="grid gap-4 xl:grid-cols-[3fr_2fr]">
      <div className="overflow-x-auto">
        <table className="w-full text-xs" aria-label="Scenario results">
          <thead>
            <tr className="border-b border-line text-left text-muted">
              <th className="py-1.5 pr-2 font-medium">Scenario</th>
              {run.configs.map((c) => (
                <th key={c.name} className="py-1.5 pr-2 text-center font-mono font-medium">
                  {c.name.replace("rules-", "").replace("llm-planner", "llm")}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {run.scenarios.map((s) => (
              <tr
                key={s.id}
                onClick={() => {
                  setSelected(s.id);
                }}
                className={cn(
                  "cursor-pointer border-b border-line/50 hover:bg-raised/50",
                  selected === s.id && "bg-brand-soft/40",
                )}
              >
                <td className="py-1 pr-2">
                  <div className="font-mono text-ink">{s.id}</div>
                  <div className="text-[11px] text-faint">{s.tags.join(" · ")}</div>
                </td>
                {run.configs.map((c) => {
                  const r = cell.get(`${s.id}|${c.name}`);
                  return (
                    <td key={c.name} className="py-1 pr-2 text-center">
                      {r ? (
                        <span
                          title={`${r.root_cause_note}; severity ${r.severity ?? "?"}/${r.severity_expected}; remediation ${r.remediation_correct ? "ok" : r.remediation_note}`}
                          className={cn(
                            "inline-flex min-w-12 justify-center gap-1 rounded px-1.5 py-0.5 font-mono",
                            scoreTone(r.root_cause_score),
                          )}
                        >
                          {r.root_cause_score.toFixed(1)}
                          <span className={r.severity_correct ? "opacity-60" : "text-warn"}>S</span>
                          <span className={r.remediation_correct ? "opacity-60" : "text-danger"}>
                            R
                          </span>
                        </span>
                      ) : (
                        <span className="text-faint">—</span>
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
        <p className="mt-2 text-[11px] text-faint">
          Cell: root-cause score (1 correct, 0.5 acceptable alternative, 0 wrong). S and R light up
          when severity or remediation is wrong.
        </p>
      </div>
      <Card>
        <CardHeader
          title={scenario ? scenario.title : "Scenario details"}
          description={scenario?.id}
        />
        <CardBody className="space-y-3 text-xs">
          {scenario ? (
            <>
              <KeyValue
                items={[
                  [
                    "Expected cause",
                    scenario.expected.root_cause
                      ? `${scenario.expected.root_cause.category} · ${scenario.expected.root_cause.component}${scenario.expected.root_cause.trigger_ref ? ` · ${scenario.expected.root_cause.trigger_ref}` : ""}`
                      : "none (should abstain)",
                  ],
                  ["Severity", scenario.expected.severity],
                  [
                    "Remediation",
                    `${scenario.expected.remediation.action} ${scenario.expected.remediation.target ?? ""} ${scenario.expected.remediation.keywords.join(", ")}`,
                  ],
                ]}
              />
              {rows.map((r) => (
                <div key={r.config} className="rounded border border-line px-2 py-1.5">
                  <div className="flex items-center justify-between">
                    <span className="font-mono text-ink">{r.config}</span>
                    <span className={cn("rounded px-1.5 font-mono", scoreTone(r.root_cause_score))}>
                      {r.root_cause_score.toFixed(1)}
                    </span>
                  </div>
                  <div className="mt-0.5 text-muted">
                    {r.predicted.category
                      ? `${r.predicted.category} · ${r.predicted.component} (${((r.predicted.confidence ?? 0) * 100).toFixed(0)}% ${r.predicted.band ?? ""})`
                      : "no root cause"}{" "}
                    · {r.root_cause_note}
                  </div>
                  <div className="text-muted">
                    severity {r.severity ?? "—"} ·{" "}
                    {r.remediation_correct
                      ? "remediation ok"
                      : `remediation: ${r.remediation_note}`}{" "}
                    · {r.tool_calls} calls · {r.latency_ms.toFixed(0)} ms
                    {r.tokens ? ` · ${r.tokens} tokens` : ""}
                  </div>
                  {r.ungrounded_claims.length ? (
                    <div className="text-danger">Ungrounded: {r.ungrounded_claims.join(", ")}</div>
                  ) : null}
                  {r.judge ? (
                    <div className="text-info">
                      Judge: {r.judge.verdict} · explanation {r.judge.explanation_quality}/5 ·{" "}
                      {r.judge.rationale}
                    </div>
                  ) : null}
                </div>
              ))}
            </>
          ) : (
            <EmptyState
              title="Select a scenario"
              description="Click a row to compare configurations on it."
            />
          )}
        </CardBody>
      </Card>
    </div>
  );
}

function ByTag({ run }: { run: EvalRun }) {
  const [config, setConfig] = useState(run.configs[0]?.name ?? "");
  const cfg = run.configs.find((c) => c.name === config) ?? run.configs[0];
  if (!cfg) return null;
  return (
    <Card>
      <CardHeader
        title="By scenario type"
        description="Where the agent is strong and where it fails"
        actions={
          <>
            <label htmlFor="tag-config" className="sr-only">
              Configuration
            </label>
            <Select
              id="tag-config"
              className="h-8 w-56"
              value={cfg.name}
              onChange={(e) => {
                setConfig(e.target.value);
              }}
            >
              {run.configs.map((c) => (
                <option key={c.name}>{c.name}</option>
              ))}
            </Select>
          </>
        }
      />
      <CardBody>
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-line text-left text-xs text-muted">
              <th className="py-1.5 font-medium">Scenario type</th>
              <th className="py-1.5 text-right font-medium">n</th>
              <th className="py-1.5 text-right font-medium">Root cause</th>
              <th className="py-1.5 text-right font-medium">Severity</th>
              <th className="py-1.5 text-right font-medium">Remediation</th>
              <th className="py-1.5 text-right font-medium">Evidence recall</th>
            </tr>
          </thead>
          <tbody className="tabular">
            {Object.entries(cfg.by_tag).map(([tag, m]) => (
              <tr key={tag} className="border-b border-line/60 last:border-0">
                <td className="py-1.5">{titleCase(tag)}</td>
                <td className="py-1.5 text-right text-muted">{m.n}</td>
                <td
                  className={cn(
                    "py-1.5 text-right",
                    (m.root_cause_accuracy ?? 1) < 0.75 && "text-danger",
                  )}
                >
                  {fmt(m.root_cause_accuracy, "pct")}
                </td>
                <td className="py-1.5 text-right">{fmt(m.severity_accuracy, "pct")}</td>
                <td className="py-1.5 text-right">{fmt(m.remediation_accuracy, "pct")}</td>
                <td className="py-1.5 text-right">{fmt(m.evidence_recall, "pct")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </CardBody>
    </Card>
  );
}

function RunView({ run, runs }: { run: EvalRun; runs: EvalRunSummary[] }) {
  const judged = run.configs.filter((c) => c.judge);
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader
          title="Configurations compared"
          description={`${run.dataset.name} v${run.dataset.version} · ${run.dataset.evaluated} scenarios · ${run.mode} mode · commit ${run.environment.git_sha ?? "unknown"} · best value per column in green`}
        />
        <CardBody>
          <Comparison rows={run.configs} label="Configuration comparison" />
        </CardBody>
      </Card>
      {run.head_to_head ? (
        <Card>
          <CardHeader
            title={`Head to head on ${String(run.head_to_head.scenarios.length)} common scenarios`}
            description={`LLM configurations ran on a subset (token budget); compare them here only: ${run.head_to_head.scenarios.join(", ")}`}
          />
          <CardBody>
            <Comparison
              rows={Object.entries(run.head_to_head.configs).map(([name, metrics]) => ({
                name,
                metrics,
              }))}
              label="Head to head comparison"
            />
          </CardBody>
        </Card>
      ) : null}
      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader title="Quality by configuration" />
          <CardBody>
            <MetricChart run={run} />
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Trend across runs" description="rules-baseline, oldest to newest" />
          <CardBody>
            <Trend runs={runs} />
          </CardBody>
        </Card>
      </div>
      {judged.length ? (
        <Card>
          <CardHeader
            title="Model-based judge"
            description="Subjective; reported next to, never instead of, the programmatic scores"
          />
          <CardBody className="flex flex-wrap gap-6 text-sm">
            {judged.map((c) =>
              c.judge ? (
                <div key={c.name}>
                  <div className="font-mono text-xs text-ink">{c.name}</div>
                  <div className="text-muted">
                    n={c.judge.n} · agreement {Math.round(c.judge.agreement_with_program * 100)}% ·
                    explanation {c.judge.avg_explanation_quality}/5 · actionability{" "}
                    {c.judge.avg_actionability}/5
                  </div>
                </div>
              ) : null,
            )}
          </CardBody>
        </Card>
      ) : null}
      <ByTag run={run} />
      <Card>
        <CardHeader title="Scenarios × configurations" />
        <CardBody>
          <ScenarioMatrix run={run} />
        </CardBody>
      </Card>
      <p className="text-xs text-faint">
        Replay mode: deterministic scenario worlds derived from injected faults
        (docs/evaluation.md). Latency is agent time without telemetry-backend latency. Costs are
        estimates from token counts and list prices.
      </p>
    </div>
  );
}

export function EvaluationsPage() {
  const runs = useEvalRuns();
  const [params, setParams] = useSearchParams();
  const runId = params.get("run") ?? runs.data?.runs[0]?.run_id;
  const run = useEvalRun(runId);

  return (
    <>
      <PageHeader
        title="Evaluations"
        description="How well the investigator finds root causes, cites evidence and recommends fixes, measured on the incident dataset."
        actions={
          runs.data?.runs.length ? (
            <>
              <label htmlFor="run" className="sr-only">
                Evaluation run
              </label>
              <Select
                id="run"
                className="h-8 w-64"
                value={runId}
                onChange={(e) => {
                  setParams({ run: e.target.value }, { replace: true });
                }}
              >
                {runs.data.runs.map((r) => (
                  <option key={r.run_id} value={r.run_id}>
                    {r.run_id} · {r.configs.length} config(s)
                  </option>
                ))}
              </Select>
            </>
          ) : null
        }
      />
      <QueryState
        query={runs}
        loading={<LoadingBlock rows={6} />}
        isEmpty={(d) => !d.runs.length}
        empty={
          <EmptyState
            title="No evaluation runs yet"
            description={
              <>
                Run <code className="font-mono text-ink">python -m evaluation.run</code> to evaluate
                the agent on the incident dataset.
              </>
            }
          />
        }
      >
        {(d) => (
          <QueryState
            query={run}
            loading={<LoadingBlock rows={10} />}
            errorTitle="Run not available"
          >
            {(r) => (
              <>
                <div className="mb-3 flex flex-wrap gap-2">
                  {r.configs.some((c) => c.reasoner === "llm") ? (
                    <Badge tone="brand">includes LLM configurations</Badge>
                  ) : null}
                  <Badge>{new Date(r.created_at).toLocaleString([], { hour12: false })}</Badge>
                </div>
                <RunView run={r} runs={d.runs} />
              </>
            )}
          </QueryState>
        )}
      </QueryState>
    </>
  );
}
