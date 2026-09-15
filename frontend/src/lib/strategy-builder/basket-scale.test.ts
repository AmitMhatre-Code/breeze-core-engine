import { describe, expect, it } from "vitest";
import {
  activeLotsGcd,
  computeNetDebit,
  computeScaleMultiplier,
  hasUnpricedActiveLeg,
  solveMarginScale,
  suggestScaleMode,
  type ScaleLeg,
} from "@/lib/strategy-builder/basket-scale";

const LOT = 50;

describe("computeNetDebit", () => {
  it("sums premium paid for a pure-buy basket as a positive debit", () => {
    const legs: ScaleLeg[] = [
      { lots: 1, side: "Buy", unitPrice: 100 },
      { lots: 2, side: "Buy", unitPrice: 40 },
    ];
    // 100*1*50 + 40*2*50 = 5000 + 4000
    expect(computeNetDebit(legs, LOT)).toBe(9000);
  });

  it("nets credit against debit for a mixed basket", () => {
    const legs: ScaleLeg[] = [
      { lots: 1, side: "Buy", unitPrice: 120 }, // +6000
      { lots: 1, side: "Sell", unitPrice: 30 }, // -1500
    ];
    expect(computeNetDebit(legs, LOT)).toBe(4500);
  });

  it("returns a negative number for a net-credit basket", () => {
    const legs: ScaleLeg[] = [
      { lots: 1, side: "Sell", unitPrice: 80 },
      { lots: 1, side: "Buy", unitPrice: 20 },
    ];
    // -4000 + 1000
    expect(computeNetDebit(legs, LOT)).toBe(-3000);
  });

  it("skips inactive and unpriced legs", () => {
    const legs: ScaleLeg[] = [
      { lots: 1, side: "Buy", unitPrice: 100 }, // +5000
      { lots: 0, side: "Buy", unitPrice: 999 }, // inactive
      { lots: 3, side: "Buy", unitPrice: undefined }, // unpriced
    ];
    expect(computeNetDebit(legs, LOT)).toBe(5000);
  });

  it("uses the resolved mid supplied for an aggressive leg", () => {
    // Caller resolves an aggressive leg's last-known mid into unitPrice.
    const legs: ScaleLeg[] = [{ lots: 2, side: "Buy", unitPrice: 55 }];
    expect(computeNetDebit(legs, LOT)).toBe(5500);
  });
});

describe("hasUnpricedActiveLeg", () => {
  it("flags an active leg with no price", () => {
    expect(
      hasUnpricedActiveLeg([{ lots: 1, side: "Buy", unitPrice: undefined }]),
    ).toBe(true);
  });

  it("ignores inactive unpriced legs", () => {
    expect(
      hasUnpricedActiveLeg([{ lots: 0, side: "Buy", unitPrice: undefined }]),
    ).toBe(false);
  });

  it("is false when all active legs are priced", () => {
    expect(
      hasUnpricedActiveLeg([{ lots: 1, side: "Buy", unitPrice: 10 }]),
    ).toBe(false);
  });
});

describe("suggestScaleMode", () => {
  it("suggests premium for a net-debit basket", () => {
    expect(suggestScaleMode(9000)).toBe("premium");
  });

  it("suggests margin for a net-credit or zero-debit basket", () => {
    expect(suggestScaleMode(-3000)).toBe("margin");
    expect(suggestScaleMode(0)).toBe("margin");
  });
});

describe("activeLotsGcd", () => {
  it("returns the common lot count for an equal-ratio basket", () => {
    expect(activeLotsGcd([{ lots: 4 }, { lots: 4 }])).toBe(4);
  });

  it("reduces a ratio spread to its irreducible unit", () => {
    // 6:3 → unit is 2:1 (gcd 3)
    expect(activeLotsGcd([{ lots: 6 }, { lots: 3 }])).toBe(3);
  });

  it("is 1 for an already-irreducible ratio", () => {
    expect(activeLotsGcd([{ lots: 2 }, { lots: 1 }])).toBe(1);
  });

  it("ignores inactive legs", () => {
    expect(activeLotsGcd([{ lots: 0 }, { lots: 5 }])).toBe(5);
  });

  it("is 0 when no leg is active", () => {
    expect(activeLotsGcd([{ lots: 0 }, { lots: 0 }])).toBe(0);
  });
});

