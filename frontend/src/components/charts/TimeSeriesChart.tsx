import { useMemo } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { EmptyState } from "@/components/ui";
import { SERIES_COLORS } from "@/lib/chart";
import { formatValue } from "@/lib/format";
import type { Point } from "@/lib/types";

export interface ChartSeries {
  key: string;
  label: string;
  points: Point[];
  color?: string | undefined;
  dashed?: boolean | undefined;
}

interface Row {
  t: number;
  [key: string]: number | null;
}

function merge(series: ChartSeries[]): Row[] {
  const rows = new Map<number, Row>();
  for (const s of series) {
    for (const [iso, value] of s.points) {
      // Bucket to the second so series sampled on the same step line up.
      const t = Math.round(Date.parse(iso) / 1000) * 1000;
      if (Number.isNaN(t)) continue;
      const row = rows.get(t) ?? { t };
      row[s.key] = value;
      rows.set(t, row);
    }
  }
  return [...rows.values()].sort((a, b) => a.t - b.t);
}

function tick(t: number): string {
  return new Date(t).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}

export function TimeSeriesChart({
  series,
  unit,
  height = 220,
  showLegend = true,
}: {
  series: ChartSeries[];
  unit?: string;
  height?: number;
  showLegend?: boolean;
}) {
  const rows = useMemo(() => merge(series), [series]);
  const hasData = rows.some((r) => series.some((s) => typeof r[s.key] === "number"));
  if (!hasData) {
    return (
      <EmptyState
        title="No samples in this window"
        description="Prometheus returned no data points for the selected range."
      />
    );
  }
  return (
    <div style={{ height }} className="w-full" data-testid="timeseries-chart">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={rows} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="#222734" strokeDasharray="3 3" vertical={false} />
          <XAxis
            dataKey="t"
            type="number"
            scale="time"
            domain={["dataMin", "dataMax"]}
            tickFormatter={tick}
            stroke="#5c6478"
            tick={{ fontSize: 11 }}
            tickLine={false}
            axisLine={{ stroke: "#222734" }}
            minTickGap={40}
          />
          <YAxis
            stroke="#5c6478"
            tick={{ fontSize: 11 }}
            tickLine={false}
            axisLine={false}
            width={64}
            tickFormatter={(v: number) => formatValue(v, unit)}
          />
          <Tooltip
            contentStyle={{
              background: "#161a22",
              border: "1px solid #2e3445",
              borderRadius: 6,
              fontSize: 12,
            }}
            labelStyle={{ color: "#8a93a6" }}
            labelFormatter={(t) => new Date(Number(t)).toLocaleString([], { hour12: false })}
            formatter={(v) => formatValue(typeof v === "number" ? v : null, unit)}
          />
          {showLegend ? <Legend wrapperStyle={{ fontSize: 11, color: "#8a93a6" }} /> : null}
          {series.map((s, i) => (
            <Line
              key={s.key}
              dataKey={s.key}
              name={s.label}
              type="monotone"
              stroke={s.color ?? SERIES_COLORS[i % SERIES_COLORS.length] ?? "#6d7dff"}
              strokeWidth={s.dashed ? 1.25 : 1.75}
              {...(s.dashed ? { strokeDasharray: "4 3" } : {})}
              strokeOpacity={s.dashed ? 0.7 : 1}
              dot={false}
              connectNulls
              isAnimationActive={false}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
