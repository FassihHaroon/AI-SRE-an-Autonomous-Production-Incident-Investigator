import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";

import { ApiError } from "@/lib/api";

import { ConfidenceMeter, ErrorState, Modal, QueryState } from ".";

describe("ErrorState", () => {
  it("distinguishes network failures from API errors and offers retry", async () => {
    const onRetry = vi.fn();
    const { rerender } = render(
      <ErrorState error={new TypeError("Failed to fetch")} onRetry={onRetry} />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Cannot reach the API");
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(onRetry).toHaveBeenCalledOnce();

    rerender(
      <ErrorState
        error={new ApiError(502, { title: "Bad Gateway", status: 502, detail: "loki down" })}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("A data source is unavailable");
    expect(screen.getByRole("alert")).toHaveTextContent("HTTP 502 · loki down");
  });
});

describe("QueryState", () => {
  const base = { isFetching: false, refetch: vi.fn() };

  it("renders loading, error, empty and content states", () => {
    const { rerender } = render(
      <QueryState query={{ ...base, data: undefined, error: null, isPending: true }}>
        {() => "content"}
      </QueryState>,
    );
    expect(screen.getByRole("status", { name: "Loading" })).toBeInTheDocument();

    rerender(
      <QueryState query={{ ...base, data: undefined, error: new Error("boom"), isPending: false }}>
        {() => "content"}
      </QueryState>,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("boom");

    rerender(
      <QueryState
        query={{ ...base, data: [], error: null, isPending: false }}
        isEmpty={(d) => !d.length}
      >
        {() => "content"}
      </QueryState>,
    );
    expect(screen.getByText("Nothing here yet")).toBeInTheDocument();

    rerender(
      <QueryState query={{ ...base, data: [1], error: new Error("stale"), isPending: false }}>
        {() => "content"}
      </QueryState>,
    );
    expect(screen.getByText("content")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Showing last known data");
  });
});

describe("ConfidenceMeter", () => {
  it("exposes the value and band accessibly", () => {
    render(<ConfidenceMeter value={0.68} band="medium" />);
    const meter = screen.getByRole("meter", { name: "Confidence" });
    expect(meter).toHaveAttribute("aria-valuenow", "0.68");
    expect(meter).toHaveAttribute("aria-valuetext", "68% (medium)");
  });
});

describe("Modal", () => {
  it("closes on Escape", async () => {
    const onClose = vi.fn();
    render(
      <Modal open title="Confirm" onClose={onClose}>
        body
      </Modal>,
    );
    expect(screen.getByRole("dialog", { name: "Confirm" })).toHaveFocus();
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalled();
  });
});
