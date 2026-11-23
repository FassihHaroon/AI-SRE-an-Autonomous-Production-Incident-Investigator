import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach, vi } from "vitest";

import { FakeEventSource } from "./eventsource";

// jsdom lacks ResizeObserver (used by Recharts' ResponsiveContainer) and EventSource.
class NoopResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

beforeEach(() => {
  FakeEventSource.instances = [];
  vi.stubGlobal("ResizeObserver", NoopResizeObserver);
  vi.stubGlobal("EventSource", FakeEventSource);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
