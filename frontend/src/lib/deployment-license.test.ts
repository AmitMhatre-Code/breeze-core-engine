import { describe, expect, it } from "vitest";
import {
  isTradingReadOnly,
  licenseBannerMessage,
  shouldShowLicenseBanner,
} from "@/lib/deployment-license";

describe("deployment-license", () => {
  it("shows banner for expired, revoked, and unlicensed", () => {
    expect(shouldShowLicenseBanner("active")).toBe(false);
    expect(shouldShowLicenseBanner(undefined)).toBe(false);
    expect(shouldShowLicenseBanner("expired")).toBe(true);
    expect(shouldShowLicenseBanner("revoked")).toBe(true);
    expect(shouldShowLicenseBanner("unlicensed")).toBe(true);
  });

  it("returns banner copy for expired, revoked, and unlicensed", () => {
    expect(licenseBannerMessage("expired")).toContain("License expired");
    expect(licenseBannerMessage("revoked")).toContain("Read-only mode");
    expect(licenseBannerMessage("unlicensed")).toContain("no valid license");
    expect(licenseBannerMessage("active")).toBeNull();
  });

  it("treats revoked and unlicensed as trading read-only", () => {
    expect(isTradingReadOnly("revoked")).toBe(true);
    expect(isTradingReadOnly("unlicensed")).toBe(true);
    expect(isTradingReadOnly("expired")).toBe(false);
    expect(isTradingReadOnly("active")).toBe(false);
  });
});
