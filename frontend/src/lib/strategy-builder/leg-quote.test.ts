import { describe, expect, it } from "vitest";
import {
  isUnpricedLeg,
  netPremiumOfPricedLegs,
  pricedLegs,
  unpricedNote,
} from "@/lib/strategy-builder/leg-quote";
import type { StrategyLeg } from "@/lib/strategy-builder/types";

const leg = (over: Partial<StrategyLeg>): StrategyLeg =>
  ({ id: "x", strike: 72000, right: "Put", side: "Sell", lots: 1, premiumPerUnit: 100, ...over }) as StrategyLeg;

describe("unpriced legs (B-57)", () => {
  const short = leg({ id: "s", strike: 72000, side: "Sell", premiumPerUnit: 120 });
  const noQuote = leg({ id: "l", strike: 71900, side: "Buy", premiumPerUnit: undefined });

  it("a sized leg with no price is unpriced; an empty leg is not", () => {
    expect(isUnpricedLeg(noQuote)).toBe(true);
    expect(isUnpricedLeg(leg({ premiumPerUnit: 0 }))).toBe(true);
    expect(isUnpricedLeg(leg({ lots: 0, premiumPerUnit: undefined }))).toBe(false);
    expect(isUnpricedLeg(short)).toBe(false);
  });

  it("leaves the unpriced leg out of the net premium instead of counting it at 0", () => {
    expect(pricedLegs([short, noQuote]).map((l) => l.id)).toEqual(["s"]);
    expect(netPremiumOfPricedLegs([short, noQuote], 20)).toBe(2400);
    expect(unpricedNote([short, noQuote])).toBe("1 leg has no quote and is left out of these figures.");
    expect(unpricedNote([short])).toBeNull();
  });
});
