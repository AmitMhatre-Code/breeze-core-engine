"use client";

import { useMemo } from "react";
import { InfinitySymbol } from "@/components/strategy-builder/InfinitySymbol";
import { OutlookIcon } from "@/components/strategy-builder/OutlookIcon";
import { PopHelpTrigger } from "@/components/strategy-builder/PopHelpTrigger";
import { PopLabel } from "@/components/strategy-builder/PopLabel";
import { formatIndianMoneyCompact, moneyToneClass } from "@/lib/format-money-in";
import { isPopMetricLabel } from "@/lib/strategy-builder/pop-help";
import { strategyOutlook } from "@/lib/strategy-builder/templates";
import {
  computeTradePop,
  formatConstraintViolation,
  formatRiskRewardRatio,
  isUnlimitedMaxLoss,
  isUnlimitedMaxProfit,
} from "@/lib/strategy-builder/trade-metrics";
import type { ProposedTrade } from "@/lib/strategy-builder/types";

const BOOK_LAKH = 100_000;

const CONVICTION_LABELS: Record<string, string> = {
  conservative: "Conservative",
  moderate: "Moderate",
  aggressive: "Aggressive",
};

const rowClass =
  "flex flex-nowrap items-center gap-x-2 whitespace-nowrap text-xs";

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

function premiumCapsuleClass(premium: number | null | undefined): string {
  if (premium == null || !Number.isFinite(premium)) {
    return "bg-panel2 text-muted";
  }
  if (premium < 0) {
    return "bg-down-tint text-down";
  }
  return "bg-up-tint text-up";
}

