import { describe, expect, it } from "vitest";
import {
  isPublicUnauthenticatedPath,
  shouldFetchLicenseHomeData,
} from "@/lib/public-auth-routes";

describe("public-auth-routes", () => {
  it("skips license home/data on auth and register flows", () => {
    for (const path of [
      "/login",
      "/challenge",
      "/logout",
      "/register",
      "/register/correct",
      "/register/forgot-password",
      "/",
    ]) {
      expect(isPublicUnauthenticatedPath(path)).toBe(true);
      expect(shouldFetchLicenseHomeData(path)).toBe(false);
    }
  });

  it("fetches license home/data on authenticated app routes", () => {
    expect(shouldFetchLicenseHomeData("/dashboard")).toBe(true);
    expect(shouldFetchLicenseHomeData("/orders")).toBe(true);
    expect(isPublicUnauthenticatedPath("/dashboard")).toBe(false);
  });
});
