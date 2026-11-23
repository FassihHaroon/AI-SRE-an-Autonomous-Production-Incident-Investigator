import { Activity, AlertTriangle, GitCommitHorizontal, Search, ShieldCheck } from "lucide-react";
import { Link } from "react-router";

import { TimeSeriesChart } from "@/components/charts/TimeSeriesChart";
import { PageHeader } from "@/components/layout/PageHeader";
import {
  Card,
  CardBody,
  CardHeader,
  ErrorState,
  LoadingBlock,
  QueryState,
  Stat,
} from "@/components/ui";
import { useInvestigations } from "@/features/investigations/api";
import { AskBox, InvestigationTable } from "@/features/investigations/components";
import { useOverview, useSeries } from "@/features/platform/api";
import { DependencyList, DeploymentList, ServiceHealthTable } from "@/features/platform/components";
import { SystemStatusPanel } from "@/features/system/SystemStatusPanel";

const GATEWAY_SERVICES = ["api-gateway", "orders-service"];

function TrafficChart({ signal, title, unit }: { signal: string; title: string; unit: string }) {
  const series = useSeries({
    services: GATEWAY_SERVICES,
    signal,
    sinceMinutes: 60,
    compareOffsetMinutes: 0,
  });
  return (
    <Card>
      <CardHeader title={title} description="Last 60 min · Prometheus" />
      <CardBody>
        <QueryState query={series} loading={<LoadingBlock rows={5} />}>
          {(data) => (
            <TimeSeriesChart
              unit={unit}
              height={180}
              series={data.series.map((s) => ({
                key: s.service,
                label: s.service,
                points: s.points,
              }))}
            />
          )}
        </QueryState>
      </CardBody>
    </Card>
  );
}

export function OverviewPage() {
  const overview = useOverview();
  const investigations = useInvestigations();
  const data = overview.data;

  const services = data?.services ?? [];
  const healthy = services.filter((s) => s.up && s.health === "healthy").length;
  const alerts = services.reduce((n, s) => n + s.firing_alerts.length, 0);
  // Relative to when the data was fetched, which keeps render pure.
  const dayAgo = overview.dataUpdatedAt - 24 * 3600 * 1000;
  const deploys24h =
    data?.deployments.filter((d) => Date.parse(d.started_at) >= dayAgo).length ?? 0;
  const allInvestigations = investigations.data?.investigations ?? [];
  const active = allInvestigations.filter(
    (i) => i.status === "running" || i.status === "queued",
  ).length;
  const awaiting = allInvestigations.filter((i) => i.status === "awaiting_approval").length;

  return (
    <>
      <PageHeader
        title="Overview"
        description="Live state of the simulated production environment and the investigation agent."
      />

      {overview.error && !data ? (
        <ErrorState
          className="mb-4"
          error={overview.error}
          onRetry={() => void overview.refetch()}
          retrying={overview.isFetching}
        />
      ) : null}
      {data?.unavailable.length ? (
        <div
          role="alert"
          className="mb-4 rounded-md border border-warn/30 bg-warn-soft/50 px-3 py-2 text-xs text-warn"
        >
          Partial data: {data.unavailable.join(", ")} could not be read.
        </div>
      ) : null}

      <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-5">
        <Stat
          label="Services healthy"
          value={data ? `${healthy}/${services.length}` : "—"}
          tone={!data ? undefined : healthy === services.length ? "ok" : "danger"}
          icon={<Activity className="size-4" aria-hidden="true" />}
        />
        <Stat
          label="Firing alerts"
          value={data ? alerts : "—"}
          tone={alerts ? "danger" : undefined}
          icon={<AlertTriangle className="size-4" aria-hidden="true" />}
        />
        <Stat
          label="Deployments (24 h)"
          value={data ? deploys24h : "—"}
          icon={<GitCommitHorizontal className="size-4" aria-hidden="true" />}
        />
        <Stat
          label="Active investigations"
          value={investigations.data ? active : "—"}
          tone={active ? "info" : undefined}
          icon={<Search className="size-4" aria-hidden="true" />}
        />
        <Stat
          label="Awaiting approval"
          value={investigations.data ? awaiting : "—"}
          tone={awaiting ? "warn" : undefined}
          icon={<ShieldCheck className="size-4" aria-hidden="true" />}
        />
      </div>

      <Card className="mb-4">
        <CardHeader
          title="Ask the AI SRE"
          description="Describe a symptom; the agent investigates with read-only tools and cites its evidence."
        />
        <CardBody>
          <AskBox />
        </CardBody>
      </Card>

      <div className="mb-4 grid gap-4 xl:grid-cols-[2fr_1fr]">
        <Card>
          <CardHeader
            title="Service health"
            actions={
              <Link to="/services" className="text-xs text-brand-strong hover:underline">
                Details
              </Link>
            }
          />
          <CardBody>
            {data ? (
              <ServiceHealthTable services={services} signals={data.golden_signals} />
            ) : (
              <LoadingBlock rows={6} />
            )}
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Dependencies" />
          <CardBody>
            {data ? <DependencyList dependencies={data.dependencies} /> : <LoadingBlock rows={5} />}
          </CardBody>
        </Card>
      </div>

      <div className="mb-4 grid gap-4 xl:grid-cols-2">
        <TrafficChart signal="error_rate" title="Error ratio" unit="ratio" />
        <TrafficChart signal="latency_p95" title="Latency p95" unit="s" />
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader
            title="Recent investigations"
            actions={
              <Link to="/investigations" className="text-xs text-brand-strong hover:underline">
                All
              </Link>
            }
          />
          <CardBody>
            <QueryState
              query={investigations}
              isEmpty={(d) => !d.investigations.length}
              empty={
                <p className="py-6 text-center text-xs text-muted">
                  No investigations yet. Ask a question above.
                </p>
              }
            >
              {(d) => (
                <InvestigationTable items={d.investigations.slice(0, 6)} showRootCause={false} />
              )}
            </QueryState>
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Recent deployments" />
          <CardBody>
            {data ? (
              <DeploymentList deployments={data.deployments} limit={6} />
            ) : (
              <LoadingBlock rows={5} />
            )}
          </CardBody>
        </Card>
      </div>

      <div className="mt-4">
        <SystemStatusPanel />
      </div>
    </>
  );
}
