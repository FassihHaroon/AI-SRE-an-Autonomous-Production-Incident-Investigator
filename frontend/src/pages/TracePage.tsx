import { useMemo, useState } from "react";
import { Link, useParams } from "react-router";

import { PageHeader } from "@/components/layout/PageHeader";
import {
  Badge,
  Card,
  CardBody,
  CardHeader,
  EmptyState,
  JsonView,
  LoadingBlock,
  QueryState,
  Stat,
} from "@/components/ui";
import { useTrace } from "@/features/evaluations/api";
import { InvestigationStatusBadge } from "@/features/investigations/components";
import { cn } from "@/lib/cn";
import { clockTime, titleCase } from "@/lib/format";
import type { InvestigationTrace, SpanKind, TraceSpan } from "@/lib/types";

const KIND_STYLE: Record<SpanKind, { bar: string; label: string }> = {
  investigation: { bar: "bg-brand/70", label: "investigation" },
  node: { bar: "bg-info/70", label: "node" },
  tool: { bar: "bg-ok/70", label: "tool" },
  llm: { bar: "bg-[#d46fd1]/80", label: "llm" },
};

function ms(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return value >= 1000
    ? `${(value / 1000).toFixed(2)} s`
    : `${value.toFixed(value < 10 ? 1 : 0)} ms`;
}

interface Row {
  span: TraceSpan;
  depth: number;
}

/** Spans of one trace (run segment) in tree order with their depth. */
function treeRows(spans: TraceSpan[]): Row[] {
  const children = new Map<string | null, TraceSpan[]>();
  const ids = new Set(spans.map((s) => s.span_id));
  for (const s of spans) {
    const parent = s.parent_id && ids.has(s.parent_id) ? s.parent_id : null;
    const list = children.get(parent) ?? [];
    list.push(s);
    children.set(parent, list);
  }
  for (const list of children.values())
    list.sort((a, b) => (a.start ?? "").localeCompare(b.start ?? ""));
  const out: Row[] = [];
  const walk = (parent: string | null, depth: number) => {
    for (const s of children.get(parent) ?? []) {
      out.push({ span: s, depth });
      walk(s.span_id, depth + 1);
    }
  };
  walk(null, 0);
  return out;
}

