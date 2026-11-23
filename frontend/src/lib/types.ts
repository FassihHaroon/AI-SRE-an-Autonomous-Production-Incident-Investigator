/**
 * Response types for the AI SRE API (`/api/v1`). They mirror the backend payloads in
 * `ai_sre.api.routes.*` and `ai_sre.agent.models`; fields the UI does not read are omitted or
 * kept loose (`Record<string, unknown>`).
 */

export type Health = "healthy" | "degraded" | "down" | "unknown" | (string & {});

// ---------------------------------------------------------------- platform

export interface ServiceStatus {
  service: string;
  up: boolean;
  version: string | null;
  uptime_s: number | null;
  restarts_last_hour: number;
  firing_alerts: { name?: string; alertname?: string; severity?: string; summary?: string }[];
  health: Health;
}

export interface Dependency {
  name: string;
  kind: string;
  used_by: string[];
  health: Health;
  evidence: string[];
}

export interface Deployment {
  id: string;
  service: string;
  kind: string;
  version: string;
  previous_version: string | null;
  status: string;
  started_at: string;
  finished_at: string | null;
  changes_summary: string;
  author: string;
  commit_sha: string | null;
  failure_reason: string | null;
}

export interface SignalValue {
  signal: string;
  unit: string;
  value: number | null;
}

export interface GoldenSignals {
  service: string;
  at: string;
  version: string | null;
  summary: string;
  signals: SignalValue[];
}

export interface Overview {
  services: ServiceStatus[];
  dependencies: Dependency[];
  deployments: Deployment[];
  golden_signals: Record<string, GoldenSignals>;
  unavailable: string[];
  active_investigations: InvestigationSummary[];
}

export type Point = [string, number | null];

export interface SeriesStats {
  min: number | null;
  max: number | null;
  avg: number | null;
  last: number | null;
  points: number;
}

export interface Series {
  service: string;
  signal: string;
  unit: string;
  points: Point[];
  stats?: SeriesStats;
  step_seconds?: number;
  baseline?: Point[];
  offset_minutes?: number;
}

export interface SeriesResponse {
  signal: string;
  unit: string;
  since_minutes: number;
  compare_offset_minutes: number | null;
  series: Series[];
  errors?: Record<string, string>;
}

export interface MetricCatalog {
  services: string[];
  signals: { signal: string; unit: string; services: string[] }[];
}

export interface LogEntry {
  timestamp: string;
  service: string;
  severity: string;
  message: string;
  endpoint: string | null;
  version: string | null;
  trace_id: string | null;
  error_type: string | null;
  error_message: string | null;
}

export interface ContainerStatus {
  service: string;
  up: boolean;
  started_at: string | null;
  restarts_last_hour: number;
  memory_bytes: number | null;
  memory_limit_bytes: number | null;
  memory_utilization: number | null;
  open_fds: number | null;
  oom_risk: boolean;
}

export interface ServiceDetail {
  service: string;
  status: ServiceStatus | null;
  metrics: GoldenSignals | null;
  container: ContainerStatus | null;
  logs: LogEntry[];
  error_logs: LogEntry[];
  deployments: Deployment[];
  series: Series[];
  unavailable?: string[];
}

// ---------------------------------------------------------------- investigations

export type InvestigationStatus =
  "queued" | "running" | "awaiting_approval" | "completed" | "failed" | "cancelled";

export type Band = "high" | "medium" | "low" | "insufficient";

export interface RootCause {
  hypothesis_id: string;
  title: string;
  category: string;
  component: string | null;
  trigger_ref: string | null;
  explanation: string;
  confidence: number;
  band: Band;
  evidence: string[];
  capped: string | null;
}

export interface InvestigationSummary {
  id: string;
  question: string;
  status: InvestigationStatus;
  severity: string | null;
  parent_id: string | null;
  reasoner: string;
  created_at: string;
  updated_at: string;
  root_cause: Pick<
    RootCause,
    "title" | "category" | "component" | "band" | "confidence" | "trigger_ref"
  > | null;
}

export interface Proposal {
  id: string;
  order: number;
  action: string;
  arguments: Record<string, unknown>;
  description: string;
  risk: "low" | "medium" | "high" | (string & {});
  expected_impact: string;
  reversible: boolean;
  executable: boolean;
  approval_request_id: string | null;
  status: string;
  result: unknown;
  error: string | null;
  references: string[];
}

export interface Approval {
  type: string;
  investigation_id: string;
  proposals: Proposal[];
  root_cause: RootCause | null;
}

export interface Reference {
  kind: string;
  title: string;
  source: string;
  section: string | null;
  document_id: string;
  evidence_id: string | null;
  incident_id: string | null;
  superseded_by: string | null;
}

