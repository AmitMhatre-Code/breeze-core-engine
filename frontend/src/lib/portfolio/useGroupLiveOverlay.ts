"use client";

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { chainQueryOptions } from "@/lib/strategy-builder/chain-query";
import { normRight } from "@/lib/portfolio/legNormalize";
import type { PortfolioPositionGroup } from "@/lib/portfolio/groupPositions";
import type { PortfolioPositionRecord } from "@/lib/portfolio";
import type { ChainRow } from "@/lib/strategy-builder/types";

function parseNum(v: unknown): number | null {
  if (v == null || v === "") return null;
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string") {
    const t = v.trim().replace(/,/g, "");
    if (!t || t === "*") return null;
    const n = Number(t);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

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
): PortfolioPositionRecord {
  const qty = parseNum(row.quantity);
  const avg = parseNum(row.average_price);
  const action = String(row.action ?? "").trim().toUpperCase();
  if (qty == null || avg == null || (action !== "SELL" && action !== "BUY")) {
    return { ...row, ltp: liveLtp };
  }
  const isSell = action === "SELL";
  const currentProfit = isSell ? (avg - liveLtp) * qty : (liveLtp - avg) * qty;
  const carryProfit = isSell ? liveLtp * qty : -liveLtp * qty;
  const next: PortfolioPositionRecord = {
    ...row,
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

function chainLtpLookup(chainRows: ChainRow[]): Map<string, number> {
  const m = new Map<string, number>();
  for (const row of chainRows) {
    const callLtp = parseNum(row.call?.ltp);
    if (callLtp != null && callLtp > 0) m.set(`Call|${row.strike_price}`, callLtp);
    const putLtp = parseNum(row.put?.ltp);
    if (putLtp != null && putLtp > 0) m.set(`Put|${row.strike_price}`, putLtp);
  }
  return m;
}

/**
 * Live-overlays a portfolio group's rows with the same WS-fed chain
 * `PortfolioGroupPayoffPanel` already fetches for the payoff curve (same query key via
 * `chainQueryOptions`, so this shares the cached request rather than double-fetching).
 * Only active while `live` (the group is expanded) — collapsed groups stay on the
 * ~30s base `/portfolio/data` poll.
 */
export function useGroupLiveOverlay(
  group: PortfolioPositionGroup,
  holderId: string,
  live: boolean,
) {
  const cq = useQuery({
    ...chainQueryOptions({
      queryKeyPrefix: ["portfolio", "group-payoff-chain"],
      stock_code: group.stockCode,
      expiry_date: group.expiryDate,
      exchange_code: group.exchangeCode,
      subscription_holder: holderId,
    }),
    enabled: live && Boolean(group.stockCode && group.expiryDate),
  });

  const chainSuccess = cq.data?.Status === 200 ? cq.data.Success : null;
  const isLive = live && chainSuccess?.quote_source === "websocket";

  const rows = useMemo(() => {
    if (!chainSuccess) return group.rows;
    const lookup = chainLtpLookup(chainSuccess.chain_rows);
    return group.rows.map((row) => {
      const right = normRight(String(row.right ?? ""));
      const strike = parseNum(row.strike_price);
      if (!right || strike == null) return row;
      const liveLtp = lookup.get(`${right}|${strike}`);
      return liveLtp == null ? row : overlayRowWithLiveLtp(row, liveLtp);
    });
  }, [group.rows, chainSuccess]);

  return { rows, isLive };
}
