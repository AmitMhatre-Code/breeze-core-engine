import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { chainLotSize, rowsToStrategyLegs } from "@/lib/portfolio/legsFromRows";
import type { PortfolioPositionRecord } from "@/lib/portfolio";
import { atmSigmaFromChain } from "@/lib/strategy-builder/chainIv";
import { payoffQuoteQueryOptions } from "@/lib/strategy-builder/chain-query";
import { expiryDisplayToYears } from "@/lib/strategy-builder/expiry";
import { estimateProbabilityOfProfit } from "@/lib/strategy-builder/payoff";

/**
 * Single-leg PoP for the individual-legs view — same math as `useGroupPoP`
 * (`portfolioPayoffAtExpiry` has no multi-leg assumption, one leg is just the
 * degenerate case), but fed a one-row leg list instead of a whole group's rows.
 *
 * The underlying chain-quote query key is built from (stockCode, expiryDate,
 * exchangeCode) only — not the leg's strike — so legs sharing a scrip+expiry
 * dedupe onto the same react-query cache entry `useGroupPoP` already produces
 * for that group. This is why per-leg PoP doesn't add extra backend load.
 */
export function useLegPoP(
  row: PortfolioPositionRecord,
  stockCode: string,
  expiryDate: string,
  exchangeCode: string,
): number | null {
  const cq = useQuery({
    ...payoffQuoteQueryOptions({
      queryKeyPrefix: ["portfolio", "group-pop"],
      stock_code: stockCode,
      expiry_date: expiryDate,
      exchange_code: exchangeCode,
    }),
    enabled: Boolean(stockCode && expiryDate),
  });

  const chainSuccess = cq.data?.Status === 200 ? cq.data?.Success : null;

  return useMemo(() => {
    if (!chainSuccess) return null;
    const lotSize = chainLotSize(chainSuccess);
    const legs = rowsToStrategyLegs([row], lotSize);
    const spot = chainSuccess.spot_price;
    if (!legs.length || spot == null || !Number.isFinite(spot) || spot <= 0) {
      return null;
    }
    const T = expiryDisplayToYears(expiryDate || "01-Jan-2099");
    const sigma = atmSigmaFromChain(chainSuccess, T);
    return estimateProbabilityOfProfit(spot, T, sigma, legs, lotSize);
  }, [chainSuccess, row, expiryDate]);
}
