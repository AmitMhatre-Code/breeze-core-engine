import { describe, expect, it } from "vitest";
import {
  compareAppVersions,
  formatAppVersionForDisplay,
  formatAppVersionLabel,
  isAppVersionBehind,
  normalizeAppVersionForCompare,
  parseAppVersion,
} from "@/lib/app-version";

describe("formatAppVersionForDisplay", () => {
  it("keeps standard semver pre-release hyphen form", () => {
    expect(formatAppVersionForDisplay("1.4.2-a")).toBe("1.4.2-a");
    expect(formatAppVersionForDisplay("1.4.2")).toBe("1.4.2");
  });

  it("converts legacy dot suffix to hyphen form", () => {
    expect(formatAppVersionForDisplay("1.4.2.a")).toBe("1.4.2-a");
  });
});

describe("formatAppVersionLabel", () => {
  it("prefixes v for display labels", () => {
    expect(formatAppVersionLabel("1.4.2-a")).toBe("v1.4.2-a");
    expect(formatAppVersionLabel("")).toBe("");
  });
});

describe("normalizeAppVersionForCompare", () => {
  it("normalizes dot suffix to hyphen form", () => {
    expect(normalizeAppVersionForCompare("1.4.2.a")).toBe("1.4.2-a");
    expect(normalizeAppVersionForCompare("1.4.2-a")).toBe("1.4.2-a");
  });

  it("extracts docker image tags", () => {
    expect(normalizeAppVersionForCompare("ghcr.io/org/app:1.4.2-a")).toBe(
      "1.4.2-a",
    );
  });
});

describe("parseAppVersion", () => {
  it("parses semver components", () => {
    expect(parseAppVersion("1.4.2-a")).toEqual({
      major: 1,
      minor: 4,
      patch: 2,
      prerelease: "a",
    });
    expect(parseAppVersion("1.4.2")).toEqual({
      major: 1,
      minor: 4,
      patch: 2,
    });
  });
});

describe("compareAppVersions", () => {
  it("orders versions like the portal backend", () => {
    expect(compareAppVersions("1.4.1", "1.4.2")).toBeLessThan(0);
    expect(compareAppVersions("1.4.2", "1.4.2")).toBe(0);
    expect(compareAppVersions("1.4.2-a", "1.4.2")).toBeGreaterThan(0);
    expect(compareAppVersions("1.4.2.a", "1.4.2")).toBeGreaterThan(0);
    expect(compareAppVersions("unknown", "1.4.2")).toBeLessThan(0);
    expect(isAppVersionBehind("unknown", "1.4.2")).toBe(true);
  });
});
