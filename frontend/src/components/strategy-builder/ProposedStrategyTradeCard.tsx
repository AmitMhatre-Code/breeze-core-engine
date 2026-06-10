"use client";

import { useMemo } from "react";
import { estimateProbabilityOfProfit } from "@/lib/strategy-builder/payoff";
import { proposedLegsToStrategyLegs } from "@/lib/strategy-builder/map-proposed-legs";
import { expiryDisplayToYears } from "@/lib/strategy-builder/expiry";
import type { ProposedTrade } from "@/lib/strategy-builder/types";
import { formatIndianMoneyCompact } from "@/lib/format-money-in";

const BOOK_LAKH = 100_000;

function formatBookQtyInL(n: number): string {
  if (!Number.isFinite(n)) return "—";
  return `${(n / BOOK_LAKH).toLocaleString("en-IN", {
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  })} L`;
}

function fmtPx(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return "—";
  return n.toLocaleString("en-IN", { maximumFractionDigits: 2 });
}

export function ProposedStrategyTradeCard({
  trade,
  lotSize,
  spot,
  atmIv,
  expiryDate,
  selected,
  onSelect,
}: {
  trade: ProposedTrade;
  lotSize: number;
  spot: number | null;
  atmIv: number | null;
  expiryDate: string;
  selected: boolean;
  onSelect: () => void;
}) {
  const skipped = trade.status === "skipped";
  const T = useMemo(() => expiryDisplayToYears(expiryDate), [expiryDate]);
  const sigma = (atmIv != null && atmIv > 0 ? atmIv : 0.2);

  const pop = useMemo(() => {
    if (skipped || spot == null || !trade.legs.length) return null;
    const legs = proposedLegsToStrategyLegs(trade.legs, lotSize);
    return estimateProbabilityOfProfit(spot, T, sigma, legs, lotSize);
  }, [skipped, spot, trade.legs, lotSize, T, sigma]);

  const vBar = (
    <span
      className="mx-1.5 h-4 w-px shrink-0 self-center bg-zinc-200 dark:bg-zinc-600"
      aria-hidden
    />
  );

  return (
    <div
      className={`w-full min-w-0 max-w-[min(100%,28rem)] rounded-md border p-2.5 shadow-sm backdrop-blur-sm ${
        skipped
          ? "border-zinc-200/60 bg-zinc-100/50 opacity-70 dark:border-zinc-700/60 dark:bg-zinc-900/40"
          : selected
            ? "border-sky-500 ring-2 ring-sky-500/40 bg-white/95 dark:bg-zinc-950/70"
            : "border-zinc-200/80 bg-white/90 dark:border-zinc-700/80 dark:bg-zinc-950/60"
      }`}
    >
      <div className="space-y-2">
        <div className="flex min-w-0 items-center gap-x-2 overflow-hidden text-sm font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
          {trade.strategy_name}
          {trade.structure_modified ? (
            <span className="shrink-0 rounded-full bg-amber-500/15 px-2 py-0.5 text-[10px] font-medium text-amber-800 dark:text-amber-200">
              Modified
            </span>
          ) : null}
        </div>

        {skipped ? (
          <p className="text-xs text-zinc-500 dark:text-zinc-400">
            {trade.skip_reason ?? "Not available"}
          </p>
        ) : (
          <>
            <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-zinc-600 dark:text-zinc-300">
              <span>
                Net prem:{" "}
                <strong className="tabular-nums text-zinc-900 dark:text-zinc-100">
                  {trade.net_premium != null
                    ? formatIndianMoneyCompact(trade.net_premium)
                    : "—"}
                </strong>
              </span>
              <span>
                Max loss:{" "}
                <strong className="tabular-nums">
                  {trade.max_loss != null
                    ? formatIndianMoneyCompact(trade.max_loss)
                    : "Unlimited"}
                </strong>
              </span>
              {trade.annualized_return_pct != null ? (
                <span>
                  Ann. ROI:{" "}
                  <strong className="tabular-nums text-emerald-700 dark:text-emerald-300">
                    {trade.annualized_return_pct.toFixed(1)}%
                  </strong>
                </span>
              ) : null}
            </div>
            <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-zinc-500 dark:text-zinc-400">
              <span>R:R {trade.risk_reward_ratio ?? "—"}</span>
              {pop != null ? (
                <span>
                  PoP:{" "}
                  <strong className="text-zinc-800 dark:text-zinc-200">
                    {pop.toFixed(1)}%
                  </strong>
                </span>
              ) : null}
            </div>

            <div className="space-y-1.5 border-t border-zinc-100 pt-1.5 dark:border-zinc-800">
              {trade.legs.map((leg, i) => {
                const abbr = leg.right === "Put" ? "PE" : "CE";
                const ratioStr =
                  typeof leg.buy_sell_ratio === "number"
                    ? leg.buy_sell_ratio.toLocaleString("en-IN", {
                        maximumFractionDigits: 2,
                      })
                    : leg.buy_sell_ratio ?? "—";
                return (
                  <div
                    key={`${leg.strike}-${leg.right}-${leg.side}-${i}`}
                    className="rounded border border-zinc-100 bg-zinc-50/80 px-2 py-1.5 text-[11px] dark:border-zinc-800 dark:bg-zinc-900/50"
                  >
                    <div className="flex flex-wrap items-center gap-x-2 font-medium text-zinc-800 dark:text-zinc-200">
                      <span>
                        {leg.side} {leg.strike.toLocaleString("en-IN")} {abbr}
                      </span>
                      {vBar}
                      <span className="tabular-nums">×{leg.quantity}</span>
                    </div>
                    <div className="mt-0.5 flex flex-wrap gap-x-2 text-zinc-500 dark:text-zinc-400">
                      <span>LTP {fmtPx(leg.ltp)}</span>
                      <span>
                        Bid {fmtPx(leg.best_bid_price)} / Offer{" "}
                        {fmtPx(leg.best_offer_price)}
                      </span>
                      <span>
                        B:S {ratioStr}
                      </span>
                      <span>
                        Buy {formatBookQtyInL(leg.total_buy_qty ?? NaN)} / Sell{" "}
                        {formatBookQtyInL(leg.total_sell_qty ?? NaN)}
                      </span>
                    </div>
                  </div>
                );
              })}
            </div>
          </>
        )}
      </div>

      {!skipped ? (
        <div className="mt-2.5">
          <button
            type="button"
            className={
              selected
                ? "w-full cursor-default rounded-lg border border-sky-600 bg-sky-600 py-2.5 text-sm font-semibold text-white"
                : "w-full rounded-lg border border-sky-600 bg-transparent py-2.5 text-sm font-semibold text-sky-700 transition hover:bg-sky-600 hover:text-white focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-500/50 dark:border-sky-500 dark:text-sky-300 dark:hover:bg-sky-600 dark:hover:text-white"
            }
            onClick={onSelect}
            aria-pressed={selected}
          >
            {selected ? "Selected" : "Select"}
          </button>
        </div>
      ) : null}
    </div>
  );
}
