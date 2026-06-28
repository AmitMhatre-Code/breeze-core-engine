"use client";

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { PayoffChart } from "@/components/strategy-builder/PayoffChart";
import { InfinitySymbol } from "@/components/strategy-builder/InfinitySymbol";
import { apiClient } from "@/lib/api-client";
import { formatIndianMoneyCompact } from "@/lib/format-money-in";
import type { PortfolioPositionRecord } from "@/lib/portfolio";
import { atmSigmaFromChain } from "@/lib/strategy-builder/chainIv";
import { expiryDisplayToYears } from "@/lib/strategy-builder/expiry";
import {
  estimateProbabilityOfProfit,
  payoffChartSpotDomain,
  scanMarkToModelCurve,
  scanPayoffCurve,
  summarizePayoffExact,
  type PayoffSummary,
} from "@/lib/strategy-builder/payoff";
import {
  isUnlimitedMaxLoss,
  isUnlimitedMaxProfit,
} from "@/lib/strategy-builder/trade-metrics";
import type {
  ChainApiResponse,
  ChainSuccess,
  OptionRight,
  OrderSide,
  StrategyLeg,
} from "@/lib/strategy-builder/types";

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

function normSide(raw: string): OrderSide | null {
  const t = raw.trim().toLowerCase();
  if (t === "buy") return "Buy";
  if (t === "sell") return "Sell";
  return null;
}

function normRight(raw: string): OptionRight | null {
  const t = raw.trim().toLowerCase();
  if (t === "put" || t === "p" || t === "pe") return "Put";
  if (t === "call" || t === "c" || t === "ce") return "Call";
  return null;
}

function chainLotSize(chain: ChainSuccess): number {
  const row = chain.chain_rows[0];
  if (!row) return 1;
  const ls =
    parseNum(row.call?.lot_size) ?? parseNum(row.put?.lot_size) ?? NaN;
  return Number.isFinite(ls) && ls > 0 ? Math.round(ls) : 1;
}

function rowsToStrategyLegs(
  rows: PortfolioPositionRecord[],
  lotSize: number,
): StrategyLeg[] {
  const ls = lotSize > 0 ? lotSize : 1;
  const legs: StrategyLeg[] = [];
  let i = 0;
  for (const row of rows) {
    const side = normSide(String(row.action ?? ""));
    const right = normRight(String(row.right ?? ""));
    const strike = parseNum(row.strike_price);
    const qtyRaw = parseNum(row.quantity);
    if (!side || !right || strike == null || strike <= 0 || qtyRaw == null) {
      continue;
    }
    const absQty = Math.abs(qtyRaw);
    if (absQty <= 0) continue;
    const lots = absQty / ls;
    const prem =
      parseNum(row.average_price) ?? parseNum(row.ltp) ?? 0;
    legs.push({
      id: `port-${i++}`,
      right,
      side,
      strike,
      lots,
      premiumPerUnit: prem,
    });
  }
  return legs;
}

type Props = {
  stockCode: string;
  exchangeCode: string;
  expiryDisplay: string;
  rows: PortfolioPositionRecord[];
  /** Proposed protective wing to overlay on payoff (hedge preview). */
  proposedLeg?: StrategyLeg | null;
};

const profitClass = "text-emerald-700 dark:text-emerald-400";
const lossClass = "text-red-700 dark:text-red-400";
const PAYOFF_STEPS = 401;

