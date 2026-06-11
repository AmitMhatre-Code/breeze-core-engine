"use client";

import { useMemo } from "react";
import { OutlookIcon } from "@/components/strategy-builder/OutlookIcon";
import { formatIndianMoneyCompact, moneyToneClass } from "@/lib/format-money-in";
import { strategyOutlook } from "@/lib/strategy-builder/templates";
import {
  computeTradePop,
  formatRiskRewardRatio,
  isUnlimitedMaxLoss,
} from "@/lib/strategy-builder/trade-metrics";
import type { ProposedTrade } from "@/lib/strategy-builder/types";

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

function InfinitySymbol() {
  return (
    <span
      className="inline-flex h-4 w-4 items-center justify-center text-base leading-none"
      aria-hidden
    >
      ∞
    </span>
  );
}

function premiumCapsuleClass(premium: number | null | undefined): string {
  if (premium == null || !Number.isFinite(premium)) {
    return "bg-zinc-500/10 text-zinc-700 ring-zinc-500/20 dark:text-zinc-300 dark:ring-zinc-400/25";
  }
  if (premium < 0) {
    return "bg-rose-500/14 text-rose-800 ring-rose-600/20 dark:bg-rose-500/12 dark:text-rose-300 dark:ring-rose-400/30";
  }
  return "bg-emerald-500/14 text-emerald-800 ring-emerald-600/20 dark:bg-emerald-500/12 dark:text-emerald-300 dark:ring-emerald-400/30";
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
  const outlook = strategyOutlook(trade.strategy_id);

  const pop = useMemo(
    () => computeTradePop(trade, spot, atmIv, expiryDate, lotSize),
    [trade, spot, atmIv, expiryDate, lotSize],
  );

  const prem = trade.net_premium;
  const premLabel =
    prem != null ? formatIndianMoneyCompact(prem) : "—";
  const roiLabel =
    trade.annualized_return_pct != null
      ? `${trade.annualized_return_pct.toFixed(1)}%`
      : "—";
  const rrLabel = formatRiskRewardRatio(
    trade.risk_reward_ratio,
    trade.max_loss,
  );

  const vBar = (
    <span
      className="mx-1.5 h-4 w-px shrink-0 self-center bg-zinc-200 dark:bg-zinc-600"
      aria-hidden
    />
  );

  return (
    <div
      className={`group w-fit max-w-full min-w-0 rounded-lg border p-2.5 shadow-sm backdrop-blur-sm transition hover:shadow-md ${
        skipped
          ? "border-zinc-200/60 bg-zinc-100/50 opacity-70 dark:border-zinc-700/60 dark:bg-zinc-900/40"
          : selected
            ? "border-sky-500 ring-2 ring-sky-500/40 bg-white/95 dark:bg-zinc-950/70"
            : "border-zinc-200/80 bg-white/90 ring-1 ring-zinc-950/[0.03] hover:border-zinc-300 dark:border-zinc-700/80 dark:bg-zinc-950/60 dark:ring-white/[0.04] dark:hover:border-zinc-600"
      }`}
    >
      <div className="space-y-2">
        <div className="relative flex min-w-0 items-start gap-2 pr-[min(100%,8rem)]">
          <div className="flex min-w-0 flex-1 items-center gap-1.5 overflow-hidden text-sm font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
            {outlook ? <OutlookIcon outlook={outlook} /> : null}
            <span className="truncate">{trade.strategy_name}</span>
            {trade.structure_modified ? (
              <span className="shrink-0 rounded-full bg-amber-500/15 px-2 py-0.5 text-[10px] font-medium text-amber-800 dark:text-amber-200">
                Modified
              </span>
            ) : null}
          </div>
          {!skipped && prem != null ? (
            <div
              className={`absolute right-0 top-0 shrink-0 rounded-full px-2.5 py-1 text-xs font-semibold tabular-nums ring-1 ${premiumCapsuleClass(prem)}`}
              title="Net premium (annualised ROI)"
            >
              <span className={moneyToneClass(prem)}>{premLabel}</span>
              {trade.annualized_return_pct != null ? (
                <span className="font-normal opacity-90"> ({roiLabel})</span>
              ) : null}
            </div>
          ) : null}
        </div>

        {skipped ? (
          <p className="text-xs text-zinc-500 dark:text-zinc-400">
            {trade.skip_reason ?? "Not available"}
          </p>
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-zinc-600 dark:text-zinc-300">
              <span className="inline-flex items-center gap-1">
                Max loss:{" "}
                <strong className="inline-flex items-center tabular-nums">
                  {isUnlimitedMaxLoss(trade.max_loss) ? (
                    <InfinitySymbol />
                  ) : (
                    formatIndianMoneyCompact(trade.max_loss!)
                  )}
                </strong>
              </span>
              <span className="inline-flex items-center gap-1">
                R:R{" "}
                <strong className="inline-flex items-center tabular-nums">
                  {rrLabel === "∞" ? <InfinitySymbol /> : rrLabel}
                </strong>
              </span>
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
                      <span>B:S {ratioStr}</span>
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
