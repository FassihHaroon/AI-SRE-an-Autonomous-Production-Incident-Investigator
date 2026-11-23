import { useQuery } from "@tanstack/react-query";

import { apiGet } from "@/lib/api";
import { isTerminal } from "@/lib/status";
import type { EvalRun, EvalRunSummary, InvestigationTrace } from "@/lib/types";

export function useEvalRuns() {
  return useQuery({
    queryKey: ["evaluations"],
    queryFn: ({ signal }) =>
      apiGet<{ runs: EvalRunSummary[]; results_dir_found: boolean }>("/api/v1/evaluations", {
        signal,
      }),
  });
}

export function useEvalRun(runId: string | undefined) {
  return useQuery({
    queryKey: ["evaluations", runId],
    queryFn: ({ signal }) =>
      apiGet<EvalRun>(`/api/v1/evaluations/${encodeURIComponent(runId ?? "")}`, { signal }),
    enabled: Boolean(runId),
    staleTime: Infinity,
  });
}

/** Polls while the investigation is still running; stops once it is terminal. */
export function useTrace(investigationId: string) {
  return useQuery({
    queryKey: ["investigations", "trace", investigationId],
    queryFn: ({ signal }) =>
      apiGet<InvestigationTrace>(
        `/api/v1/investigations/${encodeURIComponent(investigationId)}/trace`,
        { signal },
      ),
    refetchInterval: (q) => (q.state.data && isTerminal(q.state.data.status) ? false : 3_000),
  });
}
