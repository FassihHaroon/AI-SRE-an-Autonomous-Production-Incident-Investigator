import { apiGet } from "@/lib/api";

// Mirrors backend `ai_sre.api.routes.health.SystemStatus`. These hand-written types will be
// replaced by types generated from the OpenAPI schema once the API surface grows.
export interface ComponentHealth {
  name: string;
  status: "ok" | "error";
  latency_ms: number;
  detail: string | null;
}

export interface SystemStatus {
  version: string;
  environment: string;
  readiness: {
    status: "ready" | "not_ready";
    components: ComponentHealth[];
  };
  llm: {
    provider: string;
    model: string | null;
    configured: boolean;
  };
}

export function fetchSystemStatus(signal?: AbortSignal): Promise<SystemStatus> {
  return apiGet<SystemStatus>("/api/v1/system/status", signal ? { signal } : undefined);
}