export interface TimelineEntry {
  at: string;
  event: string;
  source: string | null;
}

export interface Verification {
  verdict: "resolved" | "improved" | "no_change" | "worse" | "not_verified";
  service: string | null;
  before: Record<string, number | null>;
  after: Record<string, number | null>;
  details: string;
}

export interface Report {
  investigation_id: string;
  question: string;
  summary: string;
  severity: string;
  affected_services: string[];
  root_cause: RootCause | null;
  alternatives: RootCause[];
  evidence: { id: string; tool: string; summary: string }[];
  timeline: TimelineEntry[];
  references: Reference[];
  recommendations: Proposal[];
  actions_taken: Proposal[];
  verification: Verification | null;
  limitations: string[];
  stop_reason: string;
  rounds: number;
  tool_calls: number;
  markdown: string;
}

export interface InvestigationRecord {
  id: string;
  question: string;
  status: InvestigationStatus;
  parent_id: string | null;
  severity: string | null;
  reasoner: string;
  limits: Record<string, number>;
  root_cause: RootCause | null;
  approval: Approval | null;
  report: Report | null;
  error: string | null;
  created_at: string;
  updated_at: string;
}

export interface PlannedCall {
  server: string;
  tool: string;
  purpose: string;
  priority: number;
  arguments: Record<string, unknown>;
  hypothesis: string | null;
}

export interface Plan {
  round: number;
  rationale: string;
  hypotheses_considered: string[];
  calls: PlannedCall[];
}

export interface Scope {
  question: string;
  services: string[];
  window_minutes: number;
  severity: string;
  symptoms: string[];
  missing_information: string[];
  assumptions: string[];
}

export interface Hypothesis {
  id: string;
  category: string;
  component: string | null;
  title: string;
  trigger_ref: string | null;
  supporting: string[] | number;
  contradicting: string[] | number;
  temporal: string;
  mechanism: string;
  historical_match?: string | null;
  rationale?: string;
  confidence: number;
  band: Band;
  breakdown: Record<string, number>;
}

export interface EvidenceItem {
  id: string;
  round: number;
  server: string;
  tool: string;
  ok: boolean;
  summary: string;
  error_code: string | null;
  duration_ms: number | null;
  at: string;
}

export interface InvestigationState {
  scope: Scope | null;
  round: number;
  plan: Plan | null;
  hypotheses: Hypothesis[];
  evidence: EvidenceItem[];
  timeline: TimelineEntry[];
  root_cause: RootCause | null;
  alternatives: RootCause[];
  proposals: Proposal[];
  verification: Verification | null;
  stop_reason: string | null;
  limitations: string[];
  report: Report | null;
}

export interface InvestigationDetail {
  investigation: InvestigationRecord;
  state: Partial<InvestigationState> | null;
}

export interface ToolCallDetail {
  id: string;
  round: number;
  server: string;
  tool: string;
  arguments: Record<string, unknown>;
  ok: boolean;
  summary: string;
  data: unknown;
  error_code: string | null;
  error_message: string | null;
  duration_ms: number | null;
  at: string;
}

/** One server-sent event from `/investigations/{id}/events`. */
export interface InvestigationEvent {
  id: number;
  type: string;
  data: Record<string, unknown>;
  at: string;
}

// ---------------------------------------------------------------- knowledge & settings

export interface IncidentDoc {
  document_id: string;
  incident_id: string | null;
  title: string;
  source: string;
  severity: string | null;
  category: string | null;
  services: string[];
  tags: string[];
  date: string | null;
  score?: number;
  similarity?: number;
  snippet?: string;
  section?: string;
}

export interface IncidentSearch {
  query: string | null;
  confident: boolean | null;
  incidents: IncidentDoc[];
}

export interface IncidentDetail {
  incident: IncidentDoc;
  sections: { section: string; content: string }[];
  similar: IncidentDoc[];
}

export interface AgentSettings {
  reasoner: "rules" | "llm";
  max_rounds: number;
  max_tool_calls: number;
  time_budget_s: number;
  sufficient_confidence: number;
  verification_wait_s: number;
}

export interface PlatformSettings {
  environment: { name: string; simulated: boolean; version: string };
  llm: { provider: string; model: string | null; api_key_configured: boolean };
  agent: AgentSettings;
  integrations: {
    mcp: {
      url: string;
      reachable: boolean;
      error?: string;
      servers: { name: string; enabled: boolean; read_only: boolean; tools: number }[];
    };
    prometheus: string;
    loki: string;
    deployer: string;
    remediation_enabled: boolean;
    checkpointer: string;
    event_store: string;
  };
}
