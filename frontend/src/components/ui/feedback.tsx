import { AlertTriangle, Inbox, RotateCw, WifiOff } from "lucide-react";
import type { ReactNode } from "react";

import { ApiError, errorMessage } from "@/lib/api";
import { cn } from "@/lib/cn";

import { Button } from "./Button";

export function Skeleton({ className }: { className?: string }) {
  return (
    <div aria-hidden="true" className={cn("animate-pulse rounded bg-neutral-soft", className)} />
  );
}

/** Placeholder rows shown while a panel's first request is in flight. */
export function LoadingBlock({ rows = 3, label = "Loading" }: { rows?: number; label?: string }) {
  return (
    <div role="status" aria-label={label} className="space-y-2 py-1">
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className={cn("h-4", i % 2 ? "w-2/3" : "w-full")} />
      ))}
    </div>
  );
}

export interface EmptyStateProps {
  title: string;
  description?: ReactNode;
  action?: ReactNode;
  icon?: ReactNode;
  className?: string;
}

export function EmptyState({ title, description, action, icon, className }: EmptyStateProps) {
  return (
    <div className={cn("flex flex-col items-center gap-2 px-4 py-8 text-center", className)}>
      <span className="text-faint">{icon ?? <Inbox className="size-6" aria-hidden="true" />}</span>
      <p className="text-sm font-medium text-ink">{title}</p>
      {description ? <p className="max-w-md text-xs text-muted">{description}</p> : null}
      {action}
    </div>
  );
}

export interface ErrorStateProps {
  error: unknown;
  title?: string | undefined;
  onRetry?: (() => void) | undefined;
  retrying?: boolean | undefined;
  className?: string | undefined;
}

/** Error panel with the API's problem title/detail and an optional retry button. */
export function ErrorState({ error, title, onRetry, retrying, className }: ErrorStateProps) {
  const offline = !(error instanceof ApiError);
  const status = error instanceof ApiError ? error.status : null;
  const detail = error instanceof ApiError ? error.problem?.detail : undefined;
  const heading =
    title ??
    (offline
      ? "Cannot reach the API"
      : status === 502
        ? "A data source is unavailable"
        : status === 503
          ? "Service not ready"
          : "Request failed");
  return (
    <div
      role="alert"
      className={cn(
        "flex items-start gap-3 rounded-md border border-danger/30 bg-danger-soft/60 px-3 py-2.5",
        className,
      )}
    >
      {offline ? (
        <WifiOff className="mt-0.5 size-4 shrink-0 text-danger" aria-hidden="true" />
      ) : (
        <AlertTriangle className="mt-0.5 size-4 shrink-0 text-danger" aria-hidden="true" />
      )}
      <div className="min-w-0 flex-1 text-sm">
        <p className="font-medium text-ink">{heading}</p>
        <p className="mt-0.5 break-words text-xs text-muted">
          {status ? `HTTP ${status} · ` : ""}
          {detail ?? errorMessage(error)}
        </p>
      </div>
      {onRetry ? (
        <Button variant="secondary" size="xs" onClick={onRetry} disabled={retrying}>
          <RotateCw className={cn("size-3.5", retrying && "animate-spin")} aria-hidden="true" />
          Retry
        </Button>
      ) : null}
    </div>
  );
}

interface QueryLike<T> {
  data: T | undefined;
  error: unknown;
  isPending: boolean;
  isFetching?: boolean;
  refetch: () => unknown;
}

export interface QueryStateProps<T> {
  query: QueryLike<T>;
  children: (data: T) => ReactNode;
  loading?: ReactNode;
  isEmpty?: (data: T) => boolean;
  empty?: ReactNode;
  errorTitle?: string | undefined;
}

/** Standard loading → error (with retry) → empty → content switch for a react-query result. */
export function QueryState<T>({
  query,
  children,
  loading,
  isEmpty,
  empty,
  errorTitle,
}: QueryStateProps<T>) {
  if (query.isPending) return <>{loading ?? <LoadingBlock />}</>;
  if (query.data === undefined) {
    return (
      <ErrorState
        error={query.error}
        title={errorTitle}
        onRetry={() => void query.refetch()}
        retrying={query.isFetching}
      />
    );
  }
  if (isEmpty?.(query.data)) return <>{empty ?? <EmptyState title="Nothing here yet" />}</>;
  return (
    <>
      {query.error ? (
        <ErrorState
          className="mb-3"
          error={query.error}
          title="Showing last known data. Refresh failed"
          onRetry={() => void query.refetch()}
          retrying={query.isFetching}
        />
      ) : null}
      {children(query.data)}
    </>
  );
}
