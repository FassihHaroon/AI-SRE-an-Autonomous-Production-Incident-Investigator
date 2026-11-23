import { BookOpen, FileText, Link2 } from "lucide-react";
import { Link } from "react-router";

import {
  Badge,
  Card,
  CardBody,
  CardHeader,
  ConfidenceMeter,
  EmptyState,
  SectionTitle,
} from "@/components/ui";
import { titleCase } from "@/lib/format";
import { riskTone } from "@/lib/status";
import type { Proposal, Reference, Report, RootCause, Verification } from "@/lib/types";

import { BandBadge, SeverityBadge, TimelineList } from "./components";

function EvidenceLinks({ ids, investigationId }: { ids: string[]; investigationId: string }) {
  if (!ids.length) return null;
  return (
    <div className="flex flex-wrap items-center gap-1 text-xs">
      <span className="text-muted">Evidence:</span>
      {ids.map((id) => (
        <Link
          key={id}
          to={`/investigations/${investigationId}/tools/${id}`}
          className="rounded bg-neutral-soft px-1.5 py-0.5 font-mono text-[11px] text-brand-strong hover:underline"
        >
          {id}
        </Link>
      ))}
    </div>
  );
}

export function RootCauseCard({
  rootCause,
  investigationId,
}: {
  rootCause: RootCause | null;
  investigationId: string;
}) {
  if (!rootCause) {
    return (
      <EmptyState
        title="No root cause established"
        description="The evidence did not support any hypothesis above the reporting threshold."
      />
    );
  }
  return (
    <div className="space-y-2.5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-base font-semibold text-ink">{rootCause.title}</div>
          <div className="mt-1 flex flex-wrap gap-1">
            <Badge>{titleCase(rootCause.category)}</Badge>
            {rootCause.component ? <Badge>{rootCause.component}</Badge> : null}
            {rootCause.trigger_ref ? (
              <Badge tone="info">trigger {rootCause.trigger_ref}</Badge>
            ) : null}
            <BandBadge band={rootCause.band} />
          </div>
        </div>
        <div className="w-56">
          <ConfidenceMeter value={rootCause.confidence} band={rootCause.band} />
        </div>
      </div>
      {rootCause.capped ? (
        <p className="rounded border border-warn/30 bg-warn-soft/50 px-2 py-1 text-xs text-warn">
          Confidence capped: {rootCause.capped}
        </p>
      ) : null}
      <p className="text-sm leading-relaxed text-ink/85">{rootCause.explanation}</p>
      <EvidenceLinks ids={rootCause.evidence} investigationId={investigationId} />
    </div>
  );
}

export function Alternatives({
  items,
  investigationId,
}: {
  items: RootCause[];
  investigationId: string;
}) {
  if (!items.length)
    return <p className="text-xs text-muted">No alternative explanations remained.</p>;
  return (
    <ul className="space-y-2">
      {items.map((a) => (
        <li key={a.hypothesis_id} className="rounded-md border border-line px-3 py-2">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0 text-sm text-ink">{a.title}</div>
            <div className="w-36 shrink-0">
              <ConfidenceMeter value={a.confidence} band={a.band} compact />
            </div>
          </div>
          <p className="mt-1 text-xs text-muted">Why it ranks lower: {a.explanation}</p>
          <div className="mt-1">
            <EvidenceLinks ids={a.evidence} investigationId={investigationId} />
          </div>
        </li>
      ))}
    </ul>
  );
}

export function Citations({ references }: { references: Reference[] }) {
  if (!references.length) {
    return <p className="text-xs text-muted">No knowledge-base documents were cited.</p>;
  }
  return (
    <ol className="space-y-1.5">
      {references.map((r, i) => (
        <li key={`${r.document_id}-${i}`} className="flex items-start gap-2 text-sm">
          <span className="mt-0.5 font-mono text-[11px] text-faint">[{i + 1}]</span>
          {r.kind === "similar_incident" ? (
            <BookOpen className="mt-0.5 size-3.5 shrink-0 text-info" aria-hidden="true" />
          ) : (
            <FileText className="mt-0.5 size-3.5 shrink-0 text-brand-strong" aria-hidden="true" />
          )}
          <div className="min-w-0">
            {r.kind === "similar_incident" ? (
              <Link to={`/incidents/${r.document_id}`} className="text-ink hover:text-brand-strong">
                {r.title}
              </Link>
            ) : (
              <span className="text-ink">{r.title}</span>
            )}
            <div className="text-xs text-muted">
              {titleCase(r.kind)} · <span className="font-mono">{r.source}</span>
              {r.section ? ` · ${r.section.split(" > ").slice(1).join(" › ") || r.section}` : ""}
              {r.evidence_id ? ` · via ${r.evidence_id}` : ""}
              {r.superseded_by ? (
                <Badge tone="warn" className="ml-1">
                  superseded by {r.superseded_by}
                </Badge>
              ) : null}
            </div>
          </div>
        </li>
      ))}
    </ol>
  );
}

export function ProposalStatusBadge({ status }: { status: string }) {
  const tone =
    status === "executed"
      ? "ok"
      : status === "rejected" || status === "skipped"
        ? "neutral"
        : status === "failed"
          ? "danger"
          : status === "awaiting_approval"
            ? "warn"
            : "info";
  return <Badge tone={tone}>{titleCase(status)}</Badge>;
}

