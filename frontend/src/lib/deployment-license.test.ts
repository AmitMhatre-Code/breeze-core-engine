import { describe, expect, it } from "vitest";
import {
  isTradingReadOnly,
  licenseBannerMessage,
  shouldShowLicenseBanner,
} from "@/lib/deployment-license";

describe("deployment-license", () => {
  it("shows banner only for expired and revoked", () => {
    expect(shouldShowLicenseBanner("active")).toBe(false);
    expect(shouldShowLicenseBanner(undefined)).toBe(false);
    expect(shouldShowLicenseBanner("expired")).toBe(true);
    expect(shouldShowLicenseBanner("revoked")).toBe(true);
  });

  it("returns banner copy for expired and revoked", () => {
    expect(licenseBannerMessage("expired")).toContain("License expired");
    expect(licenseBannerMessage("revoked")).toContain("Read-only mode");
    expect(licenseBannerMessage("active")).toBeNull();
  });

  it("treats only revoked as trading read-only", () => {
    expect(isTradingReadOnly("revoked")).toBe(true);
    expect(isTradingReadOnly("expired")).toBe(false);
    expect(isTradingReadOnly("active")).toBe(false);
  });
});
