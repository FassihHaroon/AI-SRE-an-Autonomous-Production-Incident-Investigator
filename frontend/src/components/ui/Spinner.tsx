import { cn } from "@/lib/cn";

export interface SpinnerProps {
  label?: string;
  className?: string;
}

export function Spinner({ label = "Loading", className }: SpinnerProps) {
  return (
    <span
      role="status"
      className={cn("inline-flex items-center gap-2 text-sm text-muted", className)}
    >
      <span
        aria-hidden="true"
        className="size-4 animate-spin rounded-full border-2 border-line border-t-brand"
      />
      <span>{label}</span>
    </span>
  );
}
