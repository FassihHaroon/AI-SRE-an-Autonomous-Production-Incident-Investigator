import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { FakeEventSource } from "@/test/eventsource";
import { detail, evidence, proposal, report, rootCause } from "@/test/fixtures";
import { jsonResponse, mockApi, problem, renderWithProviders, requestBody } from "@/test/render";

import { InvestigationPage } from "./InvestigationPage";

const PATH = "/investigations/:id";

function renderPage() {
  return renderWithProviders(<InvestigationPage />, { route: "/investigations/inv-1", path: PATH });
}

describe("InvestigationPage", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("streams live progress and refreshes state on events", async () => {
    let calls = 0;
    mockApi({
      "GET /api/v1/investigations/inv-1": () => {
        calls += 1;
        return jsonResponse(
          detail({ status: "running" }, calls === 1 ? {} : { evidence, round: 1, hypotheses: [] }),
        );
      },
      "GET /api/v1/investigations": { investigations: [] },
    });
    renderPage();

    expect(
      await screen.findByRole("heading", { name: "Why are checkout requests failing?" }),
    ).toBeInTheDocument();
    const es = FakeEventSource.latest();
    act(() => {
      es.open();
      es.emit(1, "node_started", { node: "tool_execution", label: "Gathering evidence" });
      es.emit(2, "tool_call", { ...evidence[0], arguments: {} });
    });

    expect(screen.getByText("Live")).toBeInTheDocument();
    expect(screen.getByText("Gathering evidence…")).toBeInTheDocument();
    const timeline = screen.getByRole("list", { name: "Live timeline" });
    expect(within(timeline).getByText(/deployments\.list_recent_deployments/)).toBeInTheDocument();
    expect(
      screen
        .getByRole("list", { name: "Investigation progress" })
        .querySelector('[data-state="active"]'),
    ).toHaveTextContent("Gather evidence");

    // The tool_call event triggers a state refetch that brings the evidence table.
    expect(await screen.findByText("upstream_unavailable: Loki timed out")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "ev-002" })).toHaveAttribute(
      "href",
      "/investigations/inv-1/tools/ev-002",
    );
  });

  it("shows a reconnect control when live updates fail", async () => {
    mockApi({
      "GET /api/v1/investigations/inv-1": detail({ status: "running" }),
      "GET /api/v1/investigations": { investigations: [] },
    });
    renderPage();
    await screen.findByRole("heading", { name: "Why are checkout requests failing?" });
    const first = FakeEventSource.latest();
    act(() => {
      first.fail(true);
    });
    expect(screen.getByText("Live updates unavailable (polling)")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Reconnect" }));
    expect(FakeEventSource.latest()).not.toBe(first);
  });

  it("requires explicit confirmation before approving a remediation", async () => {
    const fetchMock = mockApi({
      "GET /api/v1/investigations/inv-1": detail(
        { status: "awaiting_approval", root_cause: rootCause },
        { proposals: [proposal], evidence },
      ),
      "GET /api/v1/investigations": { investigations: [] },
      "POST /api/v1/investigations/inv-1/approval": { id: "inv-1", status: "running" },
    });
    renderPage();

    const card = await screen.findByRole("article", { name: "Proposal p1" });
    expect(within(card).getByText("Roll back orders-service, undoing d-408.")).toBeInTheDocument();
    expect(within(card).getByText(/symptoms clear within/)).toBeInTheDocument();
    expect(within(card).getByText("Medium risk")).toBeInTheDocument();

    const approve = within(card).getByRole("button", { name: "Approve…" });
    expect(approve).toBeDisabled(); // no approver yet
    await userEvent.type(screen.getByLabelText("Approver"), "Dana Oncall");
    await userEvent.click(approve);

    const dialog = screen.getByRole("dialog", { name: "Confirm remediation" });
    const execute = within(dialog).getByRole("button", { name: "Execute action" });
    expect(execute).toBeDisabled();
    await userEvent.click(within(dialog).getByRole("checkbox"));
    await userEvent.click(execute);

    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
    expect(requestBody(fetchMock, "/api/v1/investigations/inv-1/approval")).toEqual({
      proposal_id: "p1",
      decision: "approve",
      approver: "Dana Oncall",
      confirm: true,
    });
    expect(localStorage.getItem("ai-sre.approver")).toBe("Dana Oncall");
  });

  it("surfaces an approval conflict from the API", async () => {
    mockApi({
      "GET /api/v1/investigations/inv-1": detail(
        { status: "awaiting_approval", root_cause: rootCause },
        { proposals: [proposal] },
      ),
      "GET /api/v1/investigations": { investigations: [] },
      "POST /api/v1/investigations/inv-1/approval": () =>
        problem(409, "Conflict", "investigation is not awaiting approval"),
    });
    renderPage();
    await userEvent.type(await screen.findByLabelText("Approver"), "Dana");
    await userEvent.click(screen.getByRole("button", { name: "Reject…" }));
    const dialog = screen.getByRole("dialog", { name: "Confirm rejection" });
    await userEvent.click(within(dialog).getByRole("checkbox"));
    await userEvent.click(within(dialog).getByRole("button", { name: "Reject proposal" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "investigation is not awaiting approval",
    );
  });

  it("opens the report with root cause, alternatives and citations when completed", async () => {
    mockApi({
      "GET /api/v1/investigations/inv-1": detail(
        { status: "completed", root_cause: rootCause, report },
        { evidence },
      ),
      "GET /api/v1/investigations": { investigations: [] },
    });
    renderPage();

    expect(await screen.findByText(report.summary)).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Report" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getAllByRole("meter", { name: "Confidence" })[0]).toHaveAttribute(
      "aria-valuenow",
      "0.82",
    );
    expect(screen.getByText("Slow queries on PostgreSQL")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Order history N+1 query saturated the DB pool" }),
    ).toHaveAttribute("href", "/incidents/INC-2026-031");
    expect(screen.getByText("Rolling back a deployment")).toBeInTheDocument();
    expect(screen.getByText("Resolved")).toBeInTheDocument();
    // Follow-ups are enabled once finished.
    expect(screen.getByLabelText("Follow-up question")).toBeEnabled();
  });

  it("starts a linked follow-up investigation", async () => {
    const fetchMock = mockApi({
      "GET /api/v1/investigations/inv-1": detail({ status: "completed", report }),
      "GET /api/v1/investigations": { investigations: [] },
      "POST /api/v1/investigations/inv-1/follow-ups": { id: "inv-2", status: "running" },
    });
    renderPage();
    await userEvent.type(
      await screen.findByLabelText("Follow-up question"),
      "Is the DB pool affected?",
    );
    await userEvent.click(screen.getByRole("button", { name: "Ask" }));
    // Navigates to the child's workspace (unmocked here, so it renders as not found).
    expect(await screen.findByText("No investigation with id inv-2.")).toBeInTheDocument();
    expect(requestBody(fetchMock, "/api/v1/investigations/inv-1/follow-ups")).toEqual({
      question: "Is the DB pool affected?",
    });
  });

  it("shows not-found for an unknown investigation", async () => {
    mockApi({ "GET /api/v1/investigations": { investigations: [] } });
    renderPage();
    expect(await screen.findByText("Investigation not found")).toBeInTheDocument();
  });
});
