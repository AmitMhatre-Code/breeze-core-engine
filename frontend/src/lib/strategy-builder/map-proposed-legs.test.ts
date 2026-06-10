import { describe, expect, it } from "vitest";
import { proposedLegsToStrategyLegs } from "@/lib/strategy-builder/map-proposed-legs";

describe("proposedLegsToStrategyLegs", () => {
  it("maps quantity to lots", () => {
    const legs = proposedLegsToStrategyLegs(
      [
        {
          right: "Call",
          side: "Buy",
          strike: 23500,
          quantity: 150,
          premium_per_unit: 120,
        },
      ],
      75,
    );
    expect(legs).toHaveLength(1);
    expect(legs[0]?.lots).toBe(2);
    expect(legs[0]?.premiumPerUnit).toBe(120);
  });
});
