import { useMemo } from "react";
import { useSearchParams } from "react-router";

import { TimeSeriesChart, type ChartSeries } from "@/components/charts/TimeSeriesChart";
import { PageHeader } from "@/components/layout/PageHeader";
import {
  Card,
  CardBody,
  CardHeader,
  EmptyState,
  Field,
  LoadingBlock,
  QueryState,
  Segmented,
  Select,
  Spinner,
} from "@/components/ui";
import { useMetricCatalog, useSeries } from "@/features/platform/api";
import { SERIES_COLORS } from "@/lib/chart";
import { cn } from "@/lib/cn";
import { formatValue, titleCase } from "@/lib/format";
import type { MetricCatalog, SeriesResponse } from "@/lib/types";

const WINDOWS = [
  { key: "15", label: "15m" },
  { key: "30", label: "30m" },
  { key: "60", label: "1h" },
  { key: "180", label: "3h" },
  { key: "360", label: "6h" },
  { key: "1440", label: "24h" },
];

const COMPARE = [
  { key: "0", label: "Off" },
  { key: "60", label: "1 h earlier" },
  { key: "1440", label: "1 day earlier" },
  { key: "10080", label: "1 week earlier" },
];

function toSeries(data: SeriesResponse): ChartSeries[] {
  const out: ChartSeries[] = [];
  data.series.forEach((s, i) => {
    const color = SERIES_COLORS[i % SERIES_COLORS.length];
    out.push({ key: s.service, label: s.service, points: s.points, color });
    if (s.baseline?.length && s.offset_minutes) {
      out.push({
        key: `${s.service}__baseline`,
        label: `${s.service} (${s.offset_minutes} min earlier)`,
        points: s.baseline,
        color,
        dashed: true,
        // The API already aligns baseline timestamps onto the current window.
      });
    }
  });
  return out;
}

function stat(points: [string, number | null][], f: "min" | "max" | "avg" | "last"): number | null {
  const values = points.map((p) => p[1]).filter((v): v is number => v !== null);
  if (!values.length) return null;
  if (f === "min") return Math.min(...values);
  if (f === "max") return Math.max(...values);
  if (f === "last") return values[values.length - 1] ?? null;
  return values.reduce((a, b) => a + b, 0) / values.length;
}

