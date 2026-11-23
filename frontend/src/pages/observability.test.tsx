import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { EvalRun, InvestigationTrace } from "@/lib/types";
import { mockApi, problem, renderWithProviders } from "@/test/render";

import { EvaluationsPage } from "./EvaluationsPage";
import { TracePage } from "./TracePage";

const span = (over: Partial<InvestigationTrace["spans"][number]>) => ({
  span_id: "s",
  parent_id: null,
  trace_id: "t1",
  name: "x",
  kind: "node" as const,
  start: "2026-11-23T10:00:00.000Z",
  end: "2026-11-23T10:00:00.100Z",
  duration_ms: 100,
  status: "ok" as const,
  status_message: null,
  attributes: {},
  ...over,
});

const trace: InvestigationTrace = {
  investigation_id: "inv-1",
  question: "Why are orders failing?",
  status: "completed",
  reasoner: "llm",
  spans: [
    span({
      span_id: "root",
      name: "investigation.run",
      kind: "investigation",
      duration_ms: 900,
      end: "2026-11-23T10:00:00.900Z",
    }),
    span({
      span_id: "n1",
      parent_id: "root",
      name: "node.tool_execution",
      attributes: { "ai_sre.node": "tool_execution" },
    }),
    span({
      span_id: "t1",
      parent_id: "n1",
      name: "tool.logs.aggregate_errors",
      kind: "tool",
      status: "error",
      status_message: "backend_unavailable",
      attributes: { "ai_sre.tool.key": "logs.aggregate_errors", "ai_sre.tool.retries": 1 },
    }),
    span({
      span_id: "l1",
      parent_id: "root",
      name: "llm.generate",
      kind: "llm",
      attributes: { "gen_ai.usage.input_tokens": 1200 },
    }),
  ],
  summary: {
    spans: 4,
    duration_ms: 900,
    iterations: 2,
    nodes: { tool_execution: { executions: 2, total_ms: 400, errors: 0, retries: 0 } },
    tools: {
      calls: 14,
      errors: 1,
      retries: 1,
      evidence_ok: 13,
      latency_ms: 300,
      avg_latency_ms: 21.4,
      by_tool: { "logs.aggregate_errors": { calls: 1, errors: 1, retries: 1, total_ms: 50 } },
    },
    llm: {
      calls: 3,
      errors: 0,
      input_tokens: 3600,
      output_tokens: 900,
      total_tokens: 4500,
      latency_ms: 2400,
      cost_usd: 0.00072,
      models: ["gemini-3.5-flash-lite"],
    },
    retries: { tools: 1, nodes: 0 },
    result: { status: "completed", category: "bad_deploy", confidence: 0.82 },
  },
};

describe("TracePage", () => {
  it("shows tokens, cost, tool errors and the span tree with details", async () => {
    mockApi({ "GET /api/v1/investigations/inv-1/trace": trace });
    renderWithProviders(<TracePage />, {
      route: "/investigations/inv-1/trace",
      path: "/investigations/:id/trace",
    });

    expect(await screen.findByText("4,500")).toBeInTheDocument();
    expect(screen.getByText("$0.0007")).toBeInTheDocument();
    expect(screen.getByText("3,600 in / 900 out")).toBeInTheDocument();
    expect(screen.getByText("gemini-3.5-flash-lite")).toBeInTheDocument();
    expect(screen.getByText("bad_deploy")).toBeInTheDocument();

    const waterfall = screen.getByRole("table", { name: "Span waterfall" });
    const rows = within(waterfall).getAllByRole("row");
    expect(rows.map((r) => r.textContent)).toEqual([
      expect.stringContaining("investigation.run"),
      expect.stringContaining("node.tool_execution"),
      expect.stringContaining("tool.logs.aggregate_errors"),
      expect.stringContaining("llm.generate"),
    ]);
    await userEvent.click(rows[2] as HTMLElement);
    expect(screen.getByText(/backend_unavailable/)).toBeInTheDocument();
    expect(screen.getByText(/"ai_sre.tool.retries": 1/)).toBeInTheDocument();
  });

  it("reports a missing investigation", async () => {
    mockApi({ "GET /api/v1/investigations/nope/trace": () => problem(404, "Not Found") });
    renderWithProviders(<TracePage />, {
      route: "/investigations/nope/trace",
      path: "/investigations/:id/trace",
    });
    expect(await screen.findByText("Trace not available")).toBeInTheDocument();
  });
});

