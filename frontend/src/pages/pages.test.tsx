import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { evidence, overview, settings } from "@/test/fixtures";
import {
  calledUrls,
  jsonResponse,
  mockApi,
  problem,
  renderWithProviders,
  requestBody,
} from "@/test/render";

import { ApprovalsPage } from "./ApprovalsPage";
import { IncidentDetailPage } from "./IncidentDetailPage";
import { IncidentsPage } from "./IncidentsPage";
import { InvestigationsPage } from "./InvestigationsPage";
import { MetricsPage } from "./MetricsPage";
import { OverviewPage } from "./OverviewPage";
import { ServicePage } from "./ServicePage";
import { SettingsPage } from "./SettingsPage";
import { ToolCallPage } from "./ToolCallPage";

const systemStatus = {
  version: "0.1.0",
  environment: "development",
  readiness: { status: "ready", components: [] },
  llm: { provider: "gemini", model: null, configured: true },
};

const emptySeries = {
  signal: "error_rate",
  unit: "ratio",
  since_minutes: 60,
  compare_offset_minutes: 0,
  series: [],
};

describe("OverviewPage", () => {
  it("summarises live service health, deployments and investigations", async () => {
    mockApi({
      "GET /api/v1/overview": { ...overview, unavailable: ["loki"] },
      "GET /api/v1/investigations": {
        investigations: [
          {
            id: "inv-9",
            question: "Why is checkout slow?",
            status: "awaiting_approval",
            severity: "SEV2",
            parent_id: null,
            reasoner: "rules",
            created_at: "2026-11-23T10:00:00Z",
            updated_at: "2026-11-23T10:00:00Z",
            root_cause: null,
          },
        ],
      },
      "GET /api/v1/metrics/series": emptySeries,
      "GET /api/v1/system/status": systemStatus,
    });
    renderWithProviders(<OverviewPage />);

    expect(await screen.findByText("1/2")).toBeInTheDocument(); // services healthy
    expect(screen.getAllByRole("link", { name: "orders-service" })[0]).toHaveAttribute(
      "href",
      "/services/orders-service",
    );
    expect(screen.getByText("21.0%")).toHaveClass("text-danger"); // error ratio
    expect(screen.getByText("1 alert")).toBeInTheDocument();
    expect(screen.getByText("Partial data: loki could not be read.")).toBeInTheDocument();
    expect(await screen.findByText("Why is checkout slow?")).toBeInTheDocument();
    expect(screen.getByText("Order history: include line items")).toBeInTheDocument();
  });

  it("shows an error with retry when the API is down, then recovers", async () => {
    let fail = true;
    mockApi({
      "GET /api/v1/overview": () =>
        fail
          ? problem(502, "Bad Gateway", "MCP metrics server unreachable")
          : jsonResponse(overview),
      "GET /api/v1/investigations": { investigations: [] },
      "GET /api/v1/metrics/series": emptySeries,
      "GET /api/v1/system/status": systemStatus,
    });
    renderWithProviders(<OverviewPage />);
    const alert = await screen.findByText("A data source is unavailable");
    expect(alert.closest("[role=alert]")).toHaveTextContent("MCP metrics server unreachable");
    fail = false;
    await userEvent.click(
      within(alert.closest("[role=alert]") as HTMLElement).getByRole("button", { name: "Retry" }),
    );
    expect(await screen.findByText("1/2")).toBeInTheDocument();
  });
});

describe("InvestigationsPage", () => {
  it("starts an investigation from the question box", async () => {
    const fetchMock = mockApi({
      "GET /api/v1/investigations": { investigations: [] },
      "POST /api/v1/investigations": jsonResponse({ id: "inv-5", status: "running" }, 202),
    });
    renderWithProviders(<InvestigationsPage />, {
      route: "/investigations",
      path: "/investigations",
    });
    expect(await screen.findByText("No investigations yet")).toBeInTheDocument();

    const submit = screen.getByRole("button", { name: "Investigate" });
    expect(submit).toBeDisabled();
    await userEvent.click(
      screen.getByRole("button", { name: "Why is the fulfilment queue backing up?" }),
    );
    await userEvent.click(submit);
    expect(await screen.findByTestId("navigated")).toBeInTheDocument();
    expect(requestBody(fetchMock, "/api/v1/investigations")).toEqual({
      question: "Why is the fulfilment queue backing up?",
    });
  });
});

describe("ApprovalsPage", () => {
  it("explains when nothing is waiting", async () => {
    mockApi({ "GET /api/v1/investigations": { investigations: [] } });
    renderWithProviders(<ApprovalsPage />);
    expect(await screen.findByText("No pending approvals")).toBeInTheDocument();
  });
});