function Explorer({ catalog }: { catalog: MetricCatalog }) {
  const [params, setParams] = useSearchParams();
  const signal = params.get("signal") ?? "latency_p95";
  const window = params.get("window") ?? "60";
  const compare = params.get("compare") ?? "0";
  const signalInfo = catalog.signals.find((s) => s.signal === signal) ?? catalog.signals[0];
  const available = signalInfo?.services ?? [];
  const requested = params.getAll("service");
  const services = (requested.length ? requested : available.slice(0, 2)).filter((s) =>
    available.includes(s),
  );

  const update = (patch: Record<string, string | string[]>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) {
      next.delete(k);
      if (Array.isArray(v))
        v.forEach((x) => {
          next.append(k, x);
        });
      else next.set(k, v);
    }
    setParams(next, { replace: true });
  };

  const toggleService = (s: string) => {
    const next = services.includes(s)
      ? services.filter((x) => x !== s)
      : [...services, s].slice(-3);
    update({ service: next });
  };

  const query = useSeries({
    services,
    signal: signalInfo?.signal ?? signal,
    sinceMinutes: Number(window),
    compareOffsetMinutes: Number(compare),
  });
  const chartSeries = useMemo(() => (query.data ? toSeries(query.data) : []), [query.data]);

  return (
    <div className="space-y-4">
      <Card>
        <CardBody className="grid gap-4 md:grid-cols-[16rem_1fr_auto]">
          <Field label="Signal" htmlFor="signal">
            <Select
              id="signal"
              value={signalInfo?.signal ?? signal}
              onChange={(e) => {
                update({ signal: e.target.value });
              }}
            >
              {catalog.signals.map((s) => (
                <option key={s.signal} value={s.signal}>
                  {titleCase(s.signal)} ({s.unit})
                </option>
              ))}
            </Select>
          </Field>
          <div className="space-y-1">
            <span className="block text-xs font-medium text-muted">Services (up to 3)</span>
            <div className="flex flex-wrap gap-1.5" role="group" aria-label="Services">
              {catalog.services.map((s) => {
                const enabled = available.includes(s);
                const on = services.includes(s);
                return (
                  <button
                    key={s}
                    type="button"
                    aria-pressed={on}
                    disabled={!enabled}
                    onClick={() => {
                      toggleService(s);
                    }}
                    title={enabled ? undefined : `${signal} is not exported by ${s}`}
                    className={cn(
                      "rounded-md border px-2.5 py-1 text-xs",
                      on
                        ? "border-brand bg-brand-soft text-ink"
                        : "border-line-strong text-muted hover:text-ink",
                      !enabled && "cursor-not-allowed opacity-40",
                    )}
                  >
                    {s}
                  </button>
                );
              })}
            </div>
          </div>
          <div className="space-y-2">
            <div className="space-y-1">
              <span className="block text-xs font-medium text-muted">Window</span>
              <Segmented
                label="Time window"
                options={WINDOWS}
                value={window}
                onChange={(v) => {
                  update({ window: v });
                }}
              />
            </div>
            <div className="space-y-1">
              <span className="block text-xs font-medium text-muted">Compare with</span>
              <Segmented
                label="Compare with"
                options={COMPARE}
                value={compare}
                onChange={(v) => {
                  update({ compare: v });
                }}
              />
            </div>
          </div>
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title={titleCase(signalInfo?.signal ?? signal)}
          description={
            Number(compare)
              ? `Dashed lines: the same window shifted ${COMPARE.find((c) => c.key === compare)?.label ?? ""}`
              : `Unit: ${signalInfo?.unit ?? ""}`
          }
          actions={query.isFetching ? <Spinner label="Updating" /> : null}
        />
        <CardBody>
          {!services.length ? (
            <EmptyState title="Select at least one service" />
          ) : (
            <QueryState query={query} loading={<LoadingBlock rows={8} />}>
              {(data) => (
                <div className="space-y-4">
                  <TimeSeriesChart series={chartSeries} unit={data.unit} height={320} />
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-line text-left text-xs text-muted">
                        <th className="py-1.5 pr-3 font-medium">Series</th>
                        <th className="py-1.5 pr-3 text-right font-medium">Last</th>
                        <th className="py-1.5 pr-3 text-right font-medium">Avg</th>
                        <th className="py-1.5 pr-3 text-right font-medium">Min</th>
                        <th className="py-1.5 text-right font-medium">Max</th>
                      </tr>
                    </thead>
                    <tbody className="tabular">
                      {chartSeries.map((s) => (
                        <tr key={s.key} className="border-b border-line/60 last:border-0">
                          <td className="py-1.5 pr-3">
                            <span
                              className="mr-2 inline-block h-0.5 w-3 align-middle"
                              style={{ background: s.color }}
                            />
                            {s.label}
                          </td>
                          {(["last", "avg", "min", "max"] as const).map((f) => (
                            <td key={f} className="py-1.5 pr-3 text-right last:pr-0">
                              {formatValue(stat(s.points, f), data.unit)}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </QueryState>
          )}
        </CardBody>
      </Card>
    </div>
  );
}

export function MetricsPage() {
  const catalog = useMetricCatalog();
  return (
    <>
      <PageHeader
        title="Metrics explorer"
        description="Query golden signals and saturation metrics from Prometheus, and compare against an earlier window."
      />
      <QueryState query={catalog} loading={<LoadingBlock rows={6} />}>
        {(c) => <Explorer catalog={c} />}
      </QueryState>
    </>
  );
}