const metrics = (rc: number, sev: number) => ({
  n: 2,
  root_cause_accuracy: rc,
  root_cause_top3: 1,
  evidence_precision: 0.8,
  evidence_recall: 0.7,
  tool_selection_accuracy: 0.9,
  tool_call_efficiency: 0.6,
  severity_accuracy: sev,
  remediation_accuracy: 0.5,
  hallucination_rate: 0,
  completion_rate: 1,
  avg_tool_calls: 12,
  avg_latency_ms: 80,
  avg_tokens: 0,
  total_cost_usd: 0,
});

const row = (scenario: string, config: string, score: number) => ({
  scenario,
  config,
  tags: ["redis_failure"],
  completed: true,
  root_cause_score: score,
  root_cause_note: score === 1 ? "correct" : "wrong: capacity:api-gateway",
  root_cause_top3: true,
  predicted: {
    category: score ? "cache_failure" : "capacity",
    component: "redis",
    trigger_ref: null,
    confidence: 0.7,
    band: "medium",
  },
  evidence_precision: 1,
  evidence_recall: 1,
  tool_selection_accuracy: 1,
  tool_call_efficiency: 0.5,
  tool_calls: 12,
  severity: "SEV2",
  severity_expected: "SEV2",
  severity_correct: true,
  remediation_correct: score === 1,
  remediation_note: "manual",
  hallucinated: false,
  ungrounded_claims: [],
  latency_ms: 70,
  tokens: 0,
  cost_usd: 0,
  tool_errors: 0,
  retries: 0,
  summary: "s",
  tools_called: [],
  error: null,
});

const run: EvalRun = {
  run_id: "20261123-144128",
  created_at: "2026-11-23T14:41:28Z",
  mode: "replay",
  head_to_head: null,
  dataset: { name: "ai-sre-incidents", version: "1.0.0", scenarios: 25, evaluated: 2 },
  environment: { git_sha: "abc1234", python: "3.13" },
  configs: [
    {
      name: "rules-baseline",
      description: "default",
      reasoner: "rules",
      prompt: "default",
      limits: {},
      disabled_tools: [],
      metrics: metrics(1, 0.5),
      by_tag: { redis_failure: metrics(1, 0.5) },
      judge: null,
    },
    {
      name: "rules-single-round",
      description: "one round",
      reasoner: "rules",
      prompt: "default",
      limits: {},
      disabled_tools: [],
      metrics: metrics(0.5, 1),
      by_tag: { redis_failure: metrics(0.5, 1) },
      judge: null,
    },
  ],
  scenarios: [
    {
      id: "S02-redis-outage",
      title: "Redis becomes unreachable",
      tags: ["redis_failure"],
      expected: {
        root_cause: { category: "cache_failure", component: "redis" },
        severity: "SEV2",
        remediation: { action: "manual", target: null, keywords: ["redis"] },
        affected_services: [],
      },
    },
  ],
  rows: [
    row("S02-redis-outage", "rules-baseline", 1),
    row("S02-redis-outage", "rules-single-round", 0),
  ],
};

describe("EvaluationsPage", () => {
  it("compares configurations and drills into a scenario", async () => {
    mockApi({
      "GET /api/v1/evaluations": {
        runs: [
          {
            run_id: run.run_id,
            created_at: run.created_at,
            mode: "replay",
            dataset: run.dataset,
            configs: run.configs.map((c) => ({ name: c.name, metrics: c.metrics })),
          },
        ],
        results_dir_found: true,
      },
      [`GET /api/v1/evaluations/${run.run_id}`]: run,
    });
    renderWithProviders(<EvaluationsPage />, { route: "/evaluations", path: "/evaluations" });

    const table = await screen.findByRole("table", { name: "Configuration comparison" });
    const baseline = within(table).getByText("rules-baseline").closest("tr") as HTMLElement;
    // Root cause 100% is the best value in its column and is highlighted.
    expect(within(baseline).getAllByText("100%")[0]).toHaveClass("text-ok");
    expect(screen.getByText(/commit abc1234/)).toBeInTheDocument();
    expect(screen.getByText("One run so far")).toBeInTheDocument();

    await userEvent.click(screen.getByText("S02-redis-outage"));
    expect(screen.getByText("cache_failure · redis")).toBeInTheDocument();
    expect(screen.getByText(/wrong: capacity:api-gateway/)).toBeInTheDocument();
  });

  it("explains how to create the first run", async () => {
    mockApi({ "GET /api/v1/evaluations": { runs: [], results_dir_found: false } });
    renderWithProviders(<EvaluationsPage />, { route: "/evaluations", path: "/evaluations" });
    expect(await screen.findByText("No evaluation runs yet")).toBeInTheDocument();
    expect(screen.getByText("python -m evaluation.run")).toBeInTheDocument();
  });
});