export function ProposedStrategyTradeCard({
  trade,
  lotSize,
  spot,
  atmIv,
  expiryDate,
  selected,
  onSelect,
  minPopPct,
  minAnnReturnPct,
}: {
  trade: ProposedTrade;
  lotSize: number;
  spot: number | null;
  atmIv: number | null;
  expiryDate: string;
  selected: boolean;
  onSelect: () => void;
  minPopPct?: number | null;
  minAnnReturnPct?: number | null;
}) {
  const skipped = trade.status === "skipped";
  const relaxed = trade.compliance === "relaxed";
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
  const convictionLabel = trade.conviction_profile
    ? CONVICTION_LABELS[trade.conviction_profile] ?? trade.conviction_profile
    : null;
  const useHeroMetric = trade.hero_metric != null;
  const secondaryFromApi = trade.secondary_metrics ?? [];
  const hasPopInSecondary = secondaryFromApi.some((m) => isPopMetricLabel(m.label));

  const vBar = (
    <span
      className="mx-1.5 h-4 w-px shrink-0 self-center bg-border-soft"
      aria-hidden
    />
  );

  return (
    <div
      className={`group relative w-full min-w-0 rounded-[13px] border p-2.5 transition ${
        skipped
          ? "border-border-soft bg-panel2 opacity-70"
          : relaxed
            ? selected
              ? "border-amber-accent bg-amber-tint ring-1 ring-amber-accent/30"
              : "border-amber-accent/40 bg-amber-tint/60 hover:border-amber-accent/70"
          : selected
            ? "border-accent bg-accent-tint"
            : "border-border bg-panel hover:border-accent/40"
      }`}
    >
      <div className="space-y-2">
        <div className="flex items-start justify-between gap-2">
          <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-1.5 gap-y-1 text-sm font-semibold tracking-tight text-foreground">
            {outlook ? (
              <span className="shrink-0">
                <OutlookIcon outlook={outlook} />
              </span>
            ) : null}
            <span className="shrink-0 leading-snug">{trade.strategy_name}</span>
            {convictionLabel ? (
              <span className="shrink-0 rounded-full bg-gtt-tint px-2 py-0.5 text-[12px] font-medium text-gtt">
                {convictionLabel}
              </span>
            ) : null}
            {(trade.badges ?? []).map((badge) => (
              <span
                key={badge}
                className="shrink-0 rounded-full bg-up-tint px-2 py-0.5 text-[12px] font-medium text-up"
              >
                {badge}
              </span>
            ))}
            {!convictionLabel &&
            (trade.badges ?? []).length === 0 &&
            trade.variant_rank != null &&
            trade.variant_rank > 0 ? (
              <span className="shrink-0 rounded-full bg-accent-tint px-2 py-0.5 text-[12px] font-medium text-accent-strong">
                #{trade.variant_rank}
              </span>
            ) : null}
            {trade.structure_modified ? (
              <span className="shrink-0 rounded-full bg-amber-tint px-2 py-0.5 text-[12px] font-medium text-amber-accent">
                Modified
              </span>
            ) : null}
            {relaxed && (trade.constraint_violations?.length ?? 0) > 0 ? (
              <span className="shrink-0 rounded-full bg-amber-tint px-2 py-0.5 text-[12px] font-semibold text-amber-accent">
                Below your thresholds
              </span>
            ) : null}
          </div>
          {!skipped && (useHeroMetric || prem != null) ? (
            <span
              className={`shrink-0 self-start rounded-full px-2.5 py-1 font-mono text-xs font-semibold tabular-nums ${
                useHeroMetric
                  ? "bg-accent-tint text-accent-strong"
                  : premiumCapsuleClass(prem)
              }`}
              title={
                useHeroMetric
                  ? trade.hero_metric!.label
                  : "Net premium (annualised ROI)"
              }
            >
              {useHeroMetric ? (
                <>
                  <span className="font-normal opacity-80">
                    {trade.hero_metric!.label}:{" "}
                  </span>
                  <span>{trade.hero_metric!.value}</span>
                </>
              ) : (
                <>
                  <span className={moneyToneClass(prem!)}>{premLabel}</span>
                  {trade.annualized_return_pct != null ? (
                    <span className="font-normal opacity-90">
                      {" "}
                      ({roiLabel})
                    </span>
                  ) : null}
                </>
              )}
            </span>
          ) : null}
        </div>

        {skipped ? (
          <p className="whitespace-nowrap text-xs text-muted">
            {trade.skip_reason ?? "Not available"}
          </p>
        ) : (
          <>
            {trade.ranking_summary ? (
              <p className="text-xs leading-snug text-muted">
                {trade.ranking_summary}
              </p>
            ) : null}
            {relaxed && (trade.constraint_violations?.length ?? 0) > 0 ? (
              <p className="text-xs leading-snug text-amber-accent">
                {(trade.constraint_violations ?? [])
                  .map((v) =>
                    formatConstraintViolation(
                      v,
                      trade,
                      minPopPct,
                      minAnnReturnPct,
                    ),
                  )
                  .join(" · ")}
              </p>
            ) : null}
            {useHeroMetric && secondaryFromApi.length > 0 ? (
              <div
                className={`${rowClass} flex-wrap gap-x-3 gap-y-1 text-muted`}
              >
                {secondaryFromApi.map((metric) => (
                  <span key={metric.label} className="inline-flex shrink-0 items-center gap-1">
                    {isPopMetricLabel(metric.label) ? (
                      <PopLabel variant="metric" showInfo={false} />
                    ) : (
                      <span>{metric.label}</span>
                    )}
                    :{" "}
                    <strong
                      className={`font-mono tabular-nums ${
                        isPopMetricLabel(metric.label)
                          ? "font-normal text-muted"
                          : "text-foreground"
                      }`}
                    >
                      {metric.value}
                    </strong>
                  </span>
                ))}
                {hasPopInSecondary ? <PopHelpTrigger /> : null}
              </div>
            ) : (
            <div
              className={`${rowClass} flex-wrap gap-x-3 gap-y-1 text-muted`}
            >
              {trade.max_profit != null ? (
                <span className="inline-flex shrink-0 items-center gap-1">
                  Max profit:{" "}
                  <strong className="inline-flex items-center font-mono tabular-nums text-up">
                    {isUnlimitedMaxProfit(trade.max_profit) ? (
                      <InfinitySymbol />
                    ) : (
                      formatIndianMoneyCompact(trade.max_profit!)
                    )}
                  </strong>
                </span>
              ) : null}
              <span className="inline-flex shrink-0 items-center gap-1">
                Max loss:{" "}
                <strong className="inline-flex items-center font-mono tabular-nums text-down">
                  {isUnlimitedMaxLoss(trade.max_loss) ? (
                    <InfinitySymbol />
                  ) : (
                    formatIndianMoneyCompact(trade.max_loss!)
                  )}
                </strong>
              </span>
              <span className="inline-flex shrink-0 items-center gap-1">
                R:R{" "}
                <strong className="inline-flex items-center font-mono tabular-nums">
                  {rrLabel === "∞" ? <InfinitySymbol /> : rrLabel}
                </strong>
              </span>
              {pop != null ? (
                <>
                  <span className="inline-flex shrink-0 items-center gap-1">
                    <PopLabel variant="inline" showInfo={false} />
                    :{" "}
                    <strong className="font-mono tabular-nums text-foreground">
                      {pop.toFixed(1)}%
                    </strong>
                  </span>
                  <PopHelpTrigger />
                </>
              ) : null}
              <span className="inline-flex shrink-0 items-center gap-1">
                SPAN:{" "}
                <strong className="font-mono tabular-nums">
                  {trade.span_margin != null
                    ? formatIndianMoneyCompact(trade.span_margin)
                    : "—"}
                </strong>
              </span>
              <span className="inline-flex shrink-0 items-center gap-1">
                ELM:{" "}
                <strong className="font-mono tabular-nums">
                  {trade.elm_requirement != null && trade.elm_requirement > 0
                    ? formatIndianMoneyCompact(trade.elm_requirement)
                    : "—"}
                </strong>
              </span>
            </div>
            )}

            <div className="space-y-1.5 border-t border-border-soft pt-1.5">
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
                    className="rounded border border-border-soft bg-panel2 px-2 py-1.5 text-[13px]"
                  >
                    <div
                      className={`${rowClass} gap-x-2 font-medium text-foreground`}
                    >
                      <span className="shrink-0">
                        {leg.side}{" "}
                        <span className="font-mono tabular-nums">
                          {leg.strike.toLocaleString("en-IN")}
                        </span>{" "}
                        <span className="font-mono">{abbr}</span>
                      </span>
                      {vBar}
                      <span className="shrink-0 font-mono tabular-nums">×{leg.quantity}</span>
                    </div>
                    <div
                      className={`${rowClass} mt-0.5 gap-x-2 text-muted`}
                    >
                      <span className="shrink-0">
                        LTP <span className="font-mono tabular-nums">{fmtPx(leg.ltp)}</span>
                      </span>
                      <span className="shrink-0">
                        Bid{" "}
                        <span className="font-mono tabular-nums">
                          {fmtPx(leg.best_bid_price)}
                        </span>{" "}
                        / Offer{" "}
                        <span className="font-mono tabular-nums">
                          {fmtPx(leg.best_offer_price)}
                        </span>
                      </span>
                      <span className="shrink-0">
                        B:S <span className="font-mono tabular-nums">{ratioStr}</span>
                      </span>
                      <span className="shrink-0">
                        Buy{" "}
                        <span className="font-mono tabular-nums">
                          {formatBookQtyInL(leg.total_buy_qty ?? NaN)}
                        </span>{" "}
                        / Sell{" "}
                        <span className="font-mono tabular-nums">
                          {formatBookQtyInL(leg.total_sell_qty ?? NaN)}
                        </span>
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
                ? "w-full cursor-default rounded-lg bg-accent-strong py-2.5 text-sm font-bold text-accent-ink"
                : "w-full rounded-lg border border-accent-strong bg-transparent py-2.5 text-sm font-bold text-accent-strong transition hover:bg-accent-strong hover:text-accent-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
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
