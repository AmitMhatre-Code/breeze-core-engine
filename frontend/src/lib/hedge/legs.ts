import type { StrategyHedgeCandidate } from "@/lib/hedge/api";
import type { ExecutionPreviewLeg } from "@/components/order/OrderExecutionConfirmDialog";
import type { StrategyLeg } from "@/lib/strategy-builder/types";

export function candidateToStrategyLeg(
  c: StrategyHedgeCandidate,
  lotSize: number,
): StrategyLeg {
  const ls = lotSize > 0 ? lotSize : 1;
  const lots = c.hedge_quantity / ls;
  return {
    id: `hedge-${c.strike_price}-${c.right}`,
    right: c.right,
    side: "Buy",
    strike: c.strike_price,
    lots,
    premiumPerUnit: c.ltp,
  };
}

export function candidateToExecutionLeg(
  c: StrategyHedgeCandidate,
): ExecutionPreviewLeg {
  return {
    strike: c.strike_price,
    right: c.right,
    side: "Buy",
    quantity: c.hedge_quantity,
    premiumPerUnit: c.ltp,
    aggressiveLimit: false,
  };
}

export function hedgeCandidateKey(c: StrategyHedgeCandidate): string {
  return `${c.hedge_type}|${c.short_strike}|${c.strike_price}|${c.right}`;
}
