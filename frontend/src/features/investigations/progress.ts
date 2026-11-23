import type { InvestigationEvent } from "@/lib/types";

/** Graph nodes in execution order; the loop (tool selection → replanning) may repeat. */
export const NODE_ORDER: [string, string][] = [
  ["intake", "Understand question"],
  ["planning", "Plan"],
  ["tool_selection", "Select tools"],
  ["tool_execution", "Gather evidence"],
  ["evidence_analysis", "Analyse evidence"],
  ["hypothesis_evaluation", "Evaluate hypotheses"],
  ["replanning", "Decide next step"],
  ["root_cause", "Root cause"],
  ["remediation_proposal", "Propose remediation"],
  ["approval", "Approval"],
  ["remediation_execution", "Execute action"],
  ["verification", "Verify"],
  ["report", "Report"],
];

export type StepState = "done" | "active" | "pending" | "failed";

export function stepStates(
  events: InvestigationEvent[],
  status: string,
): Record<string, StepState> {
  const states: Record<string, StepState> = {};
  for (const e of events) {
    const node = typeof e.data.node === "string" ? e.data.node : null;
    if (!node) continue;
    if (e.type === "node_started") states[node] = "active";
    if (e.type === "node_completed") states[node] = "done";
  }
  if (status === "awaiting_approval") states.approval = "active";
  if (status === "failed" || status === "cancelled") {
    for (const [k, v] of Object.entries(states)) if (v === "active") states[k] = "failed";
  }
  if (status === "completed") {
    for (const [k, v] of Object.entries(states)) if (v === "active") states[k] = "done";
  }
  return states;
}
