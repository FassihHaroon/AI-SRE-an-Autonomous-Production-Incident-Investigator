import { stepStates } from "@/features/investigations/progress";

import { formatValue, relativeTime } from "./format";
import { qs } from "./api";
import { bandTone, healthTone, investigationTone } from "./status";

describe("formatValue", () => {
  it.each([
    [0.2134, "ratio", "21.3%"],
    [0.0012, "ratio", "0.12%"],
    [0.207, "s", "207 ms"],
    [2.4, "s", "2.40 s"],
    [74_133_504, "bytes", "71 MiB"],
    [2.91, "req/s", "2.91/s"],
    [null, "s", "—"],
  ] as const)("formats %s %s as %s", (value, unit, expected) => {
    expect(formatValue(value, unit)).toBe(expected);
  });
});

describe("relativeTime", () => {
  it("describes elapsed time", () => {
    const now = new Date("2026-11-23T10:00:00Z");
    expect(relativeTime("2026-11-23T09:59:50Z", now)).toBe("just now");
    expect(relativeTime("2026-11-23T09:45:00Z", now)).toBe("15 min ago");
    expect(relativeTime("2026-11-23T07:00:00Z", now)).toBe("3 h ago");
    expect(relativeTime(null, now)).toBe("—");
  });
});

describe("qs", () => {
  it("repeats array params and drops empty values", () => {
    expect(qs({ services: ["a", "b"], signal: "cpu", q: "", x: undefined })).toBe(
      "?services=a&services=b&signal=cpu",
    );
    expect(qs({})).toBe("");
  });
});

describe("status tones", () => {
  it("maps backend vocabularies", () => {
    expect(healthTone("degraded")).toBe("warn");
    expect(investigationTone("awaiting_approval")).toBe("warn");
    expect(bandTone("insufficient")).toBe("danger");
  });
});

describe("stepStates", () => {
  const ev = (type: string, node: string) => ({ id: 1, type, data: { node }, at: "" });

  it("tracks started and completed graph nodes", () => {
    const s = stepStates(
      [
        ev("node_started", "intake"),
        ev("node_completed", "intake"),
        ev("node_started", "planning"),
      ],
      "running",
    );
    expect(s).toEqual({ intake: "done", planning: "active" });
  });

  it("marks the approval step while waiting and failed steps on failure", () => {
    expect(stepStates([], "awaiting_approval").approval).toBe("active");
    expect(stepStates([ev("node_started", "tool_execution")], "failed").tool_execution).toBe(
      "failed",
    );
  });
});
