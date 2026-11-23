import { useQuery } from "@tanstack/react-query";

import { fetchSystemStatus } from "./api";

export const systemStatusQueryKey = ["system", "status"] as const;

export function useSystemStatus() {
  return useQuery({
    queryKey: systemStatusQueryKey,
    queryFn: ({ signal }) => fetchSystemStatus(signal),
    refetchInterval: 15_000,
  });
}
