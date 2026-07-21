import { describe, expect, it } from "vitest";
import {
  financialYearWeekKeys,
  formatWeekLabel,
  padWeeklyToFinancialYear,
  parseWeeklyPerformance,
} from "./performance-data";

const FY_START = "2026-04-01";
const FY_END = "2027-03-31";

describe("financialYearWeekKeys", () => {
  it("clips boundary weeks to the FY", () => {
    const keys = financialYearWeekKeys(FY_START, FY_END);
    // 1-Apr-2026 is a Wednesday, 31-Mar-2027 is a Wednesday.
    expect(keys[0]).toBe("2026-03-30");
    expect(keys.at(-1)).toBe("2027-03-29");
  });

  it("emits consecutive Mondays covering the year", () => {
    const keys = financialYearWeekKeys(FY_START, FY_END);
    expect(keys.length).toBe(53);
    for (const key of keys) {
      expect(new Date(`${key}T00:00:00Z`).getUTCDay()).toBe(1);
    }
  });

  it("treats a Sunday as belonging to the week that already started", () => {
    // 2026-04-05 is a Sunday → week of Mon 2026-03-30.
    expect(financialYearWeekKeys("2026-04-05", "2026-04-05")).toEqual([
      "2026-03-30",
    ]);
  });

  it("returns nothing for a malformed range", () => {
    expect(financialYearWeekKeys("not-a-date", FY_END)).toEqual([]);
    expect(financialYearWeekKeys(FY_END, FY_START)).toEqual([]);
  });
});

describe("formatWeekLabel", () => {
  it("renders the Monday as DD MMM", () => {
    expect(formatWeekLabel("2026-07-20")).toBe("20 Jul");
    expect(formatWeekLabel("2026-01-05")).toBe("05 Jan");
  });
});

describe("parseWeeklyPerformance", () => {
  const root = {
    Status: 200,
    Success: {
      weekly: [
        { week: "2026-07-20", pnl: -104.78, brokerage: 76, taxes: 13.78 },
        { week: "", pnl: 1, brokerage: 1, taxes: 1 },
        { week: "2026-08-03", pnl: "not-a-number", brokerage: 19, taxes: 3.4 },
      ],
    },
  };

  it("keeps well-formed rows, drops keyless ones, zeroes bad numbers", () => {
    expect(parseWeeklyPerformance(root)).toEqual([
      { week: "2026-07-20", pnl: -104.78, brokerage: 76, taxes: 13.78 },
      { week: "2026-08-03", pnl: 0, brokerage: 19, taxes: 3.4 },
    ]);
  });

  it("returns [] when the broker block failed or has no weekly array", () => {
    expect(parseWeeklyPerformance({ Status: 500, Success: null })).toEqual([]);
    expect(parseWeeklyPerformance({ Status: 200, Success: {} })).toEqual([]);
  });
});

describe("padWeeklyToFinancialYear", () => {
  it("fills untraded weeks with nulls and labels every row", () => {
    const rows = padWeeklyToFinancialYear(
      [{ week: "2026-07-20", pnl: -104.78, brokerage: 76, taxes: 13.78 }],
      FY_START,
      FY_END,
    );
    expect(rows.length).toBe(53);
    expect(rows[0]).toEqual({
      label: "30 Mar",
      pnl: null,
      brokerage: null,
      taxes: null,
    });
    expect(rows.find((r) => r.label === "20 Jul")).toEqual({
      label: "20 Jul",
      pnl: -104.78,
      brokerage: 76,
      taxes: 13.78,
    });
  });

  it("falls back to the raw rows when the FY range is unknown", () => {
    expect(
      padWeeklyToFinancialYear(
        [{ week: "2026-07-20", pnl: 1, brokerage: 2, taxes: 3 }],
        undefined,
        undefined,
      ),
    ).toEqual([{ label: "20 Jul", pnl: 1, brokerage: 2, taxes: 3 }]);
  });

  it("produces unique labels across a full FY (safe as a React key)", () => {
    const rows = padWeeklyToFinancialYear([], FY_START, FY_END);
    expect(new Set(rows.map((r) => r.label)).size).toBe(rows.length);
  });
});
