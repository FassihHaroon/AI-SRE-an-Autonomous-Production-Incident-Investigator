import { ShieldAlert } from "lucide-react";
import { useState } from "react";

import {
  Badge,
  Button,
  ConfidenceMeter,
  ErrorState,
  Field,
  Input,
  JsonView,
  Modal,
} from "@/components/ui";
import { titleCase } from "@/lib/format";
import { riskTone } from "@/lib/status";
import type { Proposal, RootCause } from "@/lib/types";

import { useDecision } from "./api";
import { ProposalStatusBadge } from "./report";

const APPROVER_KEY = "ai-sre.approver";

function storedApprover(): string {
  try {
    return localStorage.getItem(APPROVER_KEY) ?? "";
  } catch {
    return "";
  }
}

type Pending = { proposal: Proposal; decision: "approve" | "reject" } | null;

/**
 * Human approval gate for remediation proposals. A decision is only sent after an explicit
 * confirmation step, and the API additionally requires `confirm: true`.
 */
export function ApprovalPanel({
  investigationId,
  proposals,
  rootCause,
}: {
  investigationId: string;
  proposals: Proposal[];
  rootCause: RootCause | null;
}) {
  const [approver, setApprover] = useState(storedApprover);
  const [comment, setComment] = useState("");
  const [pending, setPending] = useState<Pending>(null);
  const [acknowledged, setAcknowledged] = useState(false);
  const decision = useDecision(investigationId);
  const approverValid = approver.trim().length >= 2;

  const open = (proposal: Proposal, d: "approve" | "reject") => {
    decision.reset();
    setAcknowledged(false);
    setPending({ proposal, decision: d });
  };
  const close = () => {
    setPending(null);
  };

  const submit = () => {
    if (!pending) return;
    try {
      localStorage.setItem(APPROVER_KEY, approver.trim());
    } catch {
      // Storage unavailable (private mode); the name just is not remembered.
    }
    decision.mutate(
      {
        proposal_id: pending.proposal.id,
        decision: pending.decision,
        approver: approver.trim(),
        comment: comment.trim() || undefined,
        confirm: true,
      },
      {
        onSuccess: () => {
          setPending(null);
          setComment("");
        },
      },
    );
  };

  const awaiting = proposals.filter((p) => p.status === "awaiting_approval");

  return (
    <div className="space-y-4">
      {rootCause ? (
        <div className="rounded-md border border-line bg-surface/60 px-3 py-2">
          <div className="text-xs text-muted">Diagnosis behind these proposals</div>
          <div className="mt-0.5 flex flex-wrap items-center justify-between gap-2">
            <span className="text-sm text-ink">{rootCause.title}</span>
            <div className="w-52">
              <ConfidenceMeter value={rootCause.confidence} band={rootCause.band} />
            </div>
          </div>
        </div>
      ) : null}

      {proposals.map((p) => {
        const reason = typeof p.arguments.reason === "string" ? p.arguments.reason : null;
        const isAwaiting = p.status === "awaiting_approval";
        return (
          <article
            key={p.id}
            aria-label={`Proposal ${p.id}`}
            className="rounded-lg border border-line bg-raised/40 px-4 py-3"
          >
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-xs text-faint">#{p.order}</span>
              <h3 className="font-mono text-sm font-semibold text-ink">{p.action}</h3>
              <Badge tone={riskTone(p.risk)}>{titleCase(p.risk)} risk</Badge>
              <ProposalStatusBadge status={p.status} />
              {p.reversible ? <Badge>reversible</Badge> : <Badge tone="danger">irreversible</Badge>}
            </div>
            <dl className="mt-2 grid gap-x-4 gap-y-1.5 text-sm sm:grid-cols-[9rem_1fr]">
              <dt className="text-muted">Action</dt>
              <dd className="text-ink">{p.description}</dd>
              <dt className="text-muted">Reason</dt>
              <dd className="text-ink">{reason ?? "—"}</dd>
              <dt className="text-muted">Expected outcome</dt>
              <dd className="text-ink">{p.expected_impact}</dd>
              <dt className="text-muted">Arguments</dt>
              <dd>
                <JsonView value={p.arguments} className="max-h-32" />
              </dd>
              {p.references.length ? (
                <>
                  <dt className="text-muted">Runbooks</dt>
                  <dd>
                    <ul className="space-y-0.5 text-xs text-muted">
                      {p.references.map((r) => (
                        <li key={r}>{r}</li>
                      ))}
                    </ul>
                  </dd>
                </>
              ) : null}
              {p.result !== null && p.result !== undefined ? (
                <>
                  <dt className="text-muted">Result</dt>
                  <dd>
                    <JsonView value={p.result} className="max-h-40" />
                  </dd>
                </>
              ) : null}
              {p.error ? (
                <>
                  <dt className="text-muted">Error</dt>
                  <dd className="text-danger">{p.error}</dd>
                </>
              ) : null}
            </dl>
            {isAwaiting ? (
              <div className="mt-3 flex flex-wrap gap-2 border-t border-line pt-3">
                <Button
                  size="sm"
                  onClick={() => {
                    open(p, "approve");
                  }}
                  disabled={!p.executable || !approverValid}
                  title={!approverValid ? "Enter your name first" : undefined}
                >
                  Approve…
                </Button>
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => {
                    open(p, "reject");
                  }}
                  disabled={!approverValid}
                >
                  Reject…
                </Button>
                {!p.executable ? (
                  <span className="self-center text-xs text-muted">
                    Not executable by the agent; apply it manually.
                  </span>
                ) : null}
              </div>
            ) : null}
          </article>
        );
      })}

      {awaiting.length ? (
        <div className="grid gap-3 sm:grid-cols-2">
          <Field
            label="Approver"
            htmlFor="approver"
            hint="Recorded in the audit log with the decision."
          >
            <Input
              id="approver"
              value={approver}
              maxLength={80}
              placeholder="Your name"
              onChange={(e) => {
                setApprover(e.target.value);
              }}
            />
          </Field>
          <Field label="Comment (optional)" htmlFor="approval-comment">
            <Input
              id="approval-comment"
              value={comment}
              maxLength={500}
              onChange={(e) => {
                setComment(e.target.value);
              }}
            />
          </Field>
        </div>
      ) : null}

      {decision.error && !pending ? (
        <ErrorState error={decision.error} title="Decision failed" />
      ) : null}

      <Modal
        open={pending !== null}
        onClose={close}
        title={pending?.decision === "approve" ? "Confirm remediation" : "Confirm rejection"}
        footer={
          <>
            <Button variant="ghost" size="sm" onClick={close}>
              Cancel
            </Button>
            <Button
              size="sm"
              variant={pending?.decision === "approve" ? "danger" : "primary"}
              disabled={!acknowledged || decision.isPending}
              onClick={submit}
            >
              {decision.isPending
                ? "Submitting…"
                : pending?.decision === "approve"
                  ? "Execute action"
                  : "Reject proposal"}
            </Button>
          </>
        }
      >
        {pending ? (
          <div className="space-y-3 text-sm">
            <div className="flex items-start gap-2 rounded-md border border-warn/30 bg-warn-soft/50 px-3 py-2 text-warn">
              <ShieldAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
              <span>
                {pending.decision === "approve"
                  ? "Approving executes this action against the simulated environment immediately, then the agent verifies the outcome."
                  : "Rejecting skips this action; the agent finishes the report without executing it."}
              </span>
            </div>
            <p>
              <span className="font-mono text-ink">{pending.proposal.action}</span>{" "}
              <span className="text-muted">({pending.proposal.risk} risk)</span>
            </p>
            <p className="text-muted">{pending.proposal.description}</p>
            <p className="text-xs text-muted">
              Approver: <span className="text-ink">{approver.trim()}</span>
            </p>
            <label className="flex items-center gap-2 text-sm text-ink">
              <input
                type="checkbox"
                checked={acknowledged}
                onChange={(e) => {
                  setAcknowledged(e.target.checked);
                }}
                className="size-4 accent-brand"
              />
              I have reviewed the action, reason and expected outcome.
            </label>
            {decision.error ? <ErrorState error={decision.error} title="Decision failed" /> : null}
          </div>
        ) : null}
      </Modal>
    </div>
  );
}
