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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (!headers.has("Accept")) headers.set("Accept", "application/json");
  const response = await fetch(path, { ...init, headers });
  if (!response.ok) {
    throw new ApiError(response.status, await parseProblem(response));
  }
  return (await response.json()) as T;
}

export function apiGet<T>(path: string, init?: RequestInit): Promise<T> {
  return request<T>(path, init);
}

function withBody(method: string, body: unknown, init?: RequestInit): RequestInit {
  const headers = new Headers(init?.headers);
  headers.set("Content-Type", "application/json");
  return { ...init, method, headers, body: JSON.stringify(body) };
}

export function apiPost<T>(path: string, body: unknown = {}, init?: RequestInit): Promise<T> {
  return request<T>(path, withBody("POST", body, init));
}

export function apiPut<T>(path: string, body: unknown, init?: RequestInit): Promise<T> {
  return request<T>(path, withBody("PUT", body, init));
}

/** Human-readable message for any error thrown by the client or fetch. */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.problem?.title ?? error.message;
  if (error instanceof Error) return error.message;
  return "Unexpected error";
}

export function qs(
  params: Record<string, string | number | boolean | string[] | null | undefined>,
): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    if (Array.isArray(value)) {
      for (const v of value) search.append(key, v);
    } else search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}
