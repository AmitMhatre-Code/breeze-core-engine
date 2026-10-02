"use client";

import { useLiquidityCheck, warningFor, type LiquidityWarning } from "@/lib/liquidity";
import type { StrategyLeg } from "@/lib/strategy-builder/types";

/**
 * Liquidity warnings for the Basket and Strategy Builder leg tables, keyed by leg id
 * (docs/liquidity-checks-plan.md). Each leg is judged on its own side and size; legs on the same
 * contract and side are summed by the backend, so two rows selling one strike warn together.
 */
export function useLegLiquidityWarnings(args: {
  exchangeCode: string;
  stockCode: string;
  expiryDisplay: string;
  lotSize: number;
  legs: StrategyLeg[];
}): Record<string, LiquidityWarning> {
  const ls = args.lotSize > 0 ? args.lotSize : 0;
  const q = useLiquidityCheck({
    exchangeCode: args.exchangeCode,
    stockCode: args.stockCode,
    expiryDisplay: args.expiryDisplay,
    lotSize: ls || null,
    legs: ls
      ? args.legs
          .filter((l) => l.lots > 0)
          .map((l) => ({
            ref: l.id,
            strike: l.strike,
            right: l.right,
            side: l.side,
            quantity: Math.round(l.lots * ls),
          }))
      : [],
  });
  const out: Record<string, LiquidityWarning> = {};
  for (const l of args.legs) out[l.id] = warningFor(q.data, l.id);
  return out;
}
