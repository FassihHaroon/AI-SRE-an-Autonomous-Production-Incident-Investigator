import { vi } from "vitest";

import { jsonResponse } from "@/test/render";

import { ApiError, apiGet } from "./api";

describe("apiGet", () => {
  it("returns parsed JSON on success", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse({ ok: true }));
    await expect(apiGet<{ ok: boolean }>("/api/v1/x")).resolves.toEqual({ ok: true });
  });

  it("throws ApiError carrying RFC 9457 problem details", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({ title: "Not Found", status: 404 }, 404, "application/problem+json"),
    );
    const error = await apiGet("/api/v1/missing").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(404);
    expect((error as ApiError).message).toBe("Not Found");
  });

  it("handles non-JSON error bodies", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("<html>bad gateway</html>", {
        status: 502,
        headers: { "content-type": "text/html" },
      }),
    );
    const error = (await apiGet("/api/v1/x").catch((e: unknown) => e)) as ApiError;
    expect(error.status).toBe(502);
    expect(error.problem).toBeUndefined();
  });
});
