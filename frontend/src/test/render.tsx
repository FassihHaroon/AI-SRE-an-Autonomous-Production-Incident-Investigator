import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, type RenderResult } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter, Route, Routes } from "react-router";
import { vi } from "vitest";

/**
 * Render with the providers every page needs. Retries are off so failures surface fast.
 * Pass `path` (e.g. "/investigations/:id") when the component reads route params.
 */
export function renderWithProviders(
  ui: ReactElement,
  { route = "/", path }: { route?: string; path?: string } = {},
): RenderResult & { client: QueryClient } {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const result = render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[route]}>
        {path ? (
          <Routes>
            <Route path={path} element={ui} />
            <Route path="*" element={<div data-testid="navigated" />} />
          </Routes>
        ) : (
          ui
        )}
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...result, client };
}

export function jsonResponse(body: unknown, status = 200, contentType = "application/json") {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": contentType },
  });
}

export function problem(status: number, title: string, detail?: string) {
  return jsonResponse({ title, status, detail }, status, "application/problem+json");
}

type Handler = (url: URL, init: RequestInit | undefined) => Response | Promise<Response>;

/**
 * Route `fetch` calls by "METHOD /path" (query string excluded). Unmatched calls fail the
 * request with 404 so a test never silently depends on a real network.
 */
/** Route values are a JSON body (200) or a Handler returning a Response. */
export function mockApi(routes: Record<string, unknown>) {
  return vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = new URL(urlOf(input), "http://localhost");
    const method = (init?.method ?? "GET").toUpperCase();
    const route = routes[`${method} ${url.pathname}`];
    if (route === undefined) return problem(404, `No mock for ${method} ${url.pathname}`);
    if (typeof route === "function") return (route as Handler)(url, init);
    return jsonResponse(route);
  });
}

function urlOf(input: string | URL | Request): string {
  return typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
}

type FetchMock = ReturnType<typeof mockApi>;

/** URLs (with query strings) of every mocked fetch call containing `fragment`. */
export function calledUrls(fetchMock: FetchMock, fragment = ""): string[] {
  return fetchMock.mock.calls.map(([input]) => urlOf(input)).filter((u) => u.includes(fragment));
}

/** Parsed JSON body of the n-th request with a body sent to exactly `path`. */
export function requestBody(fetchMock: FetchMock, path: string, index = 0): unknown {
  const bodies = fetchMock.mock.calls
    .filter(
      ([input, init]) => urlOf(input).split("?")[0] === path && typeof init?.body === "string",
    )
    .map(([, init]) => init?.body as string);
  const body = bodies[index];
  return body === undefined ? undefined : JSON.parse(body);
}
