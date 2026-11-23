/** Formatting helpers shared by tables, charts and stat tiles. */

export function formatValue(value: number | null | undefined, unit?: string): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  switch (unit) {
    case "ratio":
      return `${(value * 100).toFixed(value < 0.01 && value > 0 ? 2 : 1)}%`;
    case "s":
      return value < 1 ? `${Math.round(value * 1000)} ms` : `${value.toFixed(2)} s`;
    case "bytes":
      return value >= 1 << 30
        ? `${(value / (1 << 30)).toFixed(2)} GiB`
        : `${(value / (1 << 20)).toFixed(0)} MiB`;
    case "req/s":
    case "errors/s":
    case "failures/s":
      return `${value.toFixed(value < 10 ? 2 : 0)}/s`;
    case "cores":
      return `${value.toFixed(2)} cores`;
    default:
      return Number.isInteger(value) ? String(value) : value.toFixed(2);
  }
}

export function relativeTime(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return "—";
  const then = new Date(iso);
  const seconds = Math.round((now.getTime() - then.getTime()) / 1000);
  if (Number.isNaN(seconds)) return "—";
  if (seconds < 45) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours} h ago`;
  return `${Math.round(hours / 24)} d ago`;
}

export function clockTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleTimeString([], { hour12: false });
}

export function percent(value: number | null | undefined, digits = 0): string {
  return value === null || value === undefined ? "—" : `${(value * 100).toFixed(digits)}%`;
}

export function titleCase(text: string): string {
  return text.replace(/[_-]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
