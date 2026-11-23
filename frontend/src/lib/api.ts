/**
 * Minimal typed HTTP client for the AI SRE API.
 *
 * All calls go to same-origin `/api/...`: Vite proxies them in development and nginx in
 * Docker, so no CORS configuration and no API URL in the bundle.
 */

/** RFC 9457 problem details, as returned by the backend for every error. */
export interface ProblemDetails {
  type?: string;
  title: string;
  status: number;
  detail?: string;
}

export class ApiError extends Error {
  readonly status: number;
  readonly problem: ProblemDetails | undefined;

  constructor(status: number, problem?: ProblemDetails) {
    super(problem?.title ?? `Request failed with status ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.problem = problem;
  }
}

async function parseProblem(response: Response): Promise<ProblemDetails | undefined> {
  const type = response.headers.get("content-type") ?? "";
  if (!type.includes("json")) return undefined;
  try {
    return (await response.json()) as ProblemDetails;
  } catch {
    return undefined;
  }
}

export async function apiGet<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (!headers.has("Accept")) headers.set("Accept", "application/json");
  const response = await fetch(path, { ...init, headers });
  if (!response.ok) {
    throw new ApiError(response.status, await parseProblem(response));
  }
  return (await response.json()) as T;
}
