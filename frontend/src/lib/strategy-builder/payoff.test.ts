import { describe, expect, it } from "vitest";
import {
  portfolioPayoffAtExpiry,
  scanPayoffCurve,
  summarizePayoffScan,
} from "@/lib/strategy-builder/payoff";
import type { StrategyLeg } from "@/lib/strategy-builder/types";

describe("payoff", () => {
  it("bull call spread has capped max loss at net debit", () => {
    const legs: StrategyLeg[] = [
      {
        id: "a",
        right: "Call",
        side: "Buy",
        strike: 100,
        lots: 1,
        premiumPerUnit: 5,
      },
      {
        id: "b",
        right: "Call",
        side: "Sell",
        strike: 110,
        lots: 1,
        premiumPerUnit: 2,
      },
    ];
    const lotSize = 1;
    const netDebit = 5 - 2;
    const low = portfolioPayoffAtExpiry(50, legs, lotSize);
    expect(low).toBeCloseTo(-netDebit, 5);
    const { xs, ys } = scanPayoffCurve(80, 130, 60, legs, lotSize);
    const { maxLoss, maxProfit } = summarizePayoffScan(xs, ys);
    expect(maxLoss).toBeLessThanOrEqual(-netDebit + 1e-6);
    expect(maxProfit).toBeGreaterThan(0);
  });

  it("flat zero payoff emits no breakevens (empty portfolio)", () => {
    const { xs, ys } = scanPayoffCurve(100, 120, 40, [], 1);
    const { breakevens, maxProfit, maxLoss } = summarizePayoffScan(xs, ys);
    expect(breakevens).toEqual([]);
    expect(maxProfit).toBe(0);
    expect(maxLoss).toBe(0);
  });
});
