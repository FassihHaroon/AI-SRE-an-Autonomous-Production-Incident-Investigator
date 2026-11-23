import type { ReactNode } from "react";

import { cn } from "@/lib/cn";
import { percent } from "@/lib/format";
import { bandTone, toneText, type Tone } from "@/lib/status";

const dotColor: Record<Tone, string> = {
  ok: "bg-ok",
  warn: "bg-warn",
  danger: "bg-danger",
  neutral: "bg-faint",
  brand: "bg-brand",
  info: "bg-info",
};

export function StatusDot({
  tone,
  pulse,
  label,
  className,
}: {
  tone: Tone;
  pulse?: boolean;
  label?: string;
  className?: string;
}) {
  return (
    <span
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      className={cn(
        "inline-block size-2 shrink-0 rounded-full",
        dotColor[tone],
        pulse && "animate-pulse-dot",
        className,
      )}
    />
  );
}

export interface StatProps {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  tone?: Tone | undefined;
  icon?: ReactNode;
}

export function Stat({ label, value, hint, tone, icon }: StatProps) {
  return (
    <div className="rounded-lg border border-line bg-panel px-4 py-3">
      <div className="flex items-center justify-between text-xs text-muted">
        <span>{label}</span>
        {icon}
      </div>
      <div className={cn("mt-1 text-xl font-semibold tabular", tone ? toneText[tone] : "text-ink")}>
        {value}
      </div>
      {hint ? <div className="mt-0.5 truncate text-xs text-faint">{hint}</div> : null}
    </div>
  );
}

const barColor: Record<Tone, string> = {
  ok: "bg-ok",
  warn: "bg-warn",
  danger: "bg-danger",
  neutral: "bg-faint",
  brand: "bg-brand",
  info: "bg-info",
};

/** Horizontal confidence bar with the rubric band. The value comes from the agent's rubric. */
export function ConfidenceMeter({
  value,
  band,
  compact,
}: {
  value: number;
  band?: string;
  compact?: boolean;
}) {
  const tone = bandTone(band);
  const clamped = Math.max(0, Math.min(1, value));
  return (
    <div className={cn("flex items-center gap-2", compact ? "w-32" : "w-full")}>
      <div
        role="meter"
        aria-label="Confidence"
        aria-valuemin={0}
        aria-valuemax={1}
        aria-valuenow={clamped}
        aria-valuetext={`${percent(clamped)}${band ? ` (${band})` : ""}`}
        className="h-1.5 flex-1 overflow-hidden rounded-full bg-neutral-soft"
      >
        <div
          className={cn("h-full rounded-full", barColor[tone])}
          style={{ width: `${clamped * 100}%` }}
        />
      </div>
      <span className={cn("text-xs font-medium tabular", toneText[tone])}>
        {percent(clamped)}
        {band && !compact ? ` · ${band}` : ""}
      </span>
    </div>
  );
}

export function JsonView({ value, className }: { value: unknown; className?: string }) {
  return (
    <pre
      className={cn(
        "max-h-[32rem] overflow-auto rounded-md border border-line bg-surface p-3 font-mono text-xs leading-relaxed text-ink/90",
        className,
      )}
    >
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

export function KeyValue({ items }: { items: [ReactNode, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[minmax(7rem,auto)_1fr] gap-x-4 gap-y-1.5 text-sm">
      {items.map(([k, v], i) => (
        <div key={i} className="contents">
          <dt className="text-muted">{k}</dt>
          <dd className="min-w-0 break-words text-ink">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

export function SectionTitle({ children, actions }: { children: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-2 flex items-center justify-between gap-2">
      <h3 className="text-xs font-semibold tracking-wide text-muted uppercase">{children}</h3>
      {actions}
    </div>
  );
}
