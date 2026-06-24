import type { ProposedTradeLeg, StrategyLeg } from "@/lib/strategy-builder/types";

export function proposedLegsToStrategyLegs(
  legs: ProposedTradeLeg[],
  lotSize: number,
): StrategyLeg[] {
  const ls = lotSize > 0 ? lotSize : 1;
  return legs.map((leg, i) => ({
    id: `leg-${leg.strike}-${leg.right}-${leg.side}-${i}`,
    right: leg.right,
    side: leg.side,
    strike: leg.strike,
    lots: Math.max(0, Math.round(leg.quantity / ls)),
    premiumPerUnit: leg.premium_per_unit,
    aggressiveLimit: false,
  }));
}
