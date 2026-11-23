import { NavLink, Outlet } from "react-router";

import { cn } from "@/lib/cn";

interface NavItem {
  to: string;
  label: string;
}

// Investigations, approvals, knowledge, evaluations and admin screens arrive in later phases.
const NAV_ITEMS: NavItem[] = [{ to: "/", label: "Overview" }];

export function AppShell() {
  return (
    <div className="min-h-screen">
      <header className="border-b border-line bg-panel">
        <div className="mx-auto flex h-14 max-w-6xl items-center gap-8 px-4 sm:px-6">
          <span className="flex items-center gap-2 font-semibold text-ink">
            <img src="/favicon.svg" alt="" className="size-6" />
            AI SRE
          </span>
          <nav aria-label="Main" className="flex gap-1">
            {NAV_ITEMS.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end
                className={({ isActive }) =>
                  cn(
                    "rounded-md px-3 py-1.5 text-sm font-medium",
                    isActive ? "bg-neutral-soft text-ink" : "text-muted hover:text-ink",
                  )
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
        <Outlet />
      </main>
    </div>
  );
}