describe("ToolCallPage", () => {
  it("shows arguments, response and failure details for one tool call", async () => {
    const failed = evidence[1];
    mockApi({
      "GET /api/v1/investigations/inv-1/tool-calls/ev-002": {
        ...failed,
        arguments: { since_minutes: 30, top_k: 10 },
        data: null,
        error_message: "Loki did not answer within 5 s",
      },
    });
    renderWithProviders(<ToolCallPage />, {
      route: "/investigations/inv-1/tools/ev-002",
      path: "/investigations/:id/tools/:evidenceId",
    });
    expect(
      await screen.findByRole("heading", { name: "logs.aggregate_errors" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Failed")).toBeInTheDocument();
    expect(screen.getByText("Loki did not answer within 5 s")).toBeInTheDocument();
    expect(screen.getByText(/"since_minutes": 30/)).toBeInTheDocument();
  });

  it("reports a missing tool call", async () => {
    mockApi({});
    renderWithProviders(<ToolCallPage />, {
      route: "/investigations/inv-1/tools/ev-404",
      path: "/investigations/:id/tools/:evidenceId",
    });
    expect(await screen.findByText("Tool call not available")).toBeInTheDocument();
  });
});

describe("ServicePage", () => {
  it("renders signals, container, deployments and logs for a service", async () => {
    mockApi({
      "GET /api/v1/services/orders-service": {
        service: "orders-service",
        status: overview.services[1],
        metrics: overview.golden_signals["orders-service"],
        container: {
          service: "orders-service",
          up: true,
          started_at: "2026-11-23T09:00:00Z",
          restarts_last_hour: 2,
          memory_bytes: 75_000_000,
          memory_limit_bytes: 268_435_456,
          memory_utilization: 0.28,
          open_fds: 25,
          oom_risk: false,
        },
        logs: [],
        error_logs: [
          {
            timestamp: "2026-11-23T09:59:00Z",
            service: "orders-service",
            severity: "ERROR",
            message: "timed out waiting for a database connection",
            endpoint: null,
            version: "2.4.0",
            trace_id: null,
            error_type: "PoolTimeoutError",
            error_message: null,
          },
        ],
        deployments: overview.deployments,
        series: [],
      },
    });
    renderWithProviders(<ServicePage />, {
      route: "/services/orders-service",
      path: "/services/:name",
    });
    expect(
      await screen.findByText("timed out waiting for a database connection"),
    ).toBeInTheDocument();
    expect(screen.getByText("Degraded")).toBeInTheDocument();
    expect(screen.getByText("2.40 s")).toBeInTheDocument(); // latency p95 tile
    expect(screen.getByText("[PoolTimeoutError]")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Investigate orders-service" })).toBeEnabled();
  });

  it("handles an unknown service", async () => {
    mockApi({ "GET /api/v1/services/nope": () => problem(404, "Not Found") });
    renderWithProviders(<ServicePage />, { route: "/services/nope", path: "/services/:name" });
    expect(await screen.findByText("Unknown service")).toBeInTheDocument();
  });
});

describe("MetricsPage", () => {
  it("queries the selected services and overlays a shifted baseline when comparing", async () => {
    const fetchMock = mockApi({
      "GET /api/v1/metrics/catalog": {
        services: ["api-gateway", "orders-service", "fulfilment-worker"],
        signals: [
          { signal: "latency_p95", unit: "s", services: ["api-gateway", "orders-service"] },
          { signal: "queue_depth", unit: "jobs", services: ["fulfilment-worker"] },
        ],
      },
      "GET /api/v1/metrics/series": (url: URL) =>
        jsonResponse({
          signal: "latency_p95",
          unit: "s",
          since_minutes: 60,
          compare_offset_minutes: Number(url.searchParams.get("compare_offset_minutes") ?? 0),
          series: url.searchParams.getAll("services").map((service) => ({
            service,
            signal: "latency_p95",
            unit: "s",
            points: [
              ["2026-11-23T10:00:00Z", 0.2],
              ["2026-11-23T10:01:00Z", 0.4],
            ],
            ...(url.searchParams.get("compare_offset_minutes")
              ? { baseline: [["2026-11-23T09:00:00Z", 0.1]], offset_minutes: 60 }
              : {}),
          })),
        }),
    });
    renderWithProviders(<MetricsPage />, { route: "/metrics", path: "/metrics" });

    expect(await screen.findByRole("button", { name: "fulfilment-worker" })).toBeDisabled();
    await waitFor(() => {
      expect(screen.getAllByText("400 ms").length).toBeGreaterThan(0); // last/max stats
    });
    const firstUrl = calledUrls(fetchMock, "/series")[0];
    expect(firstUrl).toContain("services=api-gateway&services=orders-service");
    expect(firstUrl).not.toContain("compare_offset_minutes");

    await userEvent.click(screen.getByRole("radio", { name: "1 h earlier" }));
    expect(await screen.findByText("api-gateway (60 min earlier)")).toBeInTheDocument();
    const lastUrl = calledUrls(fetchMock, "/series").at(-1);
    expect(lastUrl).toContain("compare_offset_minutes=60");
  });
});

describe("IncidentsPage", () => {
  it("runs a semantic search with filters", async () => {
    const fetchMock = mockApi({
      "GET /api/v1/investigations": { investigations: [] },
      "GET /api/v1/knowledge/incidents": (url: URL) =>
        jsonResponse({
          query: url.searchParams.get("q"),
          confident: url.searchParams.get("q") ? true : null,
          incidents: [
            {
              document_id: "INC-2026-044",
              incident_id: "INC-2026-044",
              title: "Redis node unreachable",
              source: "incidents/INC-2026-044.md",
              severity: "SEV2",
              category: "cache_failure",
              services: ["api-gateway"],
              tags: ["redis"],
              date: "2026-04-02",
              ...(url.searchParams.get("q")
                ? { score: 0.91, snippet: "Client-side Redis timeouts" }
                : {}),
            },
          ],
        }),
    });
    renderWithProviders(<IncidentsPage />, { route: "/incidents", path: "/incidents" });
    expect(await screen.findByRole("link", { name: "Redis node unreachable" })).toHaveAttribute(
      "href",
      "/incidents/INC-2026-044",
    );
    await userEvent.type(screen.getByLabelText("Search incidents"), "redis down");
    await userEvent.selectOptions(screen.getByLabelText("Severity"), "SEV2");
    await userEvent.click(screen.getByRole("button", { name: "Search" }));
    expect(await screen.findByText("Client-side Redis timeouts")).toBeInTheDocument();
    const url = calledUrls(fetchMock, "/knowledge/").at(-1);
    expect(url).toContain("q=redis+down");
    expect(url).toContain("severity=SEV2");
  });

  it("shows a postmortem with similar incidents", async () => {
    mockApi({
      "GET /api/v1/knowledge/incidents/INC-2026-044": {
        incident: {
          document_id: "INC-2026-044",
          incident_id: "INC-2026-044",
          title: "Redis node unreachable",
          source: "incidents/INC-2026-044.md",
          severity: "SEV2",
          category: "cache_failure",
          services: ["api-gateway"],
          tags: ["redis"],
          date: "2026-04-02",
        },
        sections: [{ section: "INC-2026-044 > Resolution", content: "Network change reverted." }],
        similar: [
          {
            document_id: "INC-2026-058",
            incident_id: "INC-2026-058",
            title: "Payments degradation",
            category: "dependency_failure",
            similarity: 0.61,
          },
        ],
      },
    });
    renderWithProviders(<IncidentDetailPage />, {
      route: "/incidents/INC-2026-044",
      path: "/incidents/:documentId",
    });
    expect(await screen.findByText("Network change reverted.")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Resolution" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Payments degradation" })).toHaveAttribute(
      "href",
      "/incidents/INC-2026-058",
    );
  });

  it("explains when the knowledge base is not ready", async () => {
    mockApi({
      "GET /api/v1/investigations": { investigations: [] },
      "GET /api/v1/knowledge/incidents": () =>
        problem(503, "Service Unavailable", "knowledge base not initialised"),
    });
    renderWithProviders(<IncidentsPage />, { route: "/incidents", path: "/incidents" });
    expect(await screen.findByText("Service not ready")).toBeInTheDocument();
  });
});

describe("SettingsPage", () => {
  it("shows integrations and saves validated agent limits", async () => {
    const fetchMock = mockApi({
      "GET /api/v1/settings": settings,
      "PUT /api/v1/settings/agent": (_url: URL, init?: RequestInit) =>
        jsonResponse({ agent: JSON.parse(init?.body as string) as unknown }),
    });
    renderWithProviders(<SettingsPage />);

    expect(await screen.findByText("MCP reachable")).toBeInTheDocument();
    expect(screen.getByText("writes (gated)")).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "LLM (API key missing)" })).toBeDisabled();

    const save = screen.getByRole("button", { name: "Save limits" });
    expect(save).toBeDisabled();
    const rounds = screen.getByLabelText("Max investigation rounds");
    await userEvent.clear(rounds);
    await userEvent.type(rounds, "9");
    expect(screen.getByText("Must be between 1 and 6")).toBeInTheDocument();
    expect(save).toBeDisabled();

    await userEvent.clear(rounds);
    await userEvent.type(rounds, "4");
    await userEvent.click(save);
    await waitFor(() => {
      expect(requestBody(fetchMock, "/api/v1/settings/agent")).toEqual({
        ...settings.agent,
        max_rounds: 4,
      });
    });
  });
});
