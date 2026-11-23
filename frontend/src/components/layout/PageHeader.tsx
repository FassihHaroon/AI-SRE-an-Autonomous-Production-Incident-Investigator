import { ChevronRight } from "lucide-react";
import { Fragment, type ReactNode } from "react";
import { Link } from "react-router";

export interface PageHeaderProps {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  breadcrumbs?: { label: string; to?: string }[];
}

export function PageHeader({ title, description, actions, breadcrumbs }: PageHeaderProps) {
  return (
    <div className="mb-5">
      {breadcrumbs?.length ? (
        <nav aria-label="Breadcrumb" className="mb-1.5 flex items-center gap-1 text-xs text-muted">
          {breadcrumbs.map((b, i) => (
            <Fragment key={`${b.label}-${i}`}>
              {i > 0 ? <ChevronRight className="size-3 text-faint" aria-hidden="true" /> : null}
              {b.to ? (
                <Link to={b.to} className="hover:text-ink">
                  {b.label}
                </Link>
              ) : (
                <span className="text-faint">{b.label}</span>
              )}
            </Fragment>
          ))}
        </nav>
      ) : null}
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold tracking-tight text-ink">{title}</h1>
          {description ? <p className="mt-1 text-sm text-muted">{description}</p> : null}
        </div>
        {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
      </div>
    </div>
  );
}
