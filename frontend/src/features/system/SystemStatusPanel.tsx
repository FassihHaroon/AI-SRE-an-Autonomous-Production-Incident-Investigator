import { Badge, Button, Card, CardBody, CardHeader, Spinner } from "@/components/ui";

import type { ComponentHealth } from "./api";
import { useSystemStatus } from "./useSystemStatus";

const COMPONENT_LABELS: Record<string, string> = {
  postgres: "PostgreSQL",
  redis: "Redis",
};

export function SystemStatusPanel() {
  const { data, error, isPending, isFetching, refetch } = useSystemStatus();

  const refresh = (
    <Button variant="secondary" size="sm" onClick={() => void refetch()} disabled={isFetching}>
      Refresh
    </Button>
  );

  if (isPending) {
    return (
      <Card>
        <CardHeader title="System status" />
        <CardBody>
          <Spinner label="Checking services…" />
        </CardBody>
      </Card>
    );
  }

  if (error) {
    return (
      <Card>
        <CardHeader title="System status" actions={refresh} />
        <CardBody>
          <p role="alert" className="text-sm text-danger">
            The API is unreachable ({error.message}). Is the backend running?
          </p>
        </CardBody>
      </Card>
    );
  }

  const ready = data.readiness.status === "ready";
  return (
    <Card>
      <CardHeader
        title="System status"
        description={`API v${data.version} · ${data.environment}`}
        actions={
          <>
            <Badge tone={ready ? "ok" : "danger"}>{ready ? "Ready" : "Not ready"}</Badge>
            {refresh}
          </>
        }
      />
      <CardBody>
        <ul className="divide-y divide-line">
          {data.readiness.components.map((component) => (
            <ComponentRow key={component.name} component={component} />
          ))}
          <li className="flex items-center justify-between py-2.5">
            <span className="text-sm font-medium text-ink">
              LLM provider{" "}
              <span className="font-normal text-muted">
                ({data.llm.provider}
                {data.llm.model ? ` · ${data.llm.model}` : ""})
              </span>
            </span>
            <Badge tone={data.llm.configured ? "ok" : "warn"}>
              {data.llm.configured ? "Configured" : "API key missing"}
            </Badge>
          </li>
        </ul>
      </CardBody>
    </Card>
  );
}

function ComponentRow({ component }: { component: ComponentHealth }) {
  const ok = component.status === "ok";
  return (
    <li className="flex items-center justify-between py-2.5">
      <span className="text-sm font-medium text-ink">
        {COMPONENT_LABELS[component.name] ?? component.name}
      </span>
      <span className="flex items-center gap-3">
        <span className="font-mono text-xs text-muted">{component.latency_ms.toFixed(1)} ms</span>
        <Badge tone={ok ? "ok" : "danger"} title={component.detail ?? undefined}>
          {ok ? "Healthy" : "Unavailable"}
        </Badge>
      </span>
    </li>
  );
}
