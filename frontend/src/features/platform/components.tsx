import { AlertTriangle, RotateCcw } from "lucide-react";
import { Link } from "react-router";

import { Badge, EmptyState, StatusDot } from "@/components/ui";
import { cn } from "@/lib/cn";
import { clockTime, formatValue, relativeTime, titleCase } from "@/lib/format";
import { deploymentTone, healthTone, logTone } from "@/lib/status";
import type { Dependency, Deployment, GoldenSignals, LogEntry, ServiceStatus } from "@/lib/types";

function signal(gs: GoldenSignals | undefined, name: string): number | null {
  return gs?.signals.find((s) => s.signal === name)?.value ?? null;
}

export function HealthBadge({ health }: { health: string }) {
  const tone = healthTone(health);
  return (
    <Badge tone={tone}>
      <StatusDot tone={tone} />
      {titleCase(health)}
    </Badge>
  );
}

function uptime(seconds: number | null): string {
  if (seconds === null) return "—";
  if (seconds < 3600) return `${Math.round(seconds / 60)} min`;
  if (seconds < 172800) return `${(seconds / 3600).toFixed(1)} h`;
  return `${Math.round(seconds / 86400)} d`;
}

/** Health table with live golden signals (request rate, error ratio, p95 latency). */
export function ServiceHealthTable({
  services,
  signals,
}: {
  services: ServiceStatus[];
  signals: Record<string, GoldenSignals>;
}) {
  if (!services.length) return <EmptyState title="No services reported" />;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-line text-left text-xs text-muted">
            <th className="py-2 pr-3 font-medium">Service</th>
            <th className="py-2 pr-3 font-medium">Health</th>
            <th className="py-2 pr-3 font-medium">Version</th>
            <th className="py-2 pr-3 text-right font-medium">Req/s</th>
            <th className="py-2 pr-3 text-right font-medium">Errors</th>
            <th className="py-2 pr-3 text-right font-medium">p95</th>
            <th className="py-2 pr-3 text-right font-medium">Uptime</th>
            <th className="py-2 text-right font-medium">Restarts/h</th>
          </tr>
        </thead>
        <tbody>
          {services.map((s) => {
            const gs = signals[s.service];
            const err = signal(gs, "error_rate");
            const alerts = s.firing_alerts.length;
            return (
              <tr
                key={s.service}
                className="border-b border-line/60 last:border-0 hover:bg-raised/50"
              >
                <td className="py-2 pr-3">
                  <Link
                    to={`/services/${s.service}`}
                    className="font-medium text-ink hover:text-brand-strong"
                  >
                    {s.service}
                  </Link>
                  {alerts ? (
                    <span className="ml-2 inline-flex items-center gap-1 text-xs text-danger">
                      <AlertTriangle className="size-3" aria-hidden="true" />
                      {alerts} alert{alerts > 1 ? "s" : ""}
                    </span>
                  ) : null}
                </td>
                <td className="py-2 pr-3">
                  <HealthBadge health={s.up ? s.health : "down"} />
                </td>
                <td className="py-2 pr-3 font-mono text-xs text-muted">{s.version ?? "—"}</td>
                <td className="py-2 pr-3 text-right tabular">
                  {formatValue(signal(gs, "request_rate"), "req/s")}
                </td>
                <td
                  className={cn(
                    "py-2 pr-3 text-right tabular",
                    err !== null && err > 0.02 && "text-danger",
                  )}
                >
                  {formatValue(err, "ratio")}
                </td>
                <td className="py-2 pr-3 text-right tabular">
                  {formatValue(signal(gs, "latency_p95"), "s")}
                </td>
                <td className="py-2 pr-3 text-right text-muted tabular">{uptime(s.uptime_s)}</td>
                <td
                  className={cn(
                    "py-2 text-right tabular",
                    s.restarts_last_hour ? "text-warn" : "text-muted",
                  )}
                >
                  {s.restarts_last_hour}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function DependencyList({ dependencies }: { dependencies: Dependency[] }) {
  if (!dependencies.length) return <EmptyState title="No dependencies reported" />;
  return (
    <ul className="divide-y divide-line">
      {dependencies.map((d) => (
        <li key={d.name} className="py-2">
          <div className="flex items-center justify-between gap-2">
            <span className="flex items-center gap-2 text-sm text-ink">
              <StatusDot tone={healthTone(d.health)} label={d.health} />
              {d.name}
              <span className="text-xs text-faint">{titleCase(d.kind)}</span>
            </span>
            <span className="truncate text-xs text-muted">used by {d.used_by.join(", ")}</span>
          </div>
          {d.evidence.length ? (
            <div className="mt-0.5 pl-4 font-mono text-[11px] text-faint">
              {d.evidence.join(" · ")}
            </div>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

export function DeploymentList({
  deployments,
  limit,
}: {
  deployments: Deployment[];
  limit?: number;
}) {
  const rows = limit ? deployments.slice(0, limit) : deployments;
  if (!rows.length) return <EmptyState title="No deployments in this window" />;
  return (
    <ul className="divide-y divide-line">
      {rows.map((d) => (
        <li key={d.id} className="flex items-start gap-3 py-2">
          <span className="mt-0.5">
            {d.kind === "rollback" ? (
              <RotateCcw className="size-4 text-warn" aria-label="rollback" />
            ) : (
              <StatusDot tone={deploymentTone(d.status)} className="mt-1" label={d.status} />
            )}
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-x-2 text-sm">
              <span className="font-mono text-xs text-faint">{d.id}</span>
              <Link
                to={`/services/${d.service}`}
                className="font-medium text-ink hover:text-brand-strong"
              >
                {d.service}
              </Link>
              <span className="font-mono text-xs text-muted">
                {d.previous_version ?? "?"} → {d.version}
              </span>
              {d.status !== "succeeded" ? (
                <Badge tone={deploymentTone(d.status)}>{d.status}</Badge>
              ) : null}
            </div>
            <div className="truncate text-xs text-muted" title={d.changes_summary}>
              {d.changes_summary}
            </div>
            {d.failure_reason ? (
              <div className="text-xs text-danger">{d.failure_reason}</div>
            ) : null}
          </div>
          <div className="shrink-0 text-right text-xs text-muted">
            <div title={d.started_at}>{relativeTime(d.started_at)}</div>
            <div className="text-faint">{d.author}</div>
          </div>
        </li>
      ))}
    </ul>
  );
}

export function LogTable({ entries }: { entries: LogEntry[] }) {
  if (!entries.length) return <EmptyState title="No log lines in this window" />;
  return (
    <div className="max-h-96 overflow-auto rounded-md border border-line bg-surface">
      <table className="w-full font-mono text-[11px]">
        <tbody>
          {entries.map((l, i) => {
            const tone = logTone(l.severity);
            return (
              <tr
                key={`${l.timestamp}-${i}`}
                className="border-b border-line/50 align-top last:border-0"
              >
                <td className="py-1 pr-2 pl-2 whitespace-nowrap text-faint">
                  {clockTime(l.timestamp)}
                </td>
                <td
                  className={cn(
                    "py-1 pr-2 font-semibold",
                    tone === "danger"
                      ? "text-danger"
                      : tone === "warn"
                        ? "text-warn"
                        : "text-muted",
                  )}
                >
                  {l.severity}
                </td>
                <td className="py-1 pr-2 whitespace-nowrap text-muted">{l.service}</td>
                <td className="py-1 pr-2 break-all text-ink/90">
                  {l.message}
                  {l.error_type ? <span className="ml-1 text-danger">[{l.error_type}]</span> : null}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
