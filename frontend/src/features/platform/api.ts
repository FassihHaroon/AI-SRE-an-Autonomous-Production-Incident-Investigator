import { keepPreviousData, useQuery } from "@tanstack/react-query";

import { apiGet, qs } from "@/lib/api";
import type {
  Deployment,
  LogEntry,
  MetricCatalog,
  Overview,
  ServiceDetail,
  ServiceStatus,
  SeriesResponse,
} from "@/lib/types";

const LIVE_MS = 15_000;

export function useOverview() {
  return useQuery({
    queryKey: ["overview"],
    queryFn: ({ signal }) => apiGet<Overview>("/api/v1/overview", { signal }),
    refetchInterval: LIVE_MS,
  });
}

export function useServices() {
  return useQuery({
    queryKey: ["services"],
    queryFn: ({ signal }) => apiGet<{ services: ServiceStatus[] }>("/api/v1/services", { signal }),
    refetchInterval: LIVE_MS,
  });
}

export function useServiceDetail(service: string) {
  return useQuery({
    queryKey: ["services", service],
    queryFn: ({ signal }) =>
      apiGet<ServiceDetail>(`/api/v1/services/${encodeURIComponent(service)}`, { signal }),
    refetchInterval: LIVE_MS,
  });
}

export function useMetricCatalog() {
  return useQuery({
    queryKey: ["metrics", "catalog"],
    queryFn: ({ signal }) => apiGet<MetricCatalog>("/api/v1/metrics/catalog", { signal }),
    staleTime: Infinity,
  });
}

export interface SeriesParams {
  services: string[];
  signal: string;
  sinceMinutes: number;
  compareOffsetMinutes: number;
}

export function useSeries(params: SeriesParams, enabled = true) {
  return useQuery({
    queryKey: ["metrics", "series", params],
    queryFn: ({ signal }) =>
      apiGet<SeriesResponse>(
        `/api/v1/metrics/series${qs({
          services: params.services,
          signal: params.signal,
          since_minutes: params.sinceMinutes,
          compare_offset_minutes: params.compareOffsetMinutes || undefined,
        })}`,
        { signal },
      ),
    enabled: enabled && params.services.length > 0,
    placeholderData: keepPreviousData,
    refetchInterval: 30_000,
  });
}

export function useDeployments(service?: string) {
  return useQuery({
    queryKey: ["deployments", service ?? "all"],
    queryFn: ({ signal }) =>
      apiGet<{ deployments: Deployment[] }>(`/api/v1/deployments${qs({ service })}`, { signal }),
    refetchInterval: 30_000,
  });
}

export function useLogs(params: { service?: string; level?: string; contains?: string }) {
  return useQuery({
    queryKey: ["logs", params],
    queryFn: ({ signal }) =>
      apiGet<{ entries: LogEntry[]; summary: string; truncated: boolean }>(
        `/api/v1/logs${qs(params)}`,
        { signal },
      ),
    placeholderData: keepPreviousData,
  });
}
