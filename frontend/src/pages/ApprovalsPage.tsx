import { Link } from "react-router";

import { PageHeader } from "@/components/layout/PageHeader";
import {
  Card,
  CardBody,
  CardHeader,
  EmptyState,
  ErrorState,
  LoadingBlock,
  QueryState,
} from "@/components/ui";
import { useInvestigation, useInvestigations } from "@/features/investigations/api";
import { ApprovalPanel } from "@/features/investigations/approval";
import { InvestigationTable, SeverityBadge } from "@/features/investigations/components";
import { relativeTime } from "@/lib/format";

function PendingApproval({ id }: { id: string }) {
  const detail = useInvestigation(id);
  if (detail.isPending) return <LoadingBlock rows={4} />;
  if (!detail.data) {
    return <ErrorState error={detail.error} onRetry={() => void detail.refetch()} />;
  }
  const inv = detail.data.investigation;
  const proposals = detail.data.state?.proposals ?? inv.approval?.proposals ?? [];
  return (
    <Card className="border-warn/40">
      <CardHeader
        title={
          <Link to={`/investigations/${inv.id}`} className="hover:text-brand-strong">
            {inv.question}
          </Link>
        }
        description={`${inv.id} · requested ${relativeTime(inv.updated_at)}`}
        actions={<SeverityBadge severity={inv.severity} />}
      />
      <CardBody>
        {inv.status === "awaiting_approval" ? (
          <ApprovalPanel
            investigationId={inv.id}
            proposals={proposals}
            rootCause={inv.root_cause}
          />
        ) : (
          <p className="text-sm text-muted">
            Decision recorded; the investigation is now {inv.status.replace("_", " ")}.
          </p>
        )}
      </CardBody>
    </Card>
  );
}

export function ApprovalsPage() {
  const investigations = useInvestigations();
  return (
    <>
      <PageHeader
        title="Remediation approvals"
        description="Actions the agent proposed. Nothing is executed until a human approves it here, with confirmation."
      />
      <QueryState query={investigations} loading={<LoadingBlock rows={6} />}>
        {(d) => {
          const pending = d.investigations.filter((i) => i.status === "awaiting_approval");
          const decided = d.investigations.filter(
            (i) => i.status !== "awaiting_approval" && i.root_cause !== null,
          );
          return (
            <div className="space-y-4">
              {pending.length ? (
                pending.map((i) => <PendingApproval key={i.id} id={i.id} />)
              ) : (
                <Card>
                  <CardBody>
                    <EmptyState
                      title="No pending approvals"
                      description="When an investigation proposes a remediation it waits here for a decision."
                    />
                  </CardBody>
                </Card>
              )}
              <Card>
                <CardHeader
                  title="Recently diagnosed"
                  description="Open an investigation to see its decisions and verification"
                />
                <CardBody>
                  {decided.length ? (
                    <InvestigationTable items={decided.slice(0, 10)} />
                  ) : (
                    <EmptyState title="No completed diagnoses yet" />
                  )}
                </CardBody>
              </Card>
            </div>
          );
        }}
      </QueryState>
    </>
  );
}
