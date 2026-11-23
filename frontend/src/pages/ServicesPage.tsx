import { PageHeader } from "@/components/layout/PageHeader";
import { Card, CardBody, CardHeader, LoadingBlock, QueryState } from "@/components/ui";
import { useOverview } from "@/features/platform/api";
import { DependencyList, ServiceHealthTable } from "@/features/platform/components";
import { relativeTime } from "@/lib/format";

export function ServicesPage() {
  const overview = useOverview();
  return (
    <>
      <PageHeader
        title="Service health"
        description={
          overview.data
            ? `Health from Prometheus and container state · refreshed ${relativeTime(new Date(overview.dataUpdatedAt).toISOString())}`
            : "Health from Prometheus and container state"
        }
      />
      <QueryState query={overview} loading={<LoadingBlock rows={8} />}>
        {(data) => (
          <div className="space-y-4">
            {data.unavailable.length ? (
              <div
                role="alert"
                className="rounded-md border border-warn/30 bg-warn-soft/50 px-3 py-2 text-xs text-warn"
              >
                Partial data: {data.unavailable.join(", ")} could not be read.
              </div>
            ) : null}
            <Card>
              <CardHeader
                title="Services"
                description="Select a service for signals, logs and deployments"
              />
              <CardBody>
                <ServiceHealthTable services={data.services} signals={data.golden_signals} />
              </CardBody>
            </Card>
            <Card>
              <CardHeader
                title="Dependencies"
                description="Health derived from client-side metrics of each dependency"
              />
              <CardBody>
                <DependencyList dependencies={data.dependencies} />
              </CardBody>
            </Card>
          </div>
        )}
      </QueryState>
    </>
  );
}
