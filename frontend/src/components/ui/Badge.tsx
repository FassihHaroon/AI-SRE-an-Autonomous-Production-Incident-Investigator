import type { HTMLAttributes } from "react";

import { cn } from "@/lib/cn";

export type BadgeTone = "neutral" | "ok" | "warn" | "danger" | "brand" | "info";

const tones: Record<BadgeTone, string> = {
  neutral: "bg-neutral-soft text-muted",
  ok: "bg-ok-soft text-ok",
  warn: "bg-warn-soft text-warn",
  danger: "bg-danger-soft text-danger",
  brand: "bg-brand-soft text-brand-strong",
  info: "bg-info-soft text-info",
};

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  tone?: BadgeTone;
}

export function Badge({ tone = "neutral", className, ...props }: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] font-medium whitespace-nowrap",
        tones[tone],
        className,
      )}
      {...props}
    />
  );
}
