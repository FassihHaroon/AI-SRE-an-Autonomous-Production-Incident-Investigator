import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiGet, apiPost } from "@/lib/api";
import { isTerminal } from "@/lib/status";
import type { InvestigationDetail, InvestigationSummary, ToolCallDetail } from "@/lib/types";

export const investigationKeys = {
  all: ["investigations"] as const,
  list: () => ["investigations", "list"] as const,
  detail: (id: string) => ["investigations", "detail", id] as const,
  toolCall: (id: string, evidenceId: string) =>
    ["investigations", "detail", id, "tool", evidenceId] as const,
};

export function useInvestigations() {
  return useQuery({
    queryKey: investigationKeys.list(),
    queryFn: ({ signal }) =>
      apiGet<{ investigations: InvestigationSummary[] }>("/api/v1/investigations", { signal }),
    refetchInterval: (q) =>
      q.state.data?.investigations.some((i) => !isTerminal(i.status)) ? 5_000 : 30_000,
  });
}

/** One investigation with its live graph state. `poll` is the fallback when SSE is down. */
export function useInvestigation(id: string, poll = false) {
  return useQuery({
    queryKey: investigationKeys.detail(id),
    queryFn: ({ signal }) =>
      apiGet<InvestigationDetail>(`/api/v1/investigations/${encodeURIComponent(id)}`, { signal }),
    refetchInterval: (q) =>
      poll && !isTerminal(q.state.data?.investigation.status) ? 4_000 : false,
  });
}

export function useToolCall(id: string, evidenceId: string) {
  return useQuery({
    queryKey: investigationKeys.toolCall(id, evidenceId),
    queryFn: ({ signal }) =>
      apiGet<ToolCallDetail>(
        `/api/v1/investigations/${encodeURIComponent(id)}/tool-calls/${encodeURIComponent(evidenceId)}`,
        { signal },
      ),
    staleTime: Infinity,
  });
}

export function useStartInvestigation() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (question: string) =>
      apiPost<{ id: string; status: string }>("/api/v1/investigations", { question }),
    onSuccess: () => client.invalidateQueries({ queryKey: investigationKeys.list() }),
  });
}

export function useFollowUp(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (question: string) =>
      apiPost<{ id: string; status: string }>(
        `/api/v1/investigations/${encodeURIComponent(id)}/follow-ups`,
        { question },
      ),
    onSuccess: () => client.invalidateQueries({ queryKey: investigationKeys.list() }),
  });
}

export interface DecisionInput {
  proposal_id: string;
  decision: "approve" | "reject";
  approver: string;
  comment?: string | undefined;
  confirm: boolean;
}

export function useDecision(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: DecisionInput) =>
      apiPost<{ id: string; status: string }>(
        `/api/v1/investigations/${encodeURIComponent(id)}/approval`,
        input,
      ),
    onSettled: () => client.invalidateQueries({ queryKey: investigationKeys.all }),
  });
}

export function useCancel(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () =>
      apiPost<{ id: string; status: string }>(
        `/api/v1/investigations/${encodeURIComponent(id)}/cancel`,
      ),
    onSettled: () => client.invalidateQueries({ queryKey: investigationKeys.all }),
  });
}
