import {
  CheckCircle2,
  Circle,
  CircleDashed,
  ExternalLink,
  Loader2,
  Send,
  XCircle,
} from "lucide-react";
import { useState, type SyntheticEvent } from "react";
import { Link, useNavigate } from "react-router";

import {
  Badge,
  Button,
  ConfidenceMeter,
  EmptyState,
  ErrorState,
  StatusDot,
  Textarea,
} from "@/components/ui";
import { cn } from "@/lib/cn";
import { clockTime, relativeTime, titleCase } from "@/lib/format";
import { bandTone, investigationTone, severityTone, STATUS_LABELS } from "@/lib/status";
import type {
  EvidenceItem,
  Hypothesis,
  InvestigationEvent,
  InvestigationSummary,
} from "@/lib/types";

import { useStartInvestigation } from "./api";
import { NODE_ORDER, stepStates } from "./progress";

export function InvestigationStatusBadge({ status }: { status: string }) {
  const tone = investigationTone(status);
  return (
    <Badge tone={tone}>
      <StatusDot tone={tone} pulse={status === "running" || status === "queued"} />
      {STATUS_LABELS[status] ?? titleCase(status)}
    </Badge>
  );
}

export function SeverityBadge({ severity }: { severity: string | null | undefined }) {
  if (!severity) return null;
  return <Badge tone={severityTone(severity)}>{severity}</Badge>;
}

const EXAMPLES = [
  "Why are checkout requests failing?",
  "Why is orders-service latency elevated?",
  "Why is the fulfilment queue backing up?",
  "Did the last deployment cause errors on api-gateway?",
];

/** Free-text question box that starts an investigation and navigates to its workspace. */
export function AskBox({ initial = "", compact = false }: { initial?: string; compact?: boolean }) {
  const [question, setQuestion] = useState(initial);
  const start = useStartInvestigation();
  const navigate = useNavigate();
  const tooShort = question.trim().length < 8;

  const submit = (e: SyntheticEvent) => {
    e.preventDefault();
    if (tooShort || start.isPending) return;
    start.mutate(question.trim(), {
      onSuccess: (res) => {
        void navigate(`/investigations/${res.id}`);
      },
    });
  };

  return (
    <form onSubmit={submit} className="space-y-2" aria-label="Ask the AI SRE">
      <label htmlFor="ask-question" className="sr-only">
        Describe the incident or ask a question
      </label>
      <div className="relative">
        <Textarea
          id="ask-question"
          rows={compact ? 2 : 3}
          value={question}
          maxLength={1000}
          placeholder="Describe the symptom, e.g. “Why are checkout requests failing since 10 minutes ago?”"
          onChange={(e) => {
            setQuestion(e.target.value);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit(e);
          }}
          className="pr-28"
        />
        <Button
          type="submit"
          size="sm"
          className="absolute right-2 bottom-2"
          disabled={tooShort || start.isPending}
        >
          {start.isPending ? (
            <Loader2 className="size-4 animate-spin" aria-hidden="true" />
          ) : (
            <Send className="size-4" aria-hidden="true" />
          )}
          Investigate
        </Button>
      </div>
      {start.error ? (
        <ErrorState error={start.error} title="Could not start the investigation" />
      ) : null}
      {!compact ? (
        <div className="flex flex-wrap gap-1.5">
          {EXAMPLES.map((ex) => (
            <button
              key={ex}
              type="button"
              onClick={() => {
                setQuestion(ex);
              }}
              className="rounded-full border border-line px-2.5 py-0.5 text-xs text-muted hover:border-line-strong hover:text-ink"
            >
              {ex}
            </button>
          ))}
        </div>
      ) : null}
    </form>
  );
}

