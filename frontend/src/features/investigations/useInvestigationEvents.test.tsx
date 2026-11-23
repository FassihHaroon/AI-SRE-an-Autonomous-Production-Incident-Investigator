import { act, renderHook } from "@testing-library/react";
import { vi } from "vitest";

import { FakeEventSource } from "@/test/eventsource";

import { useInvestigationEvents } from "./useInvestigationEvents";

describe("useInvestigationEvents", () => {
  it("collects events in order and de-duplicates replayed ids", () => {
    const onEvent = vi.fn();
    const { result } = renderHook(() => useInvestigationEvents("inv-1", onEvent));
    const es = FakeEventSource.latest();
    expect(es.url).toBe("/api/v1/investigations/inv-1/events");
    expect(result.current.connection).toBe("connecting");

    act(() => {
      es.open();
      es.emit(1, "status", { status: "running" });
      es.emit(2, "node_started", { node: "intake", label: "Understanding the question" });
      es.emit(2, "node_started", { node: "intake", label: "Understanding the question" });
    });

    expect(result.current.connection).toBe("open");
    expect(result.current.events.map((e) => e.type)).toEqual(["status", "node_started"]);
    expect(onEvent).toHaveBeenCalledTimes(2);
  });

  it("reports reconnecting while the browser retries and failed when it gives up", () => {
    const { result } = renderHook(() => useInvestigationEvents("inv-1"));
    const es = FakeEventSource.latest();
    act(() => {
      es.open();
      es.fail();
    });
    expect(result.current.connection).toBe("reconnecting");
    act(() => {
      es.fail(true);
    });
    expect(result.current.connection).toBe("failed");
  });

  it("closes on the end event and resumes after the last id on reconnect", () => {
    const { result } = renderHook(() => useInvestigationEvents("inv-1"));
    const first = FakeEventSource.latest();
    act(() => {
      first.open();
      first.emit(7, "approval_required", {});
      first.end();
    });
    expect(result.current.connection).toBe("ended");
    expect(first.readyState).toBe(FakeEventSource.CLOSED);

    act(() => {
      result.current.reconnect();
    });
    const second = FakeEventSource.latest();
    expect(second).not.toBe(first);
    expect(second.url).toBe("/api/v1/investigations/inv-1/events?after=7");
    expect(result.current.connection).toBe("connecting");
    act(() => {
      second.emit(8, "status", { status: "running" });
    });
    expect(result.current.events.map((e) => e.id)).toEqual([7, 8]);
  });

  it("starts from scratch for another investigation", () => {
    const { result, rerender } = renderHook(({ id }) => useInvestigationEvents(id), {
      initialProps: { id: "inv-1" },
    });
    act(() => {
      FakeEventSource.latest().emit(1, "status", { status: "running" });
    });
    rerender({ id: "inv-2" });
    expect(result.current.events).toEqual([]);
    expect(FakeEventSource.latest().url).toBe("/api/v1/investigations/inv-2/events");
  });
});
