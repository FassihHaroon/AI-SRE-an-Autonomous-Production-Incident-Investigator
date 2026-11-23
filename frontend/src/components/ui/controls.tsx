import { X } from "lucide-react";
import {
  useEffect,
  useId,
  useRef,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react";

import { cn } from "@/lib/cn";

const fieldBase =
  "w-full rounded-md border border-line-strong bg-surface px-2.5 text-sm text-ink placeholder:text-faint " +
  "focus:border-brand focus:outline-none focus:ring-2 focus:ring-brand/30 disabled:opacity-60";

export function Input({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cn(fieldBase, "h-9", className)} {...props} />;
}

export function Select({ className, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select className={cn(fieldBase, "h-9 pr-7", className)} {...props} />;
}

export function Textarea({ className, ...props }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={cn(fieldBase, "py-2 leading-relaxed", className)} {...props} />;
}

export function Field({
  label,
  hint,
  error,
  children,
  htmlFor,
}: {
  label: string;
  hint?: ReactNode;
  error?: string | null;
  children: ReactNode;
  htmlFor?: string;
}) {
  return (
    <div className="space-y-1">
      <label htmlFor={htmlFor} className="block text-xs font-medium text-muted">
        {label}
      </label>
      {children}
      {error ? (
        <p className="text-xs text-danger">{error}</p>
      ) : hint ? (
        <p className="text-xs text-faint">{hint}</p>
      ) : null}
    </div>
  );
}

export interface TabItem<K extends string> {
  key: K;
  label: ReactNode;
  count?: number | undefined;
}

export function Tabs<K extends string>({
  items,
  value,
  onChange,
  className,
}: {
  items: TabItem<K>[];
  value: K;
  onChange: (key: K) => void;
  className?: string;
}) {
  return (
    <div role="tablist" className={cn("flex gap-1 border-b border-line", className)}>
      {items.map((item) => {
        const active = item.key === value;
        return (
          <button
            key={item.key}
            role="tab"
            type="button"
            aria-selected={active}
            onClick={() => {
              onChange(item.key);
            }}
            className={cn(
              "-mb-px flex items-center gap-1.5 border-b-2 px-3 py-2 text-sm font-medium transition-colors",
              active ? "border-brand text-ink" : "border-transparent text-muted hover:text-ink",
            )}
          >
            {item.label}
            {item.count !== undefined ? (
              <span className="rounded bg-neutral-soft px-1.5 text-[11px] text-muted tabular">
                {item.count}
              </span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}

export function Segmented<K extends string>({
  options,
  value,
  onChange,
  label,
}: {
  options: { key: K; label: string }[];
  value: K;
  onChange: (key: K) => void;
  label: string;
}) {
  return (
    <div
      role="radiogroup"
      aria-label={label}
      className="inline-flex rounded-md border border-line-strong bg-surface p-0.5"
    >
      {options.map((o) => (
        <button
          key={o.key}
          type="button"
          role="radio"
          aria-checked={o.key === value}
          onClick={() => {
            onChange(o.key);
          }}
          className={cn(
            "rounded px-2.5 py-1 text-xs font-medium",
            o.key === value ? "bg-raised text-ink" : "text-muted hover:text-ink",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/** Accessible modal dialog: focus moves inside, Escape and the backdrop close it. */
export function Modal({
  open,
  title,
  onClose,
  children,
  footer,
}: {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
}) {
  const titleId = useId();
  const panel = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    panel.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      previous?.focus();
    };
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/60" aria-hidden="true" onClick={onClose} />
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className="relative w-full max-w-lg animate-fade-in rounded-lg border border-line-strong bg-panel shadow-2xl focus:outline-none"
      >
        <div className="flex items-center justify-between border-b border-line px-4 py-3">
          <h2 id={titleId} className="text-sm font-semibold text-ink">
            {title}
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="rounded p-1 text-muted hover:bg-neutral-soft hover:text-ink"
          >
            <X className="size-4" aria-hidden="true" />
          </button>
        </div>
        <div className="px-4 py-4">{children}</div>
        {footer ? (
          <div className="flex justify-end gap-2 border-t border-line px-4 py-3">{footer}</div>
        ) : null}
      </div>
    </div>
  );
}
