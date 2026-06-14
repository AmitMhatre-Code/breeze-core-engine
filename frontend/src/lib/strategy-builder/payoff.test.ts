import { describe, expect, it } from "vitest";
import {
  portfolioPayoffAtExpiry,
  scanPayoffCurve,
  summarizePayoffExact,
  summarizePayoffScan,
} from "@/lib/strategy-builder/payoff";
import {
  isUnlimitedMaxLoss,
  isUnlimitedMaxProfit,
} from "@/lib/strategy-builder/trade-metrics";
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

    const exact = summarizePayoffExact(legs, lotSize, 100);
    expect(Number.isFinite(exact.maxProfit)).toBe(true);
    expect(Number.isFinite(exact.maxLoss)).toBe(true);
    expect(isUnlimitedMaxProfit(exact.maxProfit)).toBe(false);
    expect(isUnlimitedMaxLoss(exact.maxLoss)).toBe(false);
  });

  it("long call has unlimited max profit and finite max loss", () => {
    const legs: StrategyLeg[] = [
      {
        id: "lc",
        right: "Call",
        side: "Buy",
        strike: 100,
        lots: 1,
        premiumPerUnit: 5,
      },
    ];
    const lotSize = 75;
    const { maxProfit, maxLoss } = summarizePayoffExact(legs, lotSize, 100);
    expect(maxProfit).toBe(Infinity);
    expect(isUnlimitedMaxProfit(maxProfit)).toBe(true);
    expect(Number.isFinite(maxLoss)).toBe(true);
    expect(maxLoss).toBeLessThan(0);
    expect(maxLoss).toBeCloseTo(-5 * lotSize, 5);
  });

  it("short call has unlimited max loss and finite max profit", () => {
    const legs: StrategyLeg[] = [
      {
        id: "sc",
        right: "Call",
        side: "Sell",
        strike: 100,
        lots: 1,
        premiumPerUnit: 5,
      },
    ];
    const lotSize = 75;
    const { maxProfit, maxLoss } = summarizePayoffExact(legs, lotSize, 100);
    expect(maxLoss).toBe(-Infinity);
    expect(isUnlimitedMaxLoss(maxLoss)).toBe(true);
    expect(Number.isFinite(maxProfit)).toBe(true);
    expect(maxProfit).toBeCloseTo(5 * lotSize, 5);
  });

  it("flat zero payoff emits no breakevens (empty portfolio)", () => {
    const { xs, ys } = scanPayoffCurve(100, 120, 40, [], 1);
    const { breakevens, maxProfit, maxLoss } = summarizePayoffScan(xs, ys);
    expect(breakevens).toEqual([]);
    expect(maxProfit).toBe(0);
    expect(maxLoss).toBe(0);
  });

  it("string strikes still produce breakevens for long straddle summary", () => {
    const legs: StrategyLeg[] = [
      {
        id: "c",
        right: "Call",
        side: "Buy",
        strike: "25000" as unknown as number,
        lots: 1,
        premiumPerUnit: 45,
      },
      {
        id: "p",
        right: "Put",
        side: "Buy",
        strike: "25000" as unknown as number,
        lots: 1,
        premiumPerUnit: 53,
      },
    ];
    const { breakevens, maxLoss, maxProfit } = summarizePayoffExact(legs, 65, 25_000);
    expect(breakevens.length).toBeGreaterThanOrEqual(2);
    expect(maxProfit).toBe(Infinity);
    expect(isUnlimitedMaxProfit(maxProfit)).toBe(true);
    expect(maxLoss).toBeLessThan(0);
    expect(Number.isFinite(maxLoss)).toBe(true);
    expect(portfolioPayoffAtExpiry(25_000, legs, 65)).toBeCloseTo(maxLoss, -2);
  });
});
