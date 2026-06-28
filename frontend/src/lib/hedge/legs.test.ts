import { describe, expect, it } from "vitest";
import type { StrategyHedgeCandidate } from "@/lib/hedge/api";
import {
  candidateToExecutionLeg,
  candidateToStrategyLeg,
  hedgeCandidateKey,
} from "@/lib/hedge/legs";

const sample: StrategyHedgeCandidate = {
  strike_price: 24700,
  right: "Call",
  ltp: 55,
  net_premium_cost: 2750,
  estimated_margin_relief: 120000,
  max_loss_estimate: 85000,
  score: 1.2,
  action: "Buy",
  hedge_quantity: 50,
  short_strike: 24500,
  hedge_type: "bear_call_spread_wing",
};

describe("candidateToStrategyLeg", () => {
  it("maps buy wing to strategy leg with lots from hedge quantity", () => {
    const leg = candidateToStrategyLeg(sample, 50);
    expect(leg.side).toBe("Buy");
    expect(leg.right).toBe("Call");
    expect(leg.strike).toBe(24700);
    expect(leg.lots).toBe(1);
    expect(leg.premiumPerUnit).toBe(55);
  });
});

describe("candidateToExecutionLeg", () => {
  it("maps to execution preview leg in contract units", () => {
    const leg = candidateToExecutionLeg(sample);
    expect(leg.side).toBe("Buy");
    expect(leg.quantity).toBe(50);
    expect(leg.premiumPerUnit).toBe(55);
  });
});

describe("hedgeCandidateKey", () => {
  it("is stable for the same candidate", () => {
    expect(hedgeCandidateKey(sample)).toBe(
      "bear_call_spread_wing|24500|24700|Call",
    );
  });
});
