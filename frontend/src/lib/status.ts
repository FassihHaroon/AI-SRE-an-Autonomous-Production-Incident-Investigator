/** Maps backend status vocabularies to the UI's semantic tones. */

export type Tone = "ok" | "warn" | "danger" | "neutral" | "brand" | "info";

export const toneText: Record<Tone, string> = {
  ok: "text-ok",
  warn: "text-warn",
  danger: "text-danger",
  neutral: "text-muted",
  brand: "text-brand-strong",
  info: "text-info",
};

export function healthTone(health: string | null | undefined): Tone {
  switch (health) {
    case "healthy":
    case "ok":
    case "up":
      return "ok";
    case "degraded":
    case "warning":
      return "warn";
    case "down":
    case "unhealthy":
    case "critical":
      return "danger";
    default:
      return "neutral";
  }
}

export function investigationTone(status: string | null | undefined): Tone {
  switch (status) {
    case "running":
    case "queued":
      return "info";
    case "awaiting_approval":
      return "warn";
    case "completed":
      return "ok";
    case "failed":
      return "danger";
    default:
      return "neutral";
  }
}

export const STATUS_LABELS: Record<string, string> = {
  queued: "Queued",
  running: "Running",
  awaiting_approval: "Awaiting approval",
  completed: "Completed",
  failed: "Failed",
  cancelled: "Cancelled",
};

export function bandTone(band: string | null | undefined): Tone {
  switch (band) {
    case "high":
      return "ok";
    case "medium":
      return "brand";
    case "low":
      return "warn";
    case "insufficient":
      return "danger";
    default:
      return "neutral";
  }
}

export function riskTone(risk: string | null | undefined): Tone {
  switch (risk) {
    case "low":
      return "ok";
    case "medium":
      return "warn";
    case "high":
      return "danger";
    default:
      return "neutral";
  }
}

export function severityTone(severity: string | null | undefined): Tone {
  switch (severity) {
    case "SEV1":
      return "danger";
    case "SEV2":
      return "warn";
    case "SEV3":
      return "info";
    default:
      return "neutral";
  }
}

export function deploymentTone(status: string | null | undefined): Tone {
  if (status === "succeeded") return "ok";
  if (status === "failed") return "danger";
  if (status === "in_progress" || status === "running") return "info";
  return "neutral";
}

export function logTone(severity: string | null | undefined): Tone {
  const s = (severity ?? "").toUpperCase();
  if (s === "ERROR" || s === "CRITICAL" || s === "FATAL") return "danger";
  if (s === "WARNING" || s === "WARN") return "warn";
  return "neutral";
}

export function isTerminal(status: string | null | undefined): boolean {
  return status === "completed" || status === "failed" || status === "cancelled";
}
