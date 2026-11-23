import { PageHeader } from "@/components/layout/PageHeader";
import { Card, CardBody, CardHeader } from "@/components/ui";
import { SystemStatusPanel } from "@/features/system/SystemStatusPanel";

export function OverviewPage() {
  return (
    <>
      <PageHeader
        title="Overview"
        description="Autonomous production incident investigator. Investigations arrive in a later phase."
      />
      <div className="grid gap-6 lg:grid-cols-2">
        <SystemStatusPanel />
        <Card>
          <CardHeader title="Getting started" />
          <CardBody className="space-y-2 text-sm text-muted">
            <p>
              The platform skeleton is running. Next phases add the production simulator, the
              investigation agent, evaluations, and the investigation dashboard.
            </p>
            <p>
              See <code className="font-mono text-ink">docs/implementation-roadmap.md</code> for the
              plan.
            </p>
          </CardBody>
        </Card>
      </div>
    </>
  );
}
