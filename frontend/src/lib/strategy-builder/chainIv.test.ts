import { describe, expect, it } from "vitest";
import {
  blendedSigmaForLegs,
  buildSigmaSmile,
  MAX_TRUSTED_REL_SPREAD,
  sigmaForLeg,
  sigmaForStrike,
  type SigmaSmile,
  type SigmaSmiles,
} from "@/lib/strategy-builder/chainIv";
import type { ChainSuccess, StrategyLeg } from "@/lib/strategy-builder/types";

const SPOT = 23623.0;

function row(
  strike: number,
  right: "call" | "put",
  opts: { bid: number; ask: number; buyQty?: number; sellQty?: number },
) {
  const leg = {
    ltp: (opts.bid + opts.ask) / 2,
    best_bid_price: opts.bid,
    best_offer_price: opts.ask,
    total_buy_qty: opts.buyQty ?? 100,
    total_sell_qty: opts.sellQty ?? 100,
  };
  return {
    strike_price: strike,
    call: right === "call" ? leg : null,
    put: right === "put" ? leg : null,
  };
}

function chain(rows: ReturnType<typeof row>[]): ChainSuccess {
  return {
    chain_rows: rows,
    spot_price: SPOT,
    atm_strike: 23600,
    expiry_display: "16-Jun-2026",
    stock_code: "NIFTY",
    exchange_code: "NFO",
  };
}

describe("buildSigmaSmile", () => {
  it("keeps only trust-gated quotes, sorted ascending by log-moneyness", () => {
    const c = chain([
      row(23000, "put", { bid: 9.5, ask: 9.6 }), // rel spread ~1.05% -> trusted
      row(22500, "put", { bid: 9.5, ask: 9.6 }), // trusted
      row(22000, "put", { bid: 0.4, ask: 0.6 }), // rel spread 40% -> excluded
      row(24000, "call", { bid: 9.5, ask: 9.6 }),
    ]);
    const put = buildSigmaSmile(c, 0.05, "put");
    expect(put.length).toBe(2);
    expect(put[0].x).toBeLessThan(put[1].x); // 22500 (lower strike) sorts first
    const call = buildSigmaSmile(c, 0.05, "call");
    expect(call.length).toBe(1);
  });

  it("excludes quotes with zero depth on either side", () => {
    const c = chain([row(23000, "put", { bid: 9.5, ask: 9.6, sellQty: 0 })]);
    expect(buildSigmaSmile(c, 0.05, "put")).toEqual([]);
  });

  it("spread exactly at the cap is trusted; just over is not", () => {
    // mid=10, spread=1.0 -> rel=0.10 == cap
    const atCap = chain([row(23000, "put", { bid: 9.5, ask: 10.5 })]);
    // mid=9.95, spread=1.1 -> rel > 0.10
    const overCap = chain([row(23000, "put", { bid: 9.4, ask: 10.5 })]);
    expect(MAX_TRUSTED_REL_SPREAD).toBe(0.1);
    // A single anchor never produces a smile point on its own (needs >=1 to build, but
    // sigmaForStrike needs >=2 to interpolate) — verify via buildSigmaSmile length instead.
    expect(buildSigmaSmile(atCap, 0.05, "put").length).toBe(1);
    expect(buildSigmaSmile(overCap, 0.05, "put").length).toBe(0);
  });
});

describe("sigmaForStrike", () => {
  const curve: SigmaSmile = [
    { x: Math.log(22500 / SPOT), iv: 0.25 },
    { x: Math.log(23000 / SPOT), iv: 0.2 },
  ];

  it("interpolates linearly in log-moneyness between two anchors", () => {
    const x = Math.log(22750 / SPOT);
    const t = (x - curve[0].x) / (curve[1].x - curve[0].x);
    const expected = curve[0].iv + t * (curve[1].iv - curve[0].iv);
    const got = sigmaForStrike(curve, 22750, SPOT, 0.15);
    expect(got).toBeCloseTo(expected, 8);
    expect(got).toBeGreaterThan(curve[1].iv);
    expect(got).toBeLessThan(curve[0].iv);
  });

  it("flat-clamps beyond the outermost anchor on either side", () => {
    expect(sigmaForStrike(curve, 20000, SPOT, 0.15)).toBe(curve[0].iv);
    expect(sigmaForStrike(curve, SPOT, SPOT, 0.15)).toBe(curve[1].iv);
  });

  it("falls back with fewer than two anchors", () => {
    expect(sigmaForStrike([], 22750, SPOT, 0.15)).toBe(0.15);
    expect(sigmaForStrike([curve[0]], 22750, SPOT, 0.15)).toBe(0.15);
  });
});

describe("blendedSigmaForLegs", () => {
  const smiles: SigmaSmiles = {
    call: [],
    put: [],
  };

  it("degenerates to sigmaForLeg for a single-leg list", () => {
    const leg: StrategyLeg = { id: "a", right: "Put", side: "Sell", strike: 22750, lots: 20, premiumPerUnit: 16.75 };
    const withCurve: SigmaSmiles = {
      call: [],
      put: [
        { x: Math.log(22500 / SPOT), iv: 0.25 },
        { x: Math.log(23000 / SPOT), iv: 0.2 },
      ],
    };
    const blended = blendedSigmaForLegs(withCurve, [leg], SPOT, 65, 0.15);
    const direct = sigmaForLeg(withCurve, leg, SPOT, 0.15);
    expect(blended).toBeCloseTo(direct, 10);
  });

  it("weights toward the leg with larger notional (quantity x premium)", () => {
    const smallLeg: StrategyLeg = { id: "small", right: "Put", side: "Sell", strike: 22500, lots: 1, premiumPerUnit: 1 };
    const bigLeg: StrategyLeg = { id: "big", right: "Call", side: "Sell", strike: 24000, lots: 10, premiumPerUnit: 50 };
    const withCurves: SigmaSmiles = {
      call: [
        { x: Math.log(23900 / SPOT), iv: 0.1 },
        { x: Math.log(24100 / SPOT), iv: 0.1 },
      ],
      put: [
        { x: Math.log(22400 / SPOT), iv: 0.4 },
        { x: Math.log(22600 / SPOT), iv: 0.4 },
      ],
    };
    const blended = blendedSigmaForLegs(withCurves, [smallLeg, bigLeg], SPOT, 65, 0.15);
    // bigLeg's notional (10*50=500) dwarfs smallLeg's (1*1=1), so blended sigma should sit
    // very close to bigLeg's own (call-side, 0.1) sigma, not smallLeg's (put-side, 0.4).
    expect(blended).toBeCloseTo(0.1, 2);
  });

  it("returns fallback for an empty leg list", () => {
    expect(blendedSigmaForLegs(smiles, [], SPOT, 65, 0.18)).toBe(0.18);
  });
});
