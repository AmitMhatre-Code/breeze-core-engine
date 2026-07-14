import { describe, expect, it } from "vitest";
import { bsCallDelta, bsCallPrice, bsPutDelta, bsPutPrice } from "@/lib/strategy-builder/blackScholes";
import {
  portfolioGreeks,
  portfolioMarkToModel,
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

  it("portfolioMarkToModel and portfolioGreeks resolve each leg's own sigma via a per-leg function", () => {
    const callLeg: StrategyLeg = { id: "c", right: "Call", side: "Buy", strike: 100, lots: 1, premiumPerUnit: 5 };
    const putLeg: StrategyLeg = { id: "p", right: "Put", side: "Buy", strike: 90, lots: 1, premiumPerUnit: 4 };
    const legs = [callLeg, putLeg];
    const S = 95;
    const T = 0.1;
    const lotSize = 1;
    const sigmaByLeg = (leg: StrategyLeg) => (leg.id === "c" ? 0.15 : 0.35);

    const modelViaResolver = portfolioMarkToModel(S, legs, lotSize, T, sigmaByLeg);
    const expectedModel =
      (bsCallPrice(S, callLeg.strike, T, 0.15) - callLeg.premiumPerUnit!) +
      (bsPutPrice(S, putLeg.strike, T, 0.35) - putLeg.premiumPerUnit!);
    expect(modelViaResolver).toBeCloseTo(expectedModel, 6);

    // A flat scalar sigma must not equal the per-leg-resolved result here (sanity check that
    // the resolver is actually taking effect, not silently ignored).
    const modelViaFlatSigma = portfolioMarkToModel(S, legs, lotSize, T, 0.15);
    expect(modelViaResolver).not.toBeCloseTo(modelViaFlatSigma, 6);

    const greeksViaResolver = portfolioGreeks(S, legs, lotSize, T, sigmaByLeg);
    const expectedDelta =
      bsCallDelta(S, callLeg.strike, T, 0.15) + bsPutDelta(S, putLeg.strike, T, 0.35);
    expect(greeksViaResolver.delta).toBeCloseTo(expectedDelta, 6);
  });
});
