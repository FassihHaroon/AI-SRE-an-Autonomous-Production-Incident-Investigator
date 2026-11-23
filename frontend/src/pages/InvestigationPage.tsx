import { useQueryClient } from "@tanstack/react-query";
import { Activity, CornerDownRight, Radio, RotateCw, Square } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type SyntheticEvent } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { PageHeader } from "@/components/layout/PageHeader";
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  EmptyState,
  ErrorState,
  LoadingBlock,
  SectionTitle,
  StatusDot,
  Tabs,
  Textarea,
} from "@/components/ui";
import {
  investigationKeys,
  useCancel,
  useFollowUp,
  useInvestigation,
  useInvestigations,
} from "@/features/investigations/api";
import { ApprovalPanel } from "@/features/investigations/approval";
import {
  EvidenceTable,
  HypothesesList,
  InvestigationStatusBadge,
  InvestigationTable,
  LiveTimeline,
  ProgressSteps,
  SeverityBadge,
  TimelineList,
} from "@/features/investigations/components";
import { ReportView, RootCauseCard } from "@/features/investigations/report";
import {
  useInvestigationEvents,
  type ConnectionState,
} from "@/features/investigations/useInvestigationEvents";
import { ApiError } from "@/lib/api";
import { relativeTime } from "@/lib/format";
import { isTerminal } from "@/lib/status";
import type { InvestigationEvent, InvestigationState } from "@/lib/types";

type TabKey = "workspace" | "report" | "tools" | "timeline";

/** Event types after which the persisted state has changed and should be re-read. */
const STATE_EVENTS = new Set([
  "status",
  "scope",
  "plan",
  "tool_call",
  "evidence_analyzed",
  "hypotheses",
  "decision",
  "root_cause",
  "proposals",
  "approval_required",
  "approval_decision",
  "verification",
  "report_ready",
  "error",
]);

function ConnectionIndicator({
  connection,
  terminal,
  onRetry,
}: {
  connection: ConnectionState;
  terminal: boolean;
  onRetry: () => void;
}) {
  if (connection === "open") {
    return (
      <Badge tone="ok">
        <Radio className="size-3" aria-hidden="true" /> Live
      </Badge>
    );
  }
  if (connection === "connecting") return <Badge tone="info">Connecting…</Badge>;
  if (connection === "reconnecting") {
    return (
      <Badge tone="warn">
        <StatusDot tone="warn" pulse /> Connection lost, reconnecting…
      </Badge>
    );
  }
  if (connection === "ended") {
    return <Badge>{terminal ? "Finished" : "Paused"}</Badge>;
  }
  return (
    <span className="inline-flex items-center gap-2">
      <Badge tone="danger">Live updates unavailable (polling)</Badge>
      <Button size="xs" variant="secondary" onClick={onRetry}>
        <RotateCw className="size-3" aria-hidden="true" />
        Reconnect
      </Button>
    </span>
  );
}

function FollowUps({ id, terminal }: { id: string; terminal: boolean }) {
  const [question, setQuestion] = useState("");
  const followUp = useFollowUp(id);
  const list = useInvestigations();
  const navigate = useNavigate();
  const children = list.data?.investigations.filter((i) => i.parent_id === id) ?? [];

  const submit = (e: SyntheticEvent) => {
    e.preventDefault();
    if (question.trim().length < 3) return;
    followUp.mutate(question.trim(), {
      onSuccess: (res) => {
        setQuestion("");
        void navigate(`/investigations/${res.id}`);
      },
    });
  };

  return (
    <Card>
      <CardHeader
        title="Follow-up questions"
        description="A follow-up starts a linked investigation that carries this one's findings as context."
      />
      <CardBody className="space-y-3">
        {children.length ? <InvestigationTable items={children} /> : null}
        <form
          onSubmit={submit}
          className="flex flex-col gap-2 sm:flex-row"
          aria-label="Ask a follow-up"
        >
          <label htmlFor="follow-up" className="sr-only">
            Follow-up question
          </label>
          <Textarea
            id="follow-up"
            rows={1}
            value={question}
            maxLength={1000}
            placeholder={
              terminal
                ? "e.g. “Is the database pool also affected?”"
                : "Available when this investigation finishes"
            }
            disabled={!terminal}
            onChange={(e) => {
              setQuestion(e.target.value);
            }}
          />
          <Button
            type="submit"
            size="md"
            disabled={!terminal || question.trim().length < 3 || followUp.isPending}
          >
            <CornerDownRight className="size-4" aria-hidden="true" />
            Ask
          </Button>
        </form>
        {followUp.error ? (
          <ErrorState error={followUp.error} title="Could not start the follow-up" />
        ) : null}
      </CardBody>
    </Card>
  );
}

