/** Test fixtures shaped like real API responses (captured from the running backend). */
import type {
  InvestigationDetail,
  InvestigationRecord,
  Overview,
  PlatformSettings,
  Proposal,
  Report,
  RootCause,
} from "@/lib/types";

export const rootCause: RootCause = {
  hypothesis_id: "bad_deploy:orders-service:d-408",
  title: "d-408 (orders-service 2.3.1 -> 2.4.0) caused the incident",
  category: "bad_deploy",
  component: "orders-service",
  trigger_ref: "d-408",
  explanation: "Pool timeouts began 40 s after d-408 finished.",
  confidence: 0.82,
  band: "high",
  evidence: ["ev-001", "ev-002"],
  capped: null,
};

export const proposal: Proposal = {
  id: "p1",
  order: 1,
  action: "rollback_deployment_simulation",
  arguments: { service: "orders-service", reason: "d-408 caused the incident (confidence high)" },
  description: "Roll back orders-service, undoing d-408.",
  risk: "medium",
  expected_impact:
    "orders-service returns to its previous version; symptoms clear within ~1 minute.",
  reversible: true,
  executable: true,
  approval_request_id: "apr-1",
  status: "awaiting_approval",
  result: null,
  error: null,
  references: ["rb-rollback-procedure > Steps (runbooks/rb-rollback-procedure.md)"],
};

export function investigation(overrides: Partial<InvestigationRecord> = {}): InvestigationRecord {
  return {
    id: "inv-1",
    question: "Why are checkout requests failing?",
    status: "running",
    parent_id: null,
    severity: "SEV2",
    reasoner: "rules",
    limits: {},
    root_cause: null,
    approval: null,
    report: null,
    error: null,
    created_at: "2026-11-23T10:00:00Z",
    updated_at: "2026-11-23T10:00:05Z",
    ...overrides,
  };
}

export function detail(
  inv: Partial<InvestigationRecord> = {},
  state: InvestigationDetail["state"] = {},
): InvestigationDetail {
  return { investigation: investigation(inv), state };
}

export const evidence = [
  {
    id: "ev-001",
    round: 1,
    server: "deployments",
    tool: "list_recent_deployments",
    ok: true,
    summary: "5 deployments in the last 2 h",
    error_code: null,
    duration_ms: 223,
    at: "2026-11-23T10:00:01Z",
  },
  {
    id: "ev-002",
    round: 1,
    server: "logs",
    tool: "aggregate_errors",
    ok: false,
    summary: "Loki timed out",
    error_code: "upstream_unavailable",
    duration_ms: 5000,
    at: "2026-11-23T10:00:02Z",
  },
];

export const report: Report = {
  investigation_id: "inv-1",
  question: "Why are checkout requests failing?",
  summary: "Most probable cause: d-408 caused the incident (high confidence, 0.82).",
  severity: "SEV2",
  affected_services: ["api-gateway", "orders-service"],
  root_cause: rootCause,
  alternatives: [
    {
      ...rootCause,
      hypothesis_id: "db",
      title: "Slow queries on PostgreSQL",
      confidence: 0.3,
      band: "low",
      explanation: "2 contradicting finding(s)",
    },
  ],
  evidence: [
    { id: "ev-001", tool: "deployments.list_recent_deployments", summary: "5 deployments" },
  ],
  timeline: [
    { at: "2026-11-23T09:58:00Z", event: "orders-service: pool timeouts x82", source: "f-008" },
  ],
  references: [
    {
      kind: "similar_incident",
      title: "Order history N+1 query saturated the DB pool",
      source: "incidents/INC-2026-031.md",
      section: "INC-2026-031 > What happened",
      document_id: "INC-2026-031",
      evidence_id: "ev-006",
      incident_id: "INC-2026-031",
      superseded_by: null,
    },
    {
      kind: "runbook",
      title: "Rolling back a deployment",
      source: "runbooks/rb-rollback-procedure.md",
      section: "Procedure > Steps",
      document_id: "rb-rollback-procedure",
      evidence_id: "ev-007",
      incident_id: null,
      superseded_by: null,
    },
  ],
  recommendations: [{ ...proposal, status: "executed" }],
  actions_taken: [{ ...proposal, status: "executed" }],
  verification: {
    verdict: "resolved",
    service: "orders-service",
    before: { error_rate: 0.21 },
    after: { error_rate: 0.0 },
    details: "all degraded signals back to healthy: error_rate",
  },
  limitations: ["data comes from the SIMULATED environment"],
  stop_reason: "sufficient_confidence",
  rounds: 2,
  tool_calls: 14,
  markdown: "# Incident report",
};

export const overview: Overview = {
  services: [
    {
      service: "api-gateway",
      up: true,
      version: "3.8.0",
      uptime_s: 8600,
      restarts_last_hour: 0,
      firing_alerts: [],
      health: "healthy",
    },
    {
      service: "orders-service",
      up: true,
      version: "2.4.0",
      uptime_s: 300,
      restarts_last_hour: 2,
      firing_alerts: [{ alertname: "HighErrorRate" }],
      health: "degraded",
    },
  ],
  dependencies: [
    {
      name: "postgres",
      kind: "database",
      used_by: ["orders-service"],
      health: "healthy",
      evidence: ["pg_up=1.0"],
    },
  ],
  deployments: [
    {
      id: "d-408",
      service: "orders-service",
      kind: "deploy",
      version: "2.4.0",
      previous_version: "2.3.1",
      status: "succeeded",
      started_at: "2026-11-23T09:57:00Z",
      finished_at: "2026-11-23T09:57:30Z",
      changes_summary: "Order history: include line items",
      author: "m.okafor",
      commit_sha: "61b9cff",
      failure_reason: null,
    },
  ],
  golden_signals: {
    "orders-service": {
      service: "orders-service",
      at: "2026-11-23T10:00:00Z",
      version: "2.4.0",
      summary: "",
      signals: [
        { signal: "request_rate", unit: "req/s", value: 2.9 },
        { signal: "error_rate", unit: "ratio", value: 0.21 },
        { signal: "latency_p95", unit: "s", value: 2.4 },
      ],
    },
  },
  unavailable: [],
  active_investigations: [],
};

export const settings: PlatformSettings = {
  environment: { name: "development", simulated: true, version: "0.1.0" },
  llm: { provider: "gemini", model: "gemini-3.5-flash-lite", api_key_configured: false },
  agent: {
    reasoner: "rules",
    max_rounds: 3,
    max_tool_calls: 24,
    time_budget_s: 300,
    sufficient_confidence: 0.75,
    verification_wait_s: 45,
  },
  integrations: {
    mcp: {
      url: "http://127.0.0.1:8300",
      reachable: true,
      servers: [
        { name: "logs", enabled: true, read_only: true, tools: 4 },
        { name: "remediation", enabled: true, read_only: false, tools: 4 },
      ],
    },
    prometheus: "http://127.0.0.1:9090",
    loki: "http://127.0.0.1:3100",
    deployer: "http://127.0.0.1:8202",
    remediation_enabled: true,
    checkpointer: "postgres",
    event_store: "postgres",
  },
};