describe("computeScaleMultiplier", () => {
  it("returns the largest integer k with k*base <= target", () => {
    // base 20k, target 105k → floor(5.25) = 5
    expect(computeScaleMultiplier(20000, 105000)).toEqual({ ok: true, k: 5 });
  });

  it("returns k exactly when the target is a clean multiple", () => {
    expect(computeScaleMultiplier(20000, 60000)).toEqual({ ok: true, k: 3 });
  });

  it("underflows when a single base basket exceeds the target", () => {
    expect(computeScaleMultiplier(50000, 30000)).toEqual({
      ok: false,
      reason: "underflow",
    });
  });

  it("rejects an absent resource (base <= 0)", () => {
    expect(computeScaleMultiplier(0, 100000)).toEqual({
      ok: false,
      reason: "invalid-base",
    });
    expect(computeScaleMultiplier(-3000, 100000)).toEqual({
      ok: false,
      reason: "invalid-base",
    });
  });

  it("rejects a missing or non-positive target", () => {
    expect(computeScaleMultiplier(20000, 0)).toEqual({
      ok: false,
      reason: "invalid-target",
    });
    expect(computeScaleMultiplier(20000, NaN)).toEqual({
      ok: false,
      reason: "invalid-target",
    });
  });
});

describe("solveMarginScale", () => {
  /** Margin curve as a probe that records every size it was asked for. */
  function probe(curve: (units: number) => number) {
    const calls: number[] = [];
    const measure = async (units: number) => {
      calls.push(units);
      return curve(units);
    };
    return { calls, measure };
  }

  it("lands on the linear size in one probe when margin is linear", async () => {
    const { calls, measure } = probe((u) => 10_000 * u);
    const res = await solveMarginScale({
      currentUnits: 2,
      currentMargin: 20_000,
      target: 95_000,
      measure,
    });
    expect(res).toEqual({ ok: true, k: 9, margin: 90_000, linearOvershoot: null });
    expect(calls).toEqual([9]);
  });

  it("scales an oversized basket down", async () => {
    const { calls, measure } = probe((u) => 10_000 * u);
    const res = await solveMarginScale({
      currentUnits: 10,
      currentMargin: 100_000,
      target: 35_000,
      measure,
    });
    expect(res).toMatchObject({ ok: true, k: 3, margin: 30_000 });
    expect(calls).toEqual([3]);
  });

  it("never returns a size over target when margin grows faster than lots", async () => {
    // Hedge credit fading with size: the straight-line size (137) is ~3x over.
    const { calls, measure } = probe((u) => 20_000 * u + 350 * u * u);
    const res = await solveMarginScale({
      currentUnits: 1,
      currentMargin: 20_350,
      target: 2_800_000,
      measure,
    });
    expect(res).toEqual({
      ok: true,
      k: 57,
      margin: 2_277_150,
      linearOvershoot: 9_309_150,
    });
    expect(calls).toEqual([137, 41, 57]);
  });

  it("keeps the last measured fit once the probe budget runs out", async () => {
    const { calls, measure } = probe((u) => (u === 1 ? 1_000 : 100_001));
    const res = await solveMarginScale({
      currentUnits: 1,
      currentMargin: 1_000,
      target: 100_000,
      measure,
    });
    expect(res).toMatchObject({ ok: true, k: 1, margin: 1_000 });
    expect(calls).toHaveLength(3);
  });

  it("reports underflow without probing when one unit is already over target", async () => {
    const { calls, measure } = probe(() => 50_000);
    const res = await solveMarginScale({
      currentUnits: 1,
      currentMargin: 50_000,
      target: 30_000,
      measure,
    });
    expect(res).toEqual({
      ok: false,
      reason: "underflow",
      smallestOver: { units: 1, margin: 50_000 },
    });
    expect(calls).toEqual([]);
  });

  it("rejects a zero base margin and a non-positive target", async () => {
    const { calls, measure } = probe(() => 1);
    await expect(
      solveMarginScale({ currentUnits: 1, currentMargin: 0, target: 10, measure }),
    ).resolves.toEqual({ ok: false, reason: "invalid-base" });
    await expect(
      solveMarginScale({ currentUnits: 1, currentMargin: 10, target: 0, measure }),
    ).resolves.toEqual({ ok: false, reason: "invalid-target" });
    expect(calls).toEqual([]);
  });

  it("throws when a probe returns no usable figure", async () => {
    const { measure } = probe(() => Number.NaN);
    await expect(
      solveMarginScale({ currentUnits: 1, currentMargin: 1_000, target: 10_000, measure }),
    ).rejects.toThrow("ICICI did not return a margin figure");
  });
});
