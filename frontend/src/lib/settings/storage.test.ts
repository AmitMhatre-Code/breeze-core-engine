import { describe, expect, it } from "vitest";

import { describeCoverage, formatBytes, formatDay } from "./storage";

describe("formatBytes", () => {
  it("scales through GB", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(16 * 1024 ** 3)).toBe("16 GB");
    expect(formatBytes(null)).toBe("—");
  });
});

describe("formatDay", () => {
  it("reads the ISO day without shifting it across a timezone", () => {
    expect(formatDay("2026-09-01")).toBe("1 Sep 2026");
    expect(formatDay("2026-09-25T18:00:00+05:30")).toBe("25 Sep 2026");
    expect(formatDay(null)).toBe("—");
  });
});

describe("describeCoverage", () => {
  it("joins the range, days and count", () => {
    expect(
      describeCoverage({ from: "2026-08-01", to: "2026-09-24", days: 38, count: 1200, count_unit: "contracts", bytes: 9 }),
    ).toBe("1 Aug 2026 – 24 Sep 2026 · 38 days · 1,200 contracts");
  });

  it("collapses a single day and singular counts", () => {
    expect(
      describeCoverage({ from: "2026-09-01", to: "2026-09-01", days: 1, count: 1, count_unit: "runs", bytes: 9 }),
    ).toBe("1 Sep 2026 · 1 day · 1 run");
  });

  it("says Empty only when there are no bytes", () => {
    const none = { from: null, to: null, days: null, count: null, count_unit: null };
    expect(describeCoverage({ ...none, bytes: 0 })).toBe("Empty");
    expect(describeCoverage({ ...none, bytes: 25_000_000 })).toBe("");
  });
});
