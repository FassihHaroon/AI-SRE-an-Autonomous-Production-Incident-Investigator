import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiGet, apiPut, qs } from "@/lib/api";
import type { AgentSettings, IncidentDetail, IncidentSearch, PlatformSettings } from "@/lib/types";

export interface IncidentFilters {
  q?: string | undefined;
  service?: string | undefined;
  severity?: string | undefined;
  category?: string | undefined;
}

export function useIncidentSearch(filters: IncidentFilters) {
  return useQuery({
    queryKey: ["knowledge", "incidents", filters],
    queryFn: ({ signal }) =>
      apiGet<IncidentSearch>(`/api/v1/knowledge/incidents${qs({ ...filters })}`, { signal }),
    placeholderData: keepPreviousData,
  });
}

export function useIncident(documentId: string) {
  return useQuery({
    queryKey: ["knowledge", "incident", documentId],
    queryFn: ({ signal }) =>
      apiGet<IncidentDetail>(`/api/v1/knowledge/incidents/${encodeURIComponent(documentId)}`, {
        signal,
      }),
  });
}

export function useSettings() {
  return useQuery({
    queryKey: ["settings"],
    queryFn: ({ signal }) => apiGet<PlatformSettings>("/api/v1/settings", { signal }),
  });
}

export function useSaveAgentSettings() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (agent: AgentSettings) =>
      apiPut<{ agent: AgentSettings }>("/api/v1/settings/agent", agent),
    onSuccess: () => client.invalidateQueries({ queryKey: ["settings"] }),
  });
}
