import { afterEach, describe, expect, it, vi } from "vitest";
import { apiClient, ApiHttpError } from "@/lib/api-client";
import * as authSession from "@/lib/auth-session-expired";

vi.mock("@/lib/config", () => ({
  getBackendBaseUrl: () => "http://localhost:3000",
}));

function mock401Fetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: false,
      status: 401,
      headers: { get: (name: string) => (name === "content-type" ? "application/json" : null) },
      json: async () => ({ detail: "Invalid or missing authentication token" }),
    }),
  );
}

describe("apiClient sessionPolicy", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("passive 401 does not trigger session expired redirect", async () => {
    mock401Fetch();
    const handleSpy = vi
      .spyOn(authSession, "handleUnauthorizedApiResponse")
      .mockResolvedValue(true);

    await expect(
      apiClient.get("/deployment/license-status", { sessionPolicy: "passive" }),
    ).rejects.toBeInstanceOf(ApiHttpError);

    expect(handleSpy).not.toHaveBeenCalled();
  });

  it("default 401 invokes handleUnauthorizedApiResponse", async () => {
    mock401Fetch();
    const handleSpy = vi
      .spyOn(authSession, "handleUnauthorizedApiResponse")
      .mockResolvedValue(true);

    await expect(apiClient.get("/home/data")).rejects.toBeInstanceOf(ApiHttpError);

    expect(handleSpy).toHaveBeenCalledWith(
      "/home/data",
      401,
      "Invalid or missing authentication token",
    );
  });
});

describe("apiClient error messages", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("names the field FastAPI refused instead of blaming the backend", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 422,
        headers: { get: (name: string) => (name === "content-type" ? "application/json" : null) },
        json: async () => ({
          detail: [{ type: "literal_error", loc: ["body", "bot"], msg: "Input should be 'momentum' or 'fly'" }],
        }),
      }),
    );

    await expect(apiClient.post("/bots/backtest/start", { bot: "cas" })).rejects.toThrow(
      "Invalid request (bot): Input should be 'momentum' or 'fly'",
    );
  });
});
