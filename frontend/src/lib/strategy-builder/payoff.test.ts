import { describe, expect, it } from "vitest";
import { bsCallDelta, bsCallPrice, bsPutDelta, bsPutPrice, normCdf } from "@/lib/strategy-builder/blackScholes";
import {
  estimateProbabilityOfProfit,
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

describe("estimateProbabilityOfProfit (P of OTM expiry, deterministic)", () => {
  const spot = 24046;
  const sigma = 0.132;
  const T = 6 / 365;
  const lotSize = 75;

  /** Reference lognormal terminal-spot CDF, P(S_T < x), r=0.07 q=0 (mirrors the module). */
  const pBelow = (x: number): number => {
    const d2 = (Math.log(spot / x) + (0.07 - 0.5 * sigma * sigma) * T) / (sigma * Math.sqrt(T));
    return normCdf(-d2);
  };

  const shortCall = (strike: number): StrategyLeg[] => [
    { id: "sc", right: "Call", side: "Sell", strike, lots: 1, premiumPerUnit: 74.6 },
  ];

  it("is deterministic: identical inputs give byte-identical output across calls", () => {
    const legs = shortCall(24300);
    const first = estimateProbabilityOfProfit(spot, T, sigma, legs, lotSize);
    for (let i = 0; i < 25; i++) {
      expect(estimateProbabilityOfProfit(spot, T, sigma, legs, lotSize)).toBe(first);
    }
  });

  it("short call PoP = P(S_T < strike) — premium excluded, ICICI convention", () => {
    // Near-ATM 24300 strike: matches P(expire OTM) ~71%, NOT the premium-inclusive breakeven
    // (~77%) the old Monte Carlo produced.
    const pop = estimateProbabilityOfProfit(spot, T, sigma, shortCall(24300), lotSize);
    expect(pop / 100).toBeCloseTo(pBelow(24300), 4);
    expect(pop).toBeGreaterThan(70);
    expect(pop).toBeLessThan(72);
    // premium size must not move the answer — boundary is the strike, not strike+premium
    const richPremium: StrategyLeg[] = [
      { id: "sc", right: "Call", side: "Sell", strike: 24300, lots: 1, premiumPerUnit: 250 },
    ];
    expect(estimateProbabilityOfProfit(spot, T, sigma, richPremium, lotSize)).toBeCloseTo(pop, 6);
  });

  it("short put PoP = P(S_T > strike)", () => {
    const legs: StrategyLeg[] = [
      { id: "sp", right: "Put", side: "Sell", strike: 23800, lots: 1, premiumPerUnit: 60 },
    ];
    const pop = estimateProbabilityOfProfit(spot, T, sigma, legs, lotSize);
    expect(pop / 100).toBeCloseTo(1 - pBelow(23800), 4);
  });

  it("short strangle PoP = P(between short strikes) and is below each naked leg", () => {
    const kp = 23600;
    const kc = 24500;
    const legs: StrategyLeg[] = [
      { id: "sp", right: "Put", side: "Sell", strike: kp, lots: 1, premiumPerUnit: 40 },
      { id: "sc", right: "Call", side: "Sell", strike: kc, lots: 1, premiumPerUnit: 40 },
    ];
    const pop = estimateProbabilityOfProfit(spot, T, sigma, legs, lotSize);
    expect(pop / 100).toBeCloseTo(pBelow(kc) - pBelow(kp), 4);
    const legPut = estimateProbabilityOfProfit(spot, T, sigma, [legs[0]], lotSize);
    const legCall = estimateProbabilityOfProfit(spot, T, sigma, [legs[1]], lotSize);
    expect(pop).toBeLessThan(legPut);
    expect(pop).toBeLessThan(legCall);
  });

  it("long call (debit) falls back to P(profit past breakeven)", () => {
    const strike = 24000;
    const premium = 300;
    const legs: StrategyLeg[] = [
      { id: "lc", right: "Call", side: "Buy", strike, lots: 1, premiumPerUnit: premium },
    ];
    const pop = estimateProbabilityOfProfit(spot, T, sigma, legs, lotSize);
    // debit => premium IS included: profit needs S_T > strike + premium
    expect(pop / 100).toBeCloseTo(1 - pBelow(strike + premium), 4);
  });
});
