import { describe, expect, it } from "vitest";
import {
  computeNetDebit,
  computeScaleMultiplier,
  hasUnpricedActiveLeg,
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
