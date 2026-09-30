import type { StrategyLeg } from "@/lib/strategy-builder/types";

/** A leg with size but no usable price: no chain quote and nothing typed in.
 *
 * Such a leg used to be counted at ₹0 in the net premium and the payoff figures, so a
 * put spread whose long leg had no quote showed the short leg's premium alone as the
 * net premium (B-57). It is left out of the figures, with a note, and named on its row. */
export function isUnpricedLeg(leg: StrategyLeg): boolean {
  if (leg.lots <= 0) return false;
  const p = leg.premiumPerUnit;
  return !(typeof p === "number" && Number.isFinite(p) && p > 0);
}

/** Legs the premium and payoff figures can use: sized and priced. */
export function pricedLegs(legs: StrategyLeg[]): StrategyLeg[] {
  return legs.filter((l) => l.lots > 0 && !isUnpricedLeg(l));
}

export function unpricedLegCount(legs: StrategyLeg[]): number {
  return legs.filter(isUnpricedLeg).length;
}

/** The note shown beside figures that leave unpriced legs out, or null when none do. */
export function unpricedNote(legs: StrategyLeg[]): string | null {
  const n = unpricedLegCount(legs);
  if (!n) return null;
  return `${n} leg${n === 1 ? " has" : "s have"} no quote and ${n === 1 ? "is" : "are"} left out of these figures.`;
}

/** Signed net premium over priced, non-aggressive legs (credit positive). */
export function netPremiumOfPricedLegs(legs: StrategyLeg[], lotSize: number): number {
  let t = 0;
  for (const l of pricedLegs(legs)) {
    if (l.aggressiveLimit) continue;
    const prem = (l.premiumPerUnit as number) * l.lots * lotSize;
    t += l.side === "Sell" ? prem : -prem;
  }
  return t;
}
