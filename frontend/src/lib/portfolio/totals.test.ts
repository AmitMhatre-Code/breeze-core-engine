import { describe, expect, it } from "vitest";
import { computePortfolioTotals } from "@/lib/portfolio/totals";
import type { GroupLiveTotal } from "@/lib/portfolio/liveGroupTotals";
import type { PortfolioPositionRecord } from "@/lib/portfolio";
import type { PortfolioPositionGroup } from "@/lib/portfolio/groupPositions";

function row(current_profit: unknown, carry_profit: unknown): PortfolioPositionRecord {
  return { current_profit, carry_profit };
}

function group(
  key: string,
  rows: PortfolioPositionRecord[],
): PortfolioPositionGroup {
  return {
    key,
    stockCode: "NIFTY",
    exchangeCode: "NFO",
    expiryDate: "01-Sep-2026",
    rows,
    netted: null,
  };
}

const PORTFOLIO = {
  span_margin_required: 1000,
  elm_margin_required: 200,
  carry_margin_returns: 5.1,
};

describe("computePortfolioTotals — snapshot path", () => {
  it("sums current_profit / carry_profit across every leg of every group", () => {
    const groups = [
      group("a", [row(100, -50), row(-30, 20)]),
      group("b", [row(10, 5)]),
    ];
    const totals = computePortfolioTotals(groups, PORTFOLIO);
    expect(totals.totalMtm).toBe(80);
    expect(totals.totalCarry).toBe(-25);
    expect(totals.legCount).toBe(3);
    expect(totals.groupCount).toBe(2);
    expect(totals.totalMargin).toBe(1200);
    expect(totals.spanMargin).toBe(1000);
    expect(totals.elmMargin).toBe(200);
    expect(totals.carryReturnPct).toBe(5.1);
  });

  it("returns null MTM/Carry when no leg carries a usable figure", () => {
    const totals = computePortfolioTotals(
      [group("a", [row("*", ""), row(null, undefined)])],
      null,
    );
    expect(totals.totalMtm).toBeNull();
    expect(totals.totalCarry).toBeNull();
    expect(totals.legCount).toBe(2);
  });
});

describe("computePortfolioTotals — live overlay path", () => {
  const groups = [
    group("a", [row(100, -50), row(-30, 20)]), // snapshot sums: mtm 70, carry -30
    group("b", [row(10, 5)]), // snapshot sums: mtm 10, carry 5
  ];

  it("uses a group's live figure when present", () => {
    const live = new Map<string, GroupLiveTotal>([
      ["a", { mtm: 250, carry: -111 }],
      ["b", { mtm: 12, carry: 6 }],
    ]);
    const totals = computePortfolioTotals(groups, PORTFOLIO, live);
    expect(totals.totalMtm).toBe(262);
    expect(totals.totalCarry).toBe(-105);
  });

  it("falls back to the snapshot for groups with no live entry", () => {
    const live = new Map<string, GroupLiveTotal>([
      ["a", { mtm: 250, carry: -111 }],
    ]);
    const totals = computePortfolioTotals(groups, PORTFOLIO, live);
    // group a live (250 / -111) + group b snapshot (10 / 5)
    expect(totals.totalMtm).toBe(260);
    expect(totals.totalCarry).toBe(-106);
  });

  it("falls back per-field when a live figure is null", () => {
    const live = new Map<string, GroupLiveTotal>([
      ["a", { mtm: 250, carry: null }],
      ["b", { mtm: null, carry: 6 }],
    ]);
    const totals = computePortfolioTotals(groups, PORTFOLIO, live);
    // mtm: a live 250 + b snapshot 10 ; carry: a snapshot -30 + b live 6
    expect(totals.totalMtm).toBe(260);
    expect(totals.totalCarry).toBe(-24);
  });

  it("is identical to the snapshot path for an empty live map", () => {
    const bare = computePortfolioTotals(groups, PORTFOLIO);
    const withEmpty = computePortfolioTotals(
      groups,
      PORTFOLIO,
      new Map<string, GroupLiveTotal>(),
    );
    expect(withEmpty).toEqual(bare);
  });

  it("counts a live figure toward the not-null result even when snapshot rows are empty", () => {
    const emptyGroups = [group("a", [row(null, null)])];
    const live = new Map<string, GroupLiveTotal>([
      ["a", { mtm: 42, carry: -7 }],
    ]);
    const totals = computePortfolioTotals(emptyGroups, null, live);
    expect(totals.totalMtm).toBe(42);
    expect(totals.totalCarry).toBe(-7);
  });
});
