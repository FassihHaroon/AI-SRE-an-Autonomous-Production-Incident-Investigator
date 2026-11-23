import type { HTMLAttributes } from "react";

import { cn } from "@/lib/cn";

export type BadgeTone = "neutral" | "ok" | "warn" | "danger" | "brand";

const tones: Record<BadgeTone, string> = {
  neutral: "bg-neutral-soft text-muted",
  ok: "bg-ok-soft text-ok",
  warn: "bg-warn-soft text-warn",
  danger: "bg-danger-soft text-danger",
  brand: "bg-sky-100 text-brand-strong",
};

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  tone?: BadgeTone;
}

export function Badge({ tone = "neutral", className, ...props }: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium",
        tones[tone],
        className,
      )}
      {...props}
    />
  );
}
