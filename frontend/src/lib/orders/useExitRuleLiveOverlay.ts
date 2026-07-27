"use client";

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  chainQueryOptions,
  chainSuccessForExpiry,
} from "@/lib/strategy-builder/chain-query";
import { normRight } from "@/lib/portfolio/legNormalize";
import { chainLtpLookup, computeLegMtm, parseNum } from "@/lib/portfolio/liveMtm";
import { usePnlRecomputeRefetchMs } from "@/lib/portfolio/usePnlRecomputeRefetchMs";
import type { ExitRuleRow } from "@/lib/orders/exit-rules";

/** Chain-subscription key for one row's underlying — pass the same key to
 * `useGroupSubscriptionHolders`'s `getHolderId`/`releaseGroup` so rows that
 * share a chain (same stock+expiry) share one WS holder. */
export function exitRuleChainKey(row: ExitRuleRow): string {
  return `${row.exchangeCode}|${row.stockCode}|${row.expiryDisplay}`;
}

/** A Leg·GTT row's stored `action` is the *closing* order's side —
 * `route_gtt_exit_orders.py`'s `close_action` inverts it from the position's
 * entry side before it reaches this data. Flip it back before applying the
 * (avg-ltp)×qty / (ltp-avg)×qty MTM formula, otherwise the sign comes out
 * backwards. Group-row legs don't need this: `ExitRuleLeg.action` is already
 * the entry side (joined live from the P&L engine's position registry, not
 * from a fired closing order). */
function entryAction(closingAction: string | null | undefined): string | null {
  const side = String(closingAction ?? "").trim().toUpperCase();
  if (side === "BUY") return "SELL";
  if (side === "SELL") return "BUY";
  return null;
}

/**
 * Live MTM for one Profit Booking / Stop Loss row, fed by the same WS chain
 * mechanism (`chainQueryOptions`) and cadence (`usePnlRecomputeRefetchMs`) as
 * Portfolio's `useGroupLiveOverlay` — but always-on while `enabled` rather than
 * expand-gated, since this table has no expand/collapse concept for its rows.
 */
export function useExitRuleLiveOverlay(
  row: ExitRuleRow,
  holderId: string,
  enabled: boolean,
) {
  const refetchIntervalMs = usePnlRecomputeRefetchMs();
  const cq = useQuery({
    ...chainQueryOptions({
      queryKeyPrefix: ["orders", "exit-rule-chain"],
      stock_code: row.stockCode,
      expiry_date: row.expiryDisplay,
      exchange_code: row.exchangeCode,
      subscription_holder: holderId,
      refetchIntervalMs,
    }),
    enabled: enabled && Boolean(row.stockCode && row.expiryDisplay),
  });

  const chainSuccess = chainSuccessForExpiry(cq.data, row.expiryDisplay);
  const isLive = enabled && chainSuccess?.quote_source === "websocket";

  const mtm = useMemo(() => {
    if (!chainSuccess) return null;
    const lookup = chainLtpLookup(chainSuccess.chain_rows);

    if (row.kind === "leg_gtt") {
      const right = normRight(String(row.right ?? ""));
      const strike = parseNum(row.strikePrice);
      const qty = parseNum(row.quantity);
      if (!right || strike == null) return null;
      const liveLtp = lookup.get(`${right}|${strike}`);
      if (liveLtp == null) return null;
      return computeLegMtm(entryAction(row.action), row.averagePrice, qty, liveLtp);
    }

    if (!row.legs || row.legs.length === 0) return null;
    let total = 0;
    let sawAny = false;
    for (const leg of row.legs) {
      const right = normRight(leg.right);
      const strike = parseNum(leg.strikePrice);
      const qty = parseNum(leg.quantity);
      if (!right || strike == null) continue;
      const liveLtp = lookup.get(`${right}|${strike}`);
      if (liveLtp == null) continue;
      const legMtm = computeLegMtm(leg.action, leg.averagePrice, qty, liveLtp);
      if (legMtm == null) continue;
      total += legMtm;
      sawAny = true;
    }
    return sawAny ? total : null;
  }, [row, chainSuccess]);

  return { mtm, isLive };
}