export function ProposalSummary({ proposal }: { proposal: Proposal }) {
  const reason = typeof proposal.arguments.reason === "string" ? proposal.arguments.reason : null;
  return (
    <div className="space-y-1">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="font-mono text-sm text-ink">{proposal.action}</span>
        <Badge tone={riskTone(proposal.risk)}>{proposal.risk} risk</Badge>
        <ProposalStatusBadge status={proposal.status} />
        {proposal.reversible ? (
          <Badge>reversible</Badge>
        ) : (
          <Badge tone="danger">irreversible</Badge>
        )}
        {!proposal.executable ? <Badge tone="neutral">manual only</Badge> : null}
      </div>
      <p className="text-sm text-ink/85">{proposal.description}</p>
      {reason ? <p className="text-xs text-muted">Reason: {reason}</p> : null}
      {proposal.error ? <p className="text-xs text-danger">Error: {proposal.error}</p> : null}
    </div>
  );
}

export function VerificationCard({ verification }: { verification: Verification }) {
  const tone =
    verification.verdict === "resolved"
      ? "ok"
      : verification.verdict === "improved"
        ? "info"
        : verification.verdict === "worse"
          ? "danger"
          : "warn";
  const keys = [
    ...new Set([...Object.keys(verification.before), ...Object.keys(verification.after)]),
  ];
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <Badge tone={tone}>{titleCase(verification.verdict)}</Badge>
        <span className="text-sm text-ink/85">{verification.details}</span>
      </div>
      {keys.length ? (
        <table className="text-xs">
          <thead>
            <tr className="text-left text-muted">
              <th className="pr-4 font-medium">Signal ({verification.service})</th>
              <th className="pr-4 font-medium">Before</th>
              <th className="font-medium">After</th>
            </tr>
          </thead>
          <tbody className="font-mono tabular">
            {keys.map((k) => (
              <tr key={k}>
                <td className="pr-4 text-muted">{k}</td>
                <td className="pr-4">{verification.before[k]?.toFixed(4) ?? "—"}</td>
                <td>{verification.after[k]?.toFixed(4) ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </div>
  );
}

/** The final incident report: summary, root cause, alternatives, citations, actions. */
export function ReportView({ report }: { report: Report }) {
  const id = report.investigation_id;
  const actions = report.actions_taken.length ? report.actions_taken : report.recommendations;
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader
          title="Summary"
          description={`${report.rounds} round(s) · ${report.tool_calls} tool call(s) · stop reason: ${titleCase(report.stop_reason)}`}
          actions={<SeverityBadge severity={report.severity} />}
        />
        <CardBody className="space-y-2">
          <p className="text-sm text-ink">{report.summary}</p>
          {report.affected_services.length ? (
            <div className="flex flex-wrap items-center gap-1 text-xs text-muted">
              Affected:
              {report.affected_services.map((s) => (
                <Link key={s} to={`/services/${s}`}>
                  <Badge className="hover:text-ink">{s}</Badge>
                </Link>
              ))}
            </div>
          ) : null}
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Root cause" />
        <CardBody>
          <RootCauseCard rootCause={report.root_cause} investigationId={id} />
        </CardBody>
      </Card>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader title="Alternative explanations" />
          <CardBody>
            <Alternatives items={report.alternatives} investigationId={id} />
          </CardBody>
        </Card>
        <Card>
          <CardHeader
            title="Citations"
            description="Runbooks and past incidents retrieved from the knowledge base"
          />
          <CardBody>
            <Citations references={report.references} />
          </CardBody>
        </Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader title="Remediation" />
          <CardBody className="space-y-3">
            {actions.length ? (
              actions.map((p) => <ProposalSummary key={p.id} proposal={p} />)
            ) : (
              <p className="text-xs text-muted">No remediation was proposed.</p>
            )}
            {report.verification ? (
              <div className="border-t border-line pt-3">
                <SectionTitle>Verification</SectionTitle>
                <VerificationCard verification={report.verification} />
              </div>
            ) : null}
          </CardBody>
        </Card>
        <Card>
          <CardHeader title="Limitations" />
          <CardBody>
            <ul className="list-disc space-y-1 pl-4 text-xs text-muted">
              {report.limitations.map((l) => (
                <li key={l}>{l}</li>
              ))}
            </ul>
          </CardBody>
        </Card>
      </div>

      <Card>
        <CardHeader title="Incident timeline" />
        <CardBody>
          <TimelineList entries={report.timeline} />
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title="Evidence index"
          actions={<Link2 className="size-4 text-faint" aria-hidden="true" />}
        />
        <CardBody>
          <ul className="space-y-1 text-xs">
            {report.evidence.map((e) => (
              <li key={e.id}>
                <Link
                  to={`/investigations/${id}/tools/${e.id}`}
                  className="font-mono text-brand-strong hover:underline"
                >
                  {e.id}
                </Link>{" "}
                <span className="font-mono text-muted">{e.tool}</span>{" "}
                <span className="text-ink/85">{e.summary}</span>
              </li>
            ))}
          </ul>
        </CardBody>
      </Card>
    </div>
  );
}
