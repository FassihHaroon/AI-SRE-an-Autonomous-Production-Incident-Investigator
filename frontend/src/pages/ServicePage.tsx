import { Search } from "lucide-react";
import { useState } from "react";
import { useNavigate, useParams } from "react-router";

import { TimeSeriesChart } from "@/components/charts/TimeSeriesChart";
import { PageHeader } from "@/components/layout/PageHeader";
import {
  Button,
  Card,
  CardBody,
  CardHeader,
  EmptyState,
  ErrorState,
  KeyValue,
  LoadingBlock,
  QueryState,
  Stat,
  Tabs,
} from "@/components/ui";
import { useStartInvestigation } from "@/features/investigations/api";
import { useServiceDetail } from "@/features/platform/api";
import { DeploymentList, HealthBadge, LogTable } from "@/features/platform/components";
import { ApiError } from "@/lib/api";
import { formatValue, percent, titleCase } from "@/lib/format";
import type { ServiceDetail } from "@/lib/types";

const TILE_SIGNALS = [
  "request_rate",
  "error_rate",
  "latency_p95",
  "latency_p99",
  "cpu",
  "memory_bytes",
  "db_pool_utilization",
  "queue_depth",
];

function InvestigateButton({ service }: { service: string }) {
  const start = useStartInvestigation();
  const navigate = useNavigate();
  return (
    <>
      <Button
        size="sm"
        disabled={start.isPending}
        onClick={() => {
          start.mutate(
            `Is ${service} healthy? Investigate any recent errors or latency on ${service}.`,
            {
              onSuccess: (r) => void navigate(`/investigations/${r.id}`),
            },
          );
        }}
      >
        <Search className="size-4" aria-hidden="true" />
        Investigate {service}
      </Button>
      {start.error ? <ErrorState error={start.error} /> : null}
    </>
  );
}

function Body({ data }: { data: ServiceDetail }) {
  const [logTab, setLogTab] = useState<"errors" | "all">("errors");
  const signals = data.metrics?.signals ?? [];
  const tiles = TILE_SIGNALS.map((name) => signals.find((s) => s.signal === name)).filter(
    (s): s is NonNullable<typeof s> => s !== undefined,
  );
  const c = data.container;

  return (
    <div className="space-y-4">
      {data.unavailable?.length ? (
        <div
          role="alert"
          className="rounded-md border border-warn/30 bg-warn-soft/50 px-3 py-2 text-xs text-warn"
        >
          Partial data: {data.unavailable.join(", ")} could not be read.
        </div>
      ) : null}
      {tiles.length ? (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          {tiles.map((s) => (
            <Stat key={s.signal} label={titleCase(s.signal)} value={formatValue(s.value, s.unit)} />
          ))}
        </div>
      ) : null}

      {data.series.length ? (
        <div className="grid gap-4 xl:grid-cols-2">
          {data.series.map((s) => (
            <Card key={s.signal}>
              <CardHeader title={titleCase(s.signal)} description={`Last 60 min · ${s.unit}`} />
              <CardBody>
                <TimeSeriesChart
                  unit={s.unit}
                  height={170}
                  showLegend={false}
                  series={[{ key: s.signal, label: s.signal, points: s.points }]}
                />
              </CardBody>
            </Card>
          ))}
        </div>
      ) : null}

      <div className="grid gap-4 xl:grid-cols-[1fr_2fr]">
        <Card>
          <CardHeader title="Container" />
          <CardBody>
            {c ? (
              <KeyValue
                items={[
                  ["State", c.up ? "running" : "down"],
                  [
                    "Started",
                    c.started_at
                      ? new Date(c.started_at).toLocaleString([], { hour12: false })
                      : "—",
                  ],
                  ["Restarts (1 h)", c.restarts_last_hour],
                  [
                    "Memory",
                    `${formatValue(c.memory_bytes, "bytes")} / ${formatValue(c.memory_limit_bytes, "bytes")} (${percent(c.memory_utilization)})`,
                  ],
                  ["Open FDs", c.open_fds ?? "—"],
                  ["OOM risk", c.oom_risk ? <span className="text-danger">yes</span> : "no"],
                ]}
              />
            ) : (
              <EmptyState title="No container metrics" />
            )}
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Deployments" description="Last 7 days" />
          <CardBody>
            <DeploymentList deployments={data.deployments} limit={8} />
          </CardBody>
        </Card>
      </div>

      <Card>
        <CardHeader title="Logs" description="Loki · last 30 min" />
        <CardBody className="space-y-3">
          <Tabs
            value={logTab}
            onChange={setLogTab}
            items={[
              { key: "errors", label: "Errors", count: data.error_logs.length },
              { key: "all", label: "All", count: data.logs.length },
            ]}
          />
          <LogTable entries={logTab === "errors" ? data.error_logs : data.logs} />
        </CardBody>
      </Card>
    </div>
  );
}

export function ServicePage() {
  const { name = "" } = useParams();
  const detail = useServiceDetail(name);
  const notFound = detail.error instanceof ApiError && detail.error.status === 404;
  return (
    <>
      <PageHeader
        breadcrumbs={[{ label: "Service health", to: "/services" }, { label: name }]}
        title={
          <span className="flex items-center gap-3">
            {name}
            {detail.data?.status ? (
              <HealthBadge health={detail.data.status.up ? detail.data.status.health : "down"} />
            ) : null}
          </span>
        }
        description={
          detail.data?.status?.version ? `Version ${detail.data.status.version}` : undefined
        }
        actions={<InvestigateButton service={name} />}
      />
      {notFound ? (
        <EmptyState title="Unknown service" description={`${name} is not an observed service.`} />
      ) : (
        <QueryState query={detail} loading={<LoadingBlock rows={8} />}>
          {(data) => <Body data={data} />}
        </QueryState>
      )}
    </>
  );
}
