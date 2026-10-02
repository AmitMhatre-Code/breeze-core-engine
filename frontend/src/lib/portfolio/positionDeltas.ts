import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import type { PortfolioPositionRecord } from "@/lib/portfolio";
import { chainLotSize, parseNum } from "@/lib/portfolio/legsFromRows";
import { normRight, normSide } from "@/lib/portfolio/legNormalize";
import {
  chainSuccessForExpiry,
  payoffQuoteQueryOptions,
} from "@/lib/strategy-builder/chain-query";
import {
  buildGreeksModel,
  netDelta,
  positionDelta,
  type GreeksModel,
  type PositionDelta,
} from "@/lib/strategy-builder/greeks";
import type { ChainSuccess } from "@/lib/strategy-builder/types";

/**
 * Position delta for each row, in row order. `undefined` for a row with nothing open
 * (squared off, zero quantity), so it neither shows a delta nor holds back the total;
 * `null` when the row is open but the chain can't price it.
 */
export function portfolioRowDeltas(
  model: GreeksModel | null,
  rows: PortfolioPositionRecord[],
): (PositionDelta | null | undefined)[] {
  return rows.map((row) => {
    const units = Math.abs(parseNum(row.quantity) ?? 0);
    if (!(units > 0)) return undefined;
    const right = normRight(String(row.right ?? ""));
    const side = normSide(String(row.action ?? ""));
    const strike = parseNum(row.strike_price);
    if (!model || !right || !side || strike == null) return null;
    return positionDelta(model, { right, side, strike, units });
  });
}

export type GroupDeltas = {
  rows: (PositionDelta | null | undefined)[];
  /** Sum over the group's open legs — exact, since one group is one underlying and expiry. */
  net: number | null;
  lotSize: number | null;
};

/**
 * Deltas for one Strategy Group off the chain its live overlay already polls
 * (`useGroupLiveOverlay`), so they tick at the WS cadence with no extra request.
 */
export function useGroupDeltas(
  chainSuccess: ChainSuccess | null,
  rows: PortfolioPositionRecord[],
  expiryDate: string,
): GroupDeltas {
  // Rebuilt per chain refresh, not per render: the overlay re-maps rows on every poll.
  const model = useMemo(
    () => buildGreeksModel(chainSuccess, expiryDate),
    [chainSuccess, expiryDate],
  );
  return useMemo(() => {
    const deltas = portfolioRowDeltas(model, rows);
    return {
      rows: deltas,
      net: netDelta(deltas),
      lotSize: chainSuccess ? chainLotSize(chainSuccess) : null,
    };
  }, [model, rows, chainSuccess]);
}

/**
 * One leg's delta for the individual-legs view. Shares `useLegPoP`'s one-shot chain query
 * (same key), so it adds no request of its own.
 */
export function useLegDelta(
  row: PortfolioPositionRecord,
  stockCode: string,
  expiryDate: string,
  exchangeCode: string,
): { delta: PositionDelta | null | undefined; lotSize: number | null } {
  const cq = useQuery({
    ...payoffQuoteQueryOptions({
      queryKeyPrefix: ["portfolio", "group-pop"],
      stock_code: stockCode,
      expiry_date: expiryDate,
      exchange_code: exchangeCode,
    }),
    enabled: Boolean(stockCode && expiryDate),
  });
  const chainSuccess = chainSuccessForExpiry(cq.data, expiryDate);
  return useMemo(() => {
    const model = buildGreeksModel(chainSuccess, expiryDate);
    return {
      delta: portfolioRowDeltas(model, [row])[0],
      lotSize: chainSuccess ? chainLotSize(chainSuccess) : null,
    };
  }, [chainSuccess, row, expiryDate]);
}
