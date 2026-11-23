import { screen } from "@testing-library/react";
import { vi } from "vitest";

import { jsonResponse, renderWithProviders } from "@/test/render";

import type { SystemStatus } from "./api";
import { SystemStatusPanel } from "./SystemStatusPanel";

function status(overrides: Partial<SystemStatus> = {}): SystemStatus {
  return {
    version: "0.1.0",
    environment: "development",
    readiness: {
      status: "ready",
      components: [
        { name: "postgres", status: "ok", latency_ms: 1.2, detail: null },
        { name: "redis", status: "ok", latency_ms: 0.4, detail: null },
      ],
    },
    llm: { provider: "gemini", model: "gemini-3.5-flash-lite", configured: true },
    ...overrides,
  };
}

describe("SystemStatusPanel", () => {
  it("shows ready state with each component", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(status()));

    renderWithProviders(<SystemStatusPanel />);

    expect(await screen.findByText("Ready")).toBeInTheDocument();
    expect(screen.getByText("PostgreSQL")).toBeInTheDocument();
    expect(screen.getByText("Redis")).toBeInTheDocument();
    expect(screen.getAllByText("Healthy")).toHaveLength(2);
    expect(screen.getByText("Configured")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/system/status", expect.anything());
  });

  it("flags unavailable components and a missing LLM key", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse(
        status({
          readiness: {
            status: "not_ready",
            components: [
              { name: "postgres", status: "error", latency_ms: 2000, detail: "TimeoutError" },
            ],
          },
          llm: { provider: "gemini", model: "gemini-3.5-flash-lite", configured: false },
        }),
      ),
    );

    renderWithProviders(<SystemStatusPanel />);

    expect(await screen.findByText("Not ready")).toBeInTheDocument();
    expect(screen.getByText("Unavailable")).toHaveAttribute("title", "TimeoutError");
    expect(screen.getByText("API key missing")).toBeInTheDocument();
  });

  it("explains when the API cannot be reached", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({ title: "Bad Gateway", status: 502 }, 502, "application/problem+json"),
    );

    renderWithProviders(<SystemStatusPanel />);

    expect(await screen.findByRole("alert")).toHaveTextContent("The API is unreachable");
  });
});