/** Payoff + POP for one underlying/expiry bucket (chain fetch for lot size, spot, IV). */
export function PortfolioGroupPayoffPanel({
  stockCode,
  exchangeCode,
  expiryDisplay,
  rows,
  proposedLeg = null,
}: Props) {
  const cq = useQuery({
    queryKey: [
      "portfolio",
      "group-payoff-chain",
      stockCode,
      exchangeCode,
      expiryDisplay,
    ],
    queryFn: ({ signal }) => {
      const q = new URLSearchParams({
        stock_code: stockCode,
        exchange_code: exchangeCode,
        expiry_date: expiryDisplay,
      });
      return apiClient.get<ChainApiResponse>(
        `/strategy-builder/chain?${q.toString()}`,
        signal,
      );
    },
    enabled: Boolean(stockCode && expiryDisplay && rows.length > 0),
    staleTime: 60_000,
  });

  const chainSuccess = cq.data?.Status === 200 ? cq.data?.Success : null;

  const lotSize = useMemo(
    () => (chainSuccess ? chainLotSize(chainSuccess) : 1),
    [chainSuccess],
  );

  const legs = useMemo(() => {
    const base = rowsToStrategyLegs(rows, lotSize);
    if (!proposedLeg) return base;
    return [...base, proposedLeg];
  }, [rows, lotSize, proposedLeg]);

  const hasProposedHedge = proposedLeg != null;

  const strikes = useMemo(
    () =>
      (chainSuccess?.chain_rows ?? [])
        .map((r) => r.strike_price)
        .sort((a, b) => a - b),
    [chainSuccess],
  );

  const T = expiryDisplayToYears(expiryDisplay || "01-Jan-2099");
  const spot = chainSuccess?.spot_price ?? null;
  const sigma = chainSuccess ? atmSigmaFromChain(chainSuccess, T) : 0.22;

  const { minS, maxS } = useMemo(() => {
    if (spot != null && Number.isFinite(spot) && spot > 0) {
      return payoffChartSpotDomain(spot);
    }
    if (!strikes.length) {
      return { minS: 0, maxS: 1 };
    }
    const lo = Math.min(...strikes);
    const hi = Math.max(...strikes);
    const center = (lo + hi) / 2;
    const pad = Math.max((hi - lo) * 0.25, center * 0.35);
    return {
      minS: Math.max(0, Math.min(lo, center) - pad),
      maxS: Math.max(1, Math.max(hi, center) + pad),
    };
  }, [strikes, spot]);

  const { xs, ys, summary, xsToday, ysToday, pop } = useMemo(() => {
    const steps = PAYOFF_STEPS;
    if (!legs.length) {
      return {
        xs: [] as number[],
        ys: [] as number[],
        summary: { maxProfit: 0, maxLoss: 0, breakevens: [] } as PayoffSummary,
        xsToday: [] as number[],
        ysToday: [] as number[],
        pop: 0,
      };
    }
    const { xs: x1, ys: y1 } = scanPayoffCurve(
      minS,
      maxS,
      steps,
      legs,
      lotSize,
    );
    const exactSummary = summarizePayoffExact(legs, lotSize, spot);
    let xt: number[] = [];
    let yt: number[] = [];
    if (spot != null && T > 0) {
      const r = scanMarkToModelCurve(
        minS,
        maxS,
        steps,
        legs,
        lotSize,
        T,
        sigma,
      );
      xt = r.xs;
      yt = r.ys;
    }
    const popVal =
      spot != null ? estimateProbabilityOfProfit(spot, T, sigma, legs, lotSize) : 0;
    return {
      xs: x1,
      ys: y1,
      summary: exactSummary,
      xsToday: xt,
      ysToday: yt,
      pop: popVal,
    };
  }, [legs, minS, maxS, spot, sigma, T, lotSize]);

  const hasLegs = legs.length > 0;

  if (cq.isLoading) {
    return (
      <div className="border-t border-zinc-200/80 bg-zinc-50/80 px-3 py-4 text-sm app-text-muted dark:border-zinc-700/80 dark:bg-zinc-900/50">
        Loading payoff…
      </div>
    );
  }

  if (cq.isError) {
    return (
      <div className="border-t border-zinc-200/80 bg-zinc-50/80 px-3 py-4 text-sm text-red-600 dark:border-zinc-700/80 dark:bg-zinc-900/50 dark:text-red-400">
        {cq.error instanceof Error
          ? cq.error.message
          : "Unable to load chain for payoff."}
      </div>
    );
  }

  if (cq.data && cq.data.Status !== 200) {
    return (
      <div className="border-t border-zinc-200/80 bg-zinc-50/80 px-3 py-4 text-sm text-red-600 dark:border-zinc-700/80 dark:bg-zinc-900/50 dark:text-red-400">
        {String(cq.data.Error ?? "Chain request failed.")}
      </div>
    );
  }

  return (
    <div className="border-t border-zinc-200/80 bg-zinc-50/80 dark:border-zinc-700/80 dark:bg-zinc-900/50">
      <div className="flex w-full min-w-0 flex-col gap-4 p-3 sm:flex-row sm:items-stretch sm:gap-4">
        <div className="flex w-full shrink-0 flex-row flex-wrap gap-x-6 gap-y-3 sm:w-48 sm:flex-col sm:flex-nowrap sm:gap-3">
          <div>
            <div className="text-[11px] font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
              Max profit
            </div>
            <div className={`font-semibold tabular-nums ${profitClass}`}>
              {hasLegs ? (
                isUnlimitedMaxProfit(summary.maxProfit) ? (
                  <InfinitySymbol />
                ) : (
                  formatIndianMoneyCompact(summary.maxProfit)
                )
              ) : (
                "—"
              )}
            </div>
          </div>
          <div>
            <div className="text-[11px] font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
              Max loss
            </div>
            <div className={`font-semibold tabular-nums ${lossClass}`}>
              {hasLegs ? (
                isUnlimitedMaxLoss(summary.maxLoss) ? (
                  <InfinitySymbol />
                ) : (
                  formatIndianMoneyCompact(summary.maxLoss)
                )
              ) : (
                "—"
              )}
            </div>
          </div>
          <div>
            <div className="text-[11px] font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
              Breakevens
            </div>
            <div className="font-medium tabular-nums text-zinc-800 dark:text-zinc-200">
              {hasLegs && summary.breakevens.length
                ? summary.breakevens.map((b) => b.toFixed(0)).join(", ")
                : "—"}
            </div>
          </div>
          <div>
            <div className="text-[11px] font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
              POP (model)
            </div>
            <div className="font-medium tabular-nums text-zinc-800 dark:text-zinc-200">
              {hasLegs ? `${pop.toFixed(1)}%` : "—"}
            </div>
          </div>
        </div>
        <div className="min-w-0 flex-1">
          <p className="mb-2 text-[11px] leading-relaxed text-zinc-500 dark:text-zinc-400">
            Solid green = P&amp;L at expiry; dotted violet = mark-to-model now.
            Amber dashes = breakevens. Uses average entry and chain IV.
            {hasProposedHedge ? (
              <span className="mt-1 block font-medium text-sky-700 dark:text-sky-300">
                Includes proposed hedge leg (Buy {proposedLeg!.right}{" "}
                {proposedLeg!.strike}).
              </span>
            ) : null}
          </p>
          <PayoffChart
            key={`${stockCode}-${expiryDisplay}-${minS}-${maxS}-${proposedLeg?.id ?? "none"}`}
            idle={!hasLegs}
            xs={xs}
            ys={ys}
            xsToday={xsToday.length ? xsToday : undefined}
            ysToday={ysToday.length ? ysToday : undefined}
            spot={spot}
            breakevens={summary.breakevens}
            minS={minS}
            maxS={maxS}
            height={220}
          />
        </div>
      </div>
    </div>
  );
}