function PlanCard({ state }: { state: Partial<InvestigationState> }) {
  const plan = state.plan;
  return (
    <Card>
      <CardHeader
        title="Investigation plan"
        description={plan ? `Round ${plan.round}` : undefined}
      />
      <CardBody>
        {plan ? (
          <div className="space-y-2">
            <p className="text-sm text-ink/85">{plan.rationale}</p>
            {plan.calls.length ? (
              <ul className="space-y-1">
                {plan.calls.map((c, i) => (
                  <li key={`${c.server}.${c.tool}-${i}`} className="flex items-start gap-2 text-xs">
                    <span className="rounded bg-neutral-soft px-1 font-mono text-[10px] text-muted">
                      P{c.priority}
                    </span>
                    <span className="font-mono text-ink">
                      {c.server}.{c.tool}
                    </span>
                    <span className="text-muted">{c.purpose}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-xs text-muted">No further calls planned in this round.</p>
            )}
          </div>
        ) : (
          <EmptyState
            title="Planning…"
            description="The plan appears once the question is scoped."
          />
        )}
      </CardBody>
    </Card>
  );
}

function ScopeCard({ state }: { state: Partial<InvestigationState> }) {
  const scope = state.scope;
  if (!scope) return null;
  return (
    <Card>
      <CardHeader title="Scope" />
      <CardBody className="space-y-2 text-xs">
        <div className="flex flex-wrap gap-1">
          {scope.services.map((s) => (
            <Badge key={s}>{s}</Badge>
          ))}
          {scope.symptoms.map((s) => (
            <Badge key={s} tone="info">
              {s}
            </Badge>
          ))}
        </div>
        <p className="text-muted">Window: last {scope.window_minutes} min</p>
        {scope.assumptions.length ? (
          <ul className="list-disc space-y-0.5 pl-4 text-muted">
            {scope.assumptions.map((a) => (
              <li key={a}>{a}</li>
            ))}
          </ul>
        ) : null}
      </CardBody>
    </Card>
  );
}

export function InvestigationPage() {
  const { id = "" } = useParams();
  const client = useQueryClient();
  // null = not chosen by the user yet: show the report once it exists, else the workspace.
  const [chosenTab, setTab] = useState<TabKey | null>(null);
  const refresh = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Re-read the persisted state shortly after state-changing events, coalescing bursts.
  const onEvent = useCallback(
    (event: InvestigationEvent) => {
      if (!STATE_EVENTS.has(event.type) || refresh.current) return;
      refresh.current = setTimeout(() => {
        refresh.current = null;
        void client.invalidateQueries({ queryKey: investigationKeys.detail(id) });
        if (event.type === "status" || event.type === "report_ready") {
          void client.invalidateQueries({ queryKey: investigationKeys.list() });
        }
      }, 400);
    },
    [client, id],
  );
  useEffect(
    () => () => {
      if (refresh.current) clearTimeout(refresh.current);
    },
    [],
  );

  const stream = useInvestigationEvents(id, onEvent);
  const detail = useInvestigation(id, stream.connection === "failed");
  const cancel = useCancel(id);

  const status = detail.data?.investigation.status ?? "running";
  const terminal = isTerminal(status);
  const { connection, reconnect } = stream;

  // The stream closes while the run waits for approval; re-open it once the run resumes.
  useEffect(() => {
    if (connection === "ended" && status === "running") reconnect();
  }, [connection, status, reconnect]);

  const hasReport = Boolean(detail.data?.investigation.report);
  const tab: TabKey = chosenTab ?? (hasReport && status === "completed" ? "report" : "workspace");

  if (detail.isPending) {
    return (
      <>
        <PageHeader
          title="Loading investigation…"
          breadcrumbs={[{ label: "Investigations", to: "/investigations" }, { label: id }]}
        />
        <LoadingBlock rows={6} />
      </>
    );
  }
  if (!detail.data) {
    const notFound = detail.error instanceof ApiError && detail.error.status === 404;
    return (
      <>
        <PageHeader
          title="Investigation"
          breadcrumbs={[{ label: "Investigations", to: "/investigations" }, { label: id }]}
        />
        {notFound ? (
          <EmptyState
            title="Investigation not found"
            description={`No investigation with id ${id}.`}
            action={
              <Link to="/investigations" className="text-sm text-brand-strong hover:underline">
                Back to investigations
              </Link>
            }
          />
        ) : (
          <ErrorState
            error={detail.error}
            onRetry={() => void detail.refetch()}
            retrying={detail.isFetching}
          />
        )}
      </>
    );
  }

  const inv = detail.data.investigation;
  const state: Partial<InvestigationState> = detail.data.state ?? {};
  const evidence = state.evidence ?? [];
  const proposals = state.proposals ?? inv.approval?.proposals ?? [];
  const report = inv.report ?? state.report ?? null;
  const rootCause = inv.root_cause ?? state.root_cause ?? null;

  return (
    <>
      <PageHeader
        breadcrumbs={[{ label: "Investigations", to: "/investigations" }, { label: inv.id }]}
        title={inv.question}
        description={
          <span className="flex flex-wrap items-center gap-2">
            <span>
              Started {relativeTime(inv.created_at)} · reasoner {inv.reasoner}
            </span>
            {inv.parent_id ? (
              <span>
                · follow-up of{" "}
                <Link
                  to={`/investigations/${inv.parent_id}`}
                  className="font-mono text-brand-strong hover:underline"
                >
                  {inv.parent_id}
                </Link>
              </span>
            ) : null}
          </span>
        }
        actions={
          <>
            <SeverityBadge severity={inv.severity} />
            <InvestigationStatusBadge status={status} />
            <ConnectionIndicator connection={connection} terminal={terminal} onRetry={reconnect} />
            <Link
              to={`/investigations/${inv.id}/trace`}
              className="inline-flex h-8 items-center gap-1.5 rounded-md border border-line-strong bg-raised px-3 text-sm text-ink hover:bg-neutral-soft"
            >
              <Activity className="size-3.5" aria-hidden="true" />
              Trace
            </Link>
            {!terminal ? (
              <Button
                size="sm"
                variant="secondary"
                onClick={() => {
                  cancel.mutate();
                }}
                disabled={cancel.isPending}
              >
                <Square className="size-3.5" aria-hidden="true" />
                Cancel
              </Button>
            ) : null}
          </>
        }
      />

      {cancel.error ? (
        <ErrorState className="mb-4" error={cancel.error} title="Cancel failed" />
      ) : null}
      {detail.error ? (
        <ErrorState
          className="mb-4"
          error={detail.error}
          title="Showing last known state. Refresh failed"
          onRetry={() => void detail.refetch()}
        />
      ) : null}
      {inv.error ? (
        <ErrorState
          className="mb-4"
          error={new Error(inv.error)}
          title="The investigation failed"
        />
      ) : null}

      {status === "awaiting_approval" ? (
        <Card className="mb-4 border-warn/40">
          <CardHeader
            title="Remediation awaiting approval"
            description="The agent will not change anything until a human approves."
          />
          <CardBody>
            <ApprovalPanel investigationId={inv.id} proposals={proposals} rootCause={rootCause} />
          </CardBody>
        </Card>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-[15rem_1fr]">
        <div className="space-y-4">
          <Card>
            <CardHeader title="Progress" />
            <CardBody>
              <ProgressSteps events={stream.events} status={status} round={state.round} />
            </CardBody>
          </Card>
          <ScopeCard state={state} />
        </div>

        <div className="min-w-0 space-y-4">
          <Tabs<TabKey>
            value={tab}
            onChange={setTab}
            items={[
              { key: "workspace", label: "Workspace" },
              { key: "report", label: "Report" },
              { key: "tools", label: "Tool calls", count: evidence.length },
              { key: "timeline", label: "Incident timeline", count: state.timeline?.length ?? 0 },
            ]}
          />

          {tab === "workspace" ? (
            <div className="space-y-4">
              <Card>
                <CardHeader title="Leading hypothesis" />
                <CardBody>
                  {rootCause ? (
                    <RootCauseCard rootCause={rootCause} investigationId={inv.id} />
                  ) : (state.hypotheses ?? []).length ? (
                    <p className="text-sm text-muted">Evaluating. See hypotheses below.</p>
                  ) : (
                    <EmptyState title="No hypothesis yet" description="Gathering evidence first." />
                  )}
                </CardBody>
              </Card>
              <div className="grid gap-4 xl:grid-cols-2">
                <PlanCard state={state} />
                <Card>
                  <CardHeader
                    title="Live activity"
                    description={`${stream.events.length} events`}
                  />
                  <CardBody className="max-h-96 overflow-y-auto">
                    <LiveTimeline events={stream.events} investigationId={inv.id} />
                  </CardBody>
                </Card>
              </div>
              <Card>
                <CardHeader
                  title="Hypotheses"
                  description="Scored by a deterministic confidence rubric"
                />
                <CardBody>
                  <HypothesesList hypotheses={state.hypotheses ?? []} />
                </CardBody>
              </Card>
              <Card>
                <CardHeader title="Evidence (tool history)" />
                <CardBody>
                  <EvidenceTable
                    evidence={evidence}
                    investigationId={inv.id}
                    highlight={rootCause?.evidence}
                  />
                </CardBody>
              </Card>
            </div>
          ) : null}

          {tab === "report" ? (
            report ? (
              <ReportView report={report} />
            ) : (
              <Card>
                <CardBody>
                  <EmptyState
                    title={terminal ? "No report was produced" : "Report not ready yet"}
                    description={
                      status === "awaiting_approval"
                        ? "The report is written after the remediation decision."
                        : "The report is written when the investigation finishes."
                    }
                  />
                </CardBody>
              </Card>
            )
          ) : null}

          {tab === "tools" ? (
            <Card>
              <CardHeader
                title="Tool calls"
                description="Every MCP call, its arguments and result"
              />
              <CardBody>
                <EvidenceTable evidence={evidence} investigationId={inv.id} />
              </CardBody>
            </Card>
          ) : null}

          {tab === "timeline" ? (
            <Card>
              <CardHeader
                title="Incident timeline"
                description="Reconstructed from logs, metrics and deployments"
              />
              <CardBody>
                <TimelineList entries={state.timeline ?? []} />
              </CardBody>
            </Card>
          ) : null}

          {status !== "awaiting_approval" &&
          proposals.some((p) => p.status !== "awaiting_approval") &&
          tab === "workspace" ? (
            <Card>
              <CardHeader title="Remediation decisions" />
              <CardBody>
                <SectionTitle>Proposals</SectionTitle>
                <ApprovalPanel investigationId={inv.id} proposals={proposals} rootCause={null} />
              </CardBody>
            </Card>
          ) : null}

          <FollowUps id={inv.id} terminal={terminal} />
        </div>
      </div>
    </>
  );
}