export function InvestigationTable({
  items,
  showRootCause = true,
}: {
  items: InvestigationSummary[];
  showRootCause?: boolean;
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-line text-left text-xs text-muted">
            <th className="py-2 pr-3 font-medium">Question</th>
            <th className="py-2 pr-3 font-medium">Status</th>
            {showRootCause ? <th className="py-2 pr-3 font-medium">Root cause</th> : null}
            <th className="py-2 pr-3 font-medium">Severity</th>
            <th className="py-2 text-right font-medium">Started</th>
          </tr>
        </thead>
        <tbody>
          {items.map((inv) => (
            <tr key={inv.id} className="border-b border-line/60 last:border-0 hover:bg-raised/50">
              <td className="max-w-md py-2 pr-3">
                <Link
                  to={`/investigations/${inv.id}`}
                  className="font-medium text-ink hover:text-brand-strong"
                >
                  {inv.question}
                </Link>
                <div className="font-mono text-[11px] text-faint">
                  {inv.id}
                  {inv.parent_id ? ` · follow-up of ${inv.parent_id}` : ""}
                </div>
              </td>
              <td className="py-2 pr-3">
                <InvestigationStatusBadge status={inv.status} />
              </td>
              {showRootCause ? (
                <td className="max-w-xs py-2 pr-3">
                  {inv.root_cause ? (
                    <div className="space-y-1">
                      <div className="truncate text-xs text-ink" title={inv.root_cause.title}>
                        {inv.root_cause.title}
                      </div>
                      <ConfidenceMeter
                        value={inv.root_cause.confidence}
                        band={inv.root_cause.band}
                        compact
                      />
                    </div>
                  ) : (
                    <span className="text-xs text-faint">—</span>
                  )}
                </td>
              ) : null}
              <td className="py-2 pr-3">
                <SeverityBadge severity={inv.severity} />
              </td>
              <td
                className="py-2 text-right text-xs whitespace-nowrap text-muted"
                title={inv.created_at}
              >
                {relativeTime(inv.created_at)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ------------------------------------------------------------------ progress

export function ProgressSteps({
  events,
  status,
  round,
}: {
  events: InvestigationEvent[];
  status: string;
  round?: number | undefined;
}) {
  const states = stepStates(events, status);
  return (
    <ol className="space-y-0.5" aria-label="Investigation progress">
      {NODE_ORDER.map(([node, label]) => {
        const state = states[node] ?? "pending";
        const Icon =
          state === "done"
            ? CheckCircle2
            : state === "active"
              ? Loader2
              : state === "failed"
                ? XCircle
                : node === "approval" || node === "remediation_execution" || node === "verification"
                  ? CircleDashed
                  : Circle;
        return (
          <li
            key={node}
            data-state={state}
            className={cn(
              "flex items-center gap-2 rounded px-1.5 py-1 text-xs",
              state === "active" && "bg-info-soft text-ink",
              state === "done" && "text-ink/80",
              state === "pending" && "text-faint",
              state === "failed" && "text-danger",
            )}
          >
            <Icon
              aria-hidden="true"
              className={cn(
                "size-3.5 shrink-0",
                state === "done" && "text-ok",
                state === "active" && "animate-spin text-info",
              )}
            />
            <span>{label}</span>
            {node === "tool_selection" && round ? (
              <span className="ml-auto text-faint tabular">round {round}</span>
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}

// ------------------------------------------------------------------ live timeline

function describe(
  event: InvestigationEvent,
): { text: string; tone: Parameters<typeof StatusDot>[0]["tone"] } | null {
  const d = event.data;
  const s = (k: string) => (typeof d[k] === "string" ? d[k] : "");
  switch (event.type) {
    case "status":
      return {
        text: `Status: ${STATUS_LABELS[s("status")] ?? s("status")}`,
        tone: investigationTone(s("status")),
      };
    case "node_started":
      return { text: `${s("label")}…`, tone: "info" };
    case "node_completed":
      return null;
    case "scope": {
      const services = Array.isArray(d.services) ? (d.services as string[]).join(", ") : "";
      return {
        text: `Scoped to ${services || "all services"} · last ${String(d.window_minutes)} min`,
        tone: "neutral",
      };
    }
    case "plan": {
      const calls = Array.isArray(d.calls) ? d.calls.length : 0;
      return {
        text: `Round ${String(d.round)} plan: ${calls} tool call(s). ${s("rationale")}`,
        tone: "brand",
      };
    }
    case "tool_call":
      return {
        text: `${s("server")}.${s("tool")}: ${d.ok ? s("summary") : `failed (${s("error_code")})`}`,
        tone: d.ok ? "neutral" : "danger",
      };
    case "evidence_analyzed":
      return { text: `Analysed evidence: ${String(d.findings)} finding(s)`, tone: "neutral" };
    case "hypotheses": {
      const list = Array.isArray(d.hypotheses) ? (d.hypotheses as Hypothesis[]) : [];
      const top = list[0];
      return {
        text: top
          ? `${list.length} hypotheses; leading: ${top.title} (${Math.round(top.confidence * 100)}%)`
          : "No hypotheses yet",
        tone: "brand",
      };
    }
    case "decision":
      return {
        text: `Decision: ${titleCase(s("stop_reason") || s("next") || "continue")}`,
        tone: "neutral",
      };
    case "root_cause": {
      const rc = d.root_cause as { title?: string; confidence?: number } | null;
      return {
        text: rc?.title
          ? `Root cause: ${rc.title} (${Math.round((rc.confidence ?? 0) * 100)}%)`
          : "No root cause identified",
        tone: rc?.title ? "ok" : "warn",
      };
    }
    case "proposals": {
      const n = Array.isArray(d.proposals) ? d.proposals.length : 0;
      return { text: `${n} remediation proposal(s)`, tone: "brand" };
    }
    case "approval_required":
      return { text: "Waiting for human approval", tone: "warn" };
    case "approval_decision":
      return {
        text: `${d.approved ? "Approved" : "Rejected"} ${s("proposal_id")} by ${s("approver")}${s("comment") ? `: “${s("comment")}”` : ""}`,
        tone: d.approved ? "ok" : "warn",
      };
    case "cancel_requested":
      return { text: "Cancellation requested", tone: "warn" };
    case "verification": {
      return {
        text: `Verification: ${s("verdict") || "n/a"}. ${s("details")}`,
        tone: s("verdict") === "resolved" ? "ok" : "warn",
      };
    }
    case "report_ready":
      return { text: "Report ready", tone: "ok" };
    case "error":
      return {
        text: `Error${s("node") ? ` in ${s("node")}` : ""}: ${s("error") || s("message")}`,
        tone: "danger",
      };
    default:
      return { text: event.type, tone: "neutral" };
  }
}

export function LiveTimeline({
  events,
  investigationId,
}: {
  events: InvestigationEvent[];
  investigationId: string;
}) {
  const rows = events
    .map((e) => ({ e, d: describe(e) }))
    .filter(
      (r): r is { e: InvestigationEvent; d: NonNullable<ReturnType<typeof describe>> } =>
        r.d !== null,
    );
  if (!rows.length) {
    return (
      <EmptyState
        title="Waiting for events"
        description="Live progress appears here as the agent works."
      />
    );
  }
  return (
    <ol className="space-y-1.5" aria-label="Live timeline" aria-live="polite">
      {rows.map(({ e, d }) => (
        <li key={`${e.id}-${e.type}`} className="flex animate-fade-in items-start gap-2 text-xs">
          <span className="w-16 shrink-0 pt-px font-mono text-faint tabular">
            {clockTime(e.at)}
          </span>
          <StatusDot tone={d.tone} className="mt-1" />
          <span className="min-w-0 flex-1 break-words text-ink/90">
            {d.text}
            {e.type === "tool_call" && typeof e.data.id === "string" ? (
              <Link
                to={`/investigations/${investigationId}/tools/${e.data.id}`}
                className="ml-1.5 inline-flex items-center gap-0.5 text-brand-strong hover:underline"
              >
                {e.data.id}
                <ExternalLink className="size-3" aria-hidden="true" />
              </Link>
            ) : null}
          </span>
        </li>
      ))}
    </ol>
  );
}

// ------------------------------------------------------------------ hypotheses & evidence

function count(v: string[] | number): number {
  return Array.isArray(v) ? v.length : v;
}

export function HypothesesList({ hypotheses }: { hypotheses: Hypothesis[] }) {
  if (!hypotheses.length) {
    return (
      <EmptyState
        title="No hypotheses yet"
        description="Hypotheses are formed after the first round of evidence."
      />
    );
  }
  return (
    <ul className="space-y-2">
      {hypotheses.map((h, i) => (
        <li
          key={h.id}
          className={cn(
            "rounded-md border px-3 py-2.5",
            i === 0 ? "border-brand/40 bg-brand-soft/40" : "border-line",
          )}
        >
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="text-sm font-medium text-ink">{h.title}</div>
              <div className="mt-0.5 flex flex-wrap gap-1">
                <Badge>{titleCase(h.category)}</Badge>
                {h.component ? <Badge>{h.component}</Badge> : null}
                <Badge tone="ok">{count(h.supporting)} supporting</Badge>
                {count(h.contradicting) ? (
                  <Badge tone="danger">{count(h.contradicting)} contradicting</Badge>
                ) : null}
                <Badge>temporal: {h.temporal}</Badge>
                <Badge>mechanism: {h.mechanism}</Badge>
                {h.historical_match ? (
                  <Badge tone="info">matches {h.historical_match}</Badge>
                ) : null}
              </div>
            </div>
            <div className="w-40 shrink-0">
              <ConfidenceMeter value={h.confidence} band={h.band} />
            </div>
          </div>
          {Object.keys(h.breakdown).length ? (
            <div className="mt-2 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-muted tabular">
              {Object.entries(h.breakdown).map(([k, v]) => (
                <span key={k} className={v < 0 ? "text-danger" : undefined}>
                  {k} {v >= 0 ? "+" : ""}
                  {v.toFixed(2)}
                </span>
              ))}
            </div>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

export function EvidenceTable({
  evidence,
  investigationId,
  highlight = [],
}: {
  evidence: EvidenceItem[];
  investigationId: string;
  highlight?: string[] | undefined;
}) {
  if (!evidence.length) {
    return (
      <EmptyState
        title="No tool calls yet"
        description="Each MCP tool call the agent makes is listed here."
      />
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-line text-left text-xs text-muted">
            <th className="py-2 pr-3 font-medium">ID</th>
            <th className="py-2 pr-3 font-medium">Round</th>
            <th className="py-2 pr-3 font-medium">Tool</th>
            <th className="py-2 pr-3 font-medium">Result</th>
            <th className="py-2 text-right font-medium">Duration</th>
          </tr>
        </thead>
        <tbody>
          {evidence.map((ev) => (
            <tr
              key={ev.id}
              className={cn(
                "border-b border-line/60 align-top last:border-0 hover:bg-raised/50",
                highlight.includes(ev.id) && "bg-brand-soft/30",
              )}
            >
              <td className="py-1.5 pr-3 font-mono text-xs">
                <Link
                  to={`/investigations/${investigationId}/tools/${ev.id}`}
                  className="text-brand-strong hover:underline"
                >
                  {ev.id}
                </Link>
              </td>
              <td className="py-1.5 pr-3 text-xs text-muted tabular">{ev.round}</td>
              <td className="py-1.5 pr-3 font-mono text-xs whitespace-nowrap text-ink">
                {ev.server}.{ev.tool}
              </td>
              <td className="py-1.5 pr-3 text-xs">
                {ev.ok ? (
                  <span className="text-ink/85">{ev.summary}</span>
                ) : (
                  <span className="text-danger">
                    {ev.error_code ?? "error"}: {ev.summary}
                  </span>
                )}
              </td>
              <td className="py-1.5 text-right font-mono text-xs whitespace-nowrap text-muted tabular">
                {ev.duration_ms !== null ? `${Math.round(ev.duration_ms)} ms` : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function TimelineList({
  entries,
}: {
  entries: { at: string; event: string; source: string | null }[];
}) {
  if (!entries.length) return <EmptyState title="No incident timeline yet" />;
  return (
    <ol className="relative space-y-2 border-l border-line pl-4">
      {entries.map((t, i) => (
        <li key={`${t.at}-${i}`} className="text-xs">
          <span
            className="absolute -left-[3px] mt-1.5 size-1.5 rounded-full bg-line-strong"
            aria-hidden="true"
          />
          <span className="font-mono text-faint tabular">{clockTime(t.at)}</span>{" "}
          <span className="text-ink/90">{t.event}</span>
          {t.source ? <span className="ml-1 font-mono text-faint">[{t.source}]</span> : null}
        </li>
      ))}
    </ol>
  );
}

export function BandBadge({ band }: { band: string }) {
  return <Badge tone={bandTone(band)}>{band}</Badge>;
}
