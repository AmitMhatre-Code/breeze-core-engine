"use client";

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  chainQueryOptions,
  chainSuccessForExpiry,
} from "@/lib/strategy-builder/chain-query";
import { normRight } from "@/lib/portfolio/legNormalize";
import { chainLtpLookup, computeLegMtm, parseNum } from "@/lib/portfolio/liveMtm";
import type { PortfolioPositionGroup } from "@/lib/portfolio/groupPositions";
import type { PortfolioPositionRecord } from "@/lib/portfolio";
import { usePnlRecomputeRefetchMs } from "@/lib/portfolio/usePnlRecomputeRefetchMs";

/** (Carry ÷ DTE) × 365 ÷ margin × 100 — mirrors `_annualized_carry_percent_on_span` in processor.py. */
function annualizedCarryPercent(
  carry: number,
  dte: number,
  margin: number,
): number | null {
  if (margin <= 0 || dte <= 0) return null;
  return (carry / dte) * (365 / margin) * 100;
}

/** Recomputes MTM/Carry/Carry Ret from a fresh LTP, mirroring processor.py's SELL/BUY formulas exactly. */
function overlayRowWithLiveLtp(
  row: PortfolioPositionRecord,
  liveLtp: number,
  quoteSource: string | null,
): PortfolioPositionRecord {
  const qty = parseNum(row.quantity);
  const avg = parseNum(row.average_price);
  const action = String(row.action ?? "").trim().toUpperCase();
  const currentProfit = computeLegMtm(action, avg, qty, liveLtp);
  // The overlaid price supersedes the backend's, so its provenance must too —
  // otherwise a row refreshed from a live tick keeps the snapshot's "prev close"
  // note (or vice versa) and the badge lies about the number beside it.
  const sourced =
    quoteSource != null ? { quote_source: quoteSource } : {};
  if (qty == null || avg == null || currentProfit == null) {
    return { ...row, ...sourced, ltp: liveLtp };
  }
  const isSell = action === "SELL";
  // Carry = P&L if this leg expires worthless (full premium kept/lost) minus MTM already captured.
  const worthlessValue = isSell ? avg * qty : -avg * qty;
  const carryProfit = worthlessValue - currentProfit;
  const next: PortfolioPositionRecord = {
    ...row,
    ...sourced,
    ltp: liveLtp,
    current_profit: currentProfit,
    carry_profit: carryProfit,
  };
  if (isSell) {
    const span = parseNum(row.span_margin_required);
    const elm = parseNum(row.elm_margin_required) ?? 0;
    const dte = parseNum(row.days_to_expiry);
    if (span != null && dte != null) {
      const cr = annualizedCarryPercent(carryProfit, dte, span + elm);
      if (cr != null) next.carry_margin_returns = cr;
    }
  }
  return next;
}

/**
 * Live-overlays a portfolio group's rows with the same WS-fed chain
 * `PortfolioGroupPayoffPanel` already fetches for the payoff curve (same query key via
 * `chainQueryOptions`, so this shares the cached request rather than double-fetching —
 * both must pass the same `refetchIntervalMs` from `usePnlRecomputeRefetchMs` or their
 * observers fight over the shared query's poll cadence).
 * Active for every open-position group regardless of expand state — a WS holder is
 * registered for each group unconditionally (see `useGroupSubscriptionHolders`), so
 * collapsed groups get live LTP too instead of waiting on a REST poll.
 */
export function useGroupLiveOverlay(
  group: PortfolioPositionGroup,
  holderId: string,
) {
  const refetchIntervalMs = usePnlRecomputeRefetchMs();
  const cq = useQuery({
    ...chainQueryOptions({
      queryKeyPrefix: ["portfolio", "group-payoff-chain"],
      stock_code: group.stockCode,
      expiry_date: group.expiryDate,
      exchange_code: group.exchangeCode,
      subscription_holder: holderId,
      refetchIntervalMs,
    }),
    enabled: Boolean(group.stockCode && group.expiryDate),
  });

  const chainSuccess = chainSuccessForExpiry(cq.data, group.expiryDate);
  const isLive = chainSuccess?.quote_source === "websocket";

  const rows = useMemo(() => {
    if (!chainSuccess) return group.rows;
    const lookup = chainLtpLookup(chainSuccess.chain_rows);
    // Live underlying spot rides the same WS chain payload the LTP overlay
    // already polls (`chainSuccess.spot_price`) — overlay it onto every leg so
    // the SPOT column updates at the WS cadence and a stale/`Err` snapshot spot
    // is replaced whenever the chain carries a live spot. Falls back to the
    // `/portfolio/data` snapshot's `spot_price` when the chain has none.
    const liveSpot =
      chainSuccess.spot_price != null && Number.isFinite(chainSuccess.spot_price)
        ? chainSuccess.spot_price
        : null;
    return group.rows.map((row) => {
      const base = liveSpot != null ? { ...row, spot_price: liveSpot } : row;
      const right = normRight(String(row.right ?? ""));
      const strike = parseNum(row.strike_price);
      if (!right || strike == null) return base;
      const liveLtp = lookup.get(`${right}|${strike}`);
      return liveLtp == null
        ? base
        : overlayRowWithLiveLtp(base, liveLtp, chainSuccess.quote_source ?? null);
    });
  }, [group.rows, chainSuccess]);

  return { rows, isLive };
}
