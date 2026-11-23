import {
  Activity,
  BookOpen,
  Gauge,
  LayoutDashboard,
  LineChart,
  Search,
  Settings,
  ShieldCheck,
  type LucideIcon,
} from "lucide-react";
import { NavLink, Outlet } from "react-router";

import { StatusDot } from "@/components/ui";
import { useInvestigations } from "@/features/investigations/api";
import { useSystemStatus } from "@/features/system/useSystemStatus";
import { cn } from "@/lib/cn";

interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  end?: boolean;
}

const NAV_ITEMS: NavItem[] = [
  { to: "/", label: "Overview", icon: LayoutDashboard, end: true },
  { to: "/investigations", label: "Investigations", icon: Search },
  { to: "/approvals", label: "Approvals", icon: ShieldCheck },
  { to: "/services", label: "Service health", icon: Activity },
  { to: "/metrics", label: "Metrics explorer", icon: LineChart },
  { to: "/incidents", label: "Incident history", icon: BookOpen },
  { to: "/settings", label: "Settings", icon: Settings },
];

function ApiIndicator() {
  const { data, error } = useSystemStatus();
  const tone = error
    ? "danger"
    : !data
      ? "neutral"
      : data.readiness.status === "ready"
        ? "ok"
        : "warn";
  const text = error
    ? "API unreachable"
    : !data
      ? "Connecting…"
      : `API ${data.readiness.status.replace("_", " ")}`;
  return (
    <div className="flex items-center gap-2 text-xs text-muted" role="status">
      <StatusDot tone={tone} pulse={!data && !error} />
      <span>{text}</span>
      {data ? <span className="ml-auto text-faint">v{data.version}</span> : null}
    </div>
  );
}

function PendingApprovals() {
  const { data } = useInvestigations();
  const count = data?.investigations.filter((i) => i.status === "awaiting_approval").length ?? 0;
  if (!count) return null;
  return (
    <span
      aria-label={`${count} pending`}
      className="ml-auto rounded bg-warn-soft px-1.5 text-[11px] font-semibold text-warn tabular"
    >
      {count}
    </span>
  );
}

export function AppShell() {
  return (
    <div className="flex min-h-screen">
      <aside className="sticky top-0 hidden h-screen w-56 shrink-0 flex-col border-r border-line bg-panel md:flex">
        <div className="flex h-14 items-center gap-2 border-b border-line px-4">
          <Gauge className="size-5 text-brand" aria-hidden="true" />
          <span className="font-semibold tracking-tight text-ink">AI SRE</span>
          <span className="ml-auto rounded border border-warn/40 px-1.5 py-px text-[10px] font-semibold tracking-wide text-warn">
            SIMULATED
          </span>
        </div>
        <nav aria-label="Main" className="flex-1 space-y-0.5 overflow-y-auto p-2">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end ?? false}
              className={({ isActive }) =>
                cn(
                  "flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-sm",
                  isActive
                    ? "bg-raised font-medium text-ink"
                    : "text-muted hover:bg-raised/60 hover:text-ink",
                )
              }
            >
              <item.icon className="size-4" aria-hidden="true" />
              {item.label}
              {item.to === "/approvals" ? <PendingApprovals /> : null}
            </NavLink>
          ))}
        </nav>
        <div className="border-t border-line px-4 py-3">
          <ApiIndicator />
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        {/* Compact navigation for narrow screens. */}
        <nav
          aria-label="Main mobile"
          className="flex gap-1 overflow-x-auto border-b border-line bg-panel px-2 py-2 md:hidden"
        >
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end ?? false}
              className={({ isActive }) =>
                cn(
                  "rounded-md px-2.5 py-1 text-xs whitespace-nowrap",
                  isActive ? "bg-raised text-ink" : "text-muted",
                )
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
        <main className="mx-auto w-full max-w-[1400px] flex-1 px-4 py-6 sm:px-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