function Waterfall({
  spans,
  selected,
  onSelect,
}: {
  spans: TraceSpan[];
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  const rows = useMemo(() => treeRows(spans), [spans]);
  const starts = spans.map((s) => Date.parse(s.start ?? "")).filter((t) => !Number.isNaN(t));
  const ends = spans.map((s) => Date.parse(s.end ?? s.start ?? "")).filter((t) => !Number.isNaN(t));
  const t0 = Math.min(...starts);
  const total = Math.max(1, Math.max(...ends) - t0);
  return (
    <div className="overflow-x-auto" role="table" aria-label="Span waterfall">
      <div className="min-w-[720px]">
        {rows.map(({ span, depth }) => {
          const start = Date.parse(span.start ?? "") - t0;
          const width = Math.max(0.4, ((span.duration_ms ?? 0) / total) * 100);
          const kind = span.kind ?? "node";
          return (
            <button
              key={span.span_id}
              type="button"
              role="row"
              onClick={() => {
                onSelect(span.span_id);
              }}
              className={cn(
                "grid w-full grid-cols-[minmax(14rem,22rem)_1fr_5rem] items-center gap-2 rounded px-1 py-0.5 text-left text-xs hover:bg-raised/60",
                selected === span.span_id && "bg-brand-soft/50",
              )}
            >
              <span
                className="flex min-w-0 items-center gap-1.5"
                style={{ paddingLeft: depth * 14 }}
              >
                <span
                  className={cn("size-2 shrink-0 rounded-sm", KIND_STYLE[kind].bar)}
                  aria-hidden="true"
                />
                <span
                  className={cn(
                    "truncate font-mono",
                    span.status === "error" ? "text-danger" : "text-ink/90",
                  )}
                >
                  {span.name}
                </span>
              </span>
              <span className="relative h-3.5 rounded bg-neutral-soft/40">
                <span
                  className={cn(
                    "absolute top-0 h-full rounded",
                    span.status === "error" ? "bg-danger/80" : KIND_STYLE[kind].bar,
                  )}
                  style={{ left: `${(start / total) * 100}%`, width: `${width}%` }}
                />
              </span>
              <span className="text-right font-mono text-muted tabular">
                {ms(span.duration_ms)}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function SpanDetails({ span }: { span: TraceSpan | undefined }) {
  if (!span) {
    return <EmptyState title="Select a span" description="Click a row to see its attributes." />;
  }
  return (
    <div className="space-y-2 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-sm text-ink">{span.name}</span>
        <Badge>{span.kind}</Badge>
        {span.status === "error" ? <Badge tone="danger">error</Badge> : <Badge tone="ok">ok</Badge>}
      </div>
      <div className="text-muted">
        {clockTime(span.start)} · {ms(span.duration_ms)}
        {span.status_message ? ` · ${span.status_message}` : ""}
      </div>
      <JsonView value={span.attributes} className="max-h-80 break-all whitespace-pre-wrap" />
      <p className="text-faint">
        Attributes never include tool results, prompts or model output; arguments and user text are
        redacted.
      </p>
    </div>
  );
}

function TraceBody({ trace }: { trace: InvestigationTrace }) {
  const [selected, setSelected] = useState<string | null>(null);
  const s = trace.summary;
  const segments = useMemo(() => {
    const byTrace = new Map<string, TraceSpan[]>();
    for (const span of trace.spans) {
      const key = span.trace_id ?? "trace";
      byTrace.set(key, [...(byTrace.get(key) ?? []), span]);
    }
    return [...byTrace.values()];
  }, [trace.spans]);
  const selectedSpan = trace.spans.find((x) => x.span_id === selected);
  const result = s.result;

  if (!trace.spans.length) {
    return (
      <EmptyState
        title="No trace recorded"
        description="Traces are recorded for investigations started after tracing was enabled."
      />
    );
  }

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-8">
        <Stat
          label="Agent time"
          value={ms(s.duration_ms)}
          hint={`${segments.length} run segment(s)`}
        />
        <Stat label="Iterations" value={s.iterations} hint="investigation rounds" />
        <Stat label="Tool calls" value={s.tools.calls} hint={`avg ${ms(s.tools.avg_latency_ms)}`} />
        <Stat
          label="Tool errors"
          value={s.tools.errors}
          tone={s.tools.errors ? "danger" : undefined}
          hint={`${s.retries.tools} retries`}
        />
        <Stat label="Evidence" value={s.tools.evidence_ok} hint="successful results" />
        <Stat
          label="LLM calls"
          value={s.llm.calls}
          hint={s.llm.models.join(", ") || "rule-based"}
        />
        <Stat
          label="Tokens"
          value={s.llm.total_tokens.toLocaleString()}
          hint={`${s.llm.input_tokens.toLocaleString()} in / ${s.llm.output_tokens.toLocaleString()} out`}
        />
        <Stat
          label="Est. cost"
          value={`$${s.llm.cost_usd.toFixed(4)}`}
          hint={`LLM ${ms(s.llm.latency_ms)}`}
        />
      </div>

      {Object.keys(result).length ? (
        <Card>
          <CardHeader title="Final result" />
          <CardBody className="flex flex-wrap gap-x-6 gap-y-1 text-sm">
            {Object.entries(result).map(([k, v]) => (
              <span key={k}>
                <span className="text-muted">{titleCase(k)}:</span>{" "}
                <span className="text-ink">{typeof v === "number" ? v.toFixed(2) : String(v)}</span>
              </span>
            ))}
          </CardBody>
        </Card>
      ) : null}

      <div className="grid gap-4 xl:grid-cols-[2fr_1fr]">
        <Card className="min-w-0">
          <CardHeader
            title="Spans"
            description="OpenTelemetry spans: investigation → nodes → tool and LLM calls"
            actions={
              <span className="flex gap-2 text-[11px] text-muted">
                {(Object.keys(KIND_STYLE) as SpanKind[]).map((k) => (
                  <span key={k} className="flex items-center gap-1">
                    <span className={cn("size-2 rounded-sm", KIND_STYLE[k].bar)} />
                    {KIND_STYLE[k].label}
                  </span>
                ))}
              </span>
            }
          />
          <CardBody className="space-y-4">
            {segments.map((seg, i) => (
              <div key={seg[0]?.trace_id ?? i}>
                {segments.length > 1 ? (
                  <div className="mb-1 text-xs text-muted">
                    Segment {i + 1} · {clockTime(seg[0]?.start)}
                  </div>
                ) : null}
                <Waterfall spans={seg} selected={selected} onSelect={setSelected} />
              </div>
            ))}
          </CardBody>
        </Card>
        <Card className="min-w-0">
          <CardHeader title="Span details" />
          <CardBody>
            <SpanDetails span={selectedSpan} />
          </CardBody>
        </Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader title="Nodes" description="Executions and time per graph node" />
          <CardBody>
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-line text-left text-xs text-muted">
                  <th className="py-1.5 font-medium">Node</th>
                  <th className="py-1.5 text-right font-medium">Runs</th>
                  <th className="py-1.5 text-right font-medium">Retries</th>
                  <th className="py-1.5 text-right font-medium">Errors</th>
                  <th className="py-1.5 text-right font-medium">Total</th>
                </tr>
              </thead>
              <tbody className="tabular">
                {Object.entries(s.nodes)
                  .sort((a, b) => b[1].total_ms - a[1].total_ms)
                  .map(([name, n]) => (
                    <tr key={name} className="border-b border-line/60 last:border-0">
                      <td className="py-1.5 font-mono text-xs">{name}</td>
                      <td className="py-1.5 text-right">{n.executions}</td>
                      <td className="py-1.5 text-right">{n.retries}</td>
                      <td className={cn("py-1.5 text-right", n.errors && "text-danger")}>
                        {n.errors}
                      </td>
                      <td className="py-1.5 text-right">{ms(n.total_ms)}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Tools" description="Calls, errors, retries and latency per MCP tool" />
          <CardBody>
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-line text-left text-xs text-muted">
                  <th className="py-1.5 font-medium">Tool</th>
                  <th className="py-1.5 text-right font-medium">Calls</th>
                  <th className="py-1.5 text-right font-medium">Errors</th>
                  <th className="py-1.5 text-right font-medium">Retries</th>
                  <th className="py-1.5 text-right font-medium">Total</th>
                </tr>
              </thead>
              <tbody className="tabular">
                {Object.entries(s.tools.by_tool).map(([name, t]) => (
                  <tr key={name} className="border-b border-line/60 last:border-0">
                    <td className="py-1.5 font-mono text-xs">{name}</td>
                    <td className="py-1.5 text-right">{t.calls}</td>
                    <td className={cn("py-1.5 text-right", t.errors && "text-danger")}>
                      {t.errors}
                    </td>
                    <td className={cn("py-1.5 text-right", t.retries && "text-warn")}>
                      {t.retries}
                    </td>
                    <td className="py-1.5 text-right">{ms(t.total_ms)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardBody>
        </Card>
      </div>
    </div>
  );
}

export function TracePage() {
  const { id = "" } = useParams();
  const trace = useTrace(id);
  const status = trace.data?.status;

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: "Investigations", to: "/investigations" },
          { label: id, to: `/investigations/${id}` },
          { label: "Trace" },
        ]}
        title="Investigation trace"
        description={trace.data?.question}
        actions={
          <>
            {status ? <InvestigationStatusBadge status={status} /> : null}
            <Link
              to={`/investigations/${id}`}
              className="text-xs text-brand-strong hover:underline"
            >
              Back to workspace
            </Link>
          </>
        }
      />
      <QueryState
        query={trace}
        loading={<LoadingBlock rows={8} />}
        errorTitle="Trace not available"
      >
        {(t) => <TraceBody trace={t} />}
      </QueryState>
    </>
  );
}
