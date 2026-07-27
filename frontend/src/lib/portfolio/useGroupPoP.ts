import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { chainLotSize, rowsToStrategyLegs } from "@/lib/portfolio/legsFromRows";
import type { PortfolioPositionGroup } from "@/lib/portfolio/groupPositions";
import { atmSigmaFromChain, blendedSigmaForLegs, buildSigmaSmiles } from "@/lib/strategy-builder/chainIv";
import {
  chainSuccessForExpiry,
  payoffQuoteQueryOptions,
} from "@/lib/strategy-builder/chain-query";
import { expiryDisplayToYears } from "@/lib/strategy-builder/expiry";
import { estimateProbabilityOfProfit } from "@/lib/strategy-builder/payoff";

/**
 * Group-level PoP for the (collapsed or expanded) summary row. Deliberately does
 * *not* pass a `subscription_holder` — unlike `PortfolioGroupPayoffPanel`'s own
 * chain query, this one is enabled for every group unconditionally (including
 * collapsed ones), so it must stay a one-shot REST fetch rather than a live WS
 * subscription (an unconditional per-group WS holder for every row would be a much
 * bigger resource cost just to show a PoP number).
 */
export function useGroupPoP(group: PortfolioPositionGroup): number | null {
  const cq = useQuery({
    ...payoffQuoteQueryOptions({
      queryKeyPrefix: ["portfolio", "group-pop"],
      stock_code: group.stockCode,
      expiry_date: group.expiryDate,
      exchange_code: group.exchangeCode,
    }),
    enabled: Boolean(group.stockCode && group.expiryDate && group.rows.length > 0),
  });

  const chainSuccess = chainSuccessForExpiry(cq.data, group.expiryDate);

  return useMemo(() => {
    if (!chainSuccess) return null;
    const lotSize = chainLotSize(chainSuccess);
    const legs = rowsToStrategyLegs(group.rows, lotSize);
    const spot = chainSuccess.spot_price;
    if (!legs.length || spot == null || !Number.isFinite(spot) || spot <= 0) {
      return null;
    }
    const T = expiryDisplayToYears(group.expiryDate || "01-Jan-2099");
    const fallback = atmSigmaFromChain(chainSuccess, T);
    const sigma = blendedSigmaForLegs(buildSigmaSmiles(chainSuccess, T), legs, spot, lotSize, fallback);
    return estimateProbabilityOfProfit(spot, T, sigma, legs, lotSize);
  }, [chainSuccess, group.rows, group.expiryDate]);
}
