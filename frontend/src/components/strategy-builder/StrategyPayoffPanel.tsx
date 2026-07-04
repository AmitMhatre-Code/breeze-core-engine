"use client";

import { useId, useMemo } from "react";
import { InfinitySymbol } from "@/components/strategy-builder/InfinitySymbol";
import { IvShockSlider } from "@/components/strategy-builder/IvShockSlider";
import { MarginRefreshIconButton } from "@/components/strategy-builder/MarginRefreshIconButton";
import { PayoffChart } from "@/components/strategy-builder/PayoffChart";
import { expiryDisplayToYears } from "@/lib/strategy-builder/expiry";
import {
  estimateProbabilityOfProfit,
  payoffChartSpotDomain,
  portfolioGreeks,
  scanMarkToModelCurve,
  scanPayoffCurve,
  summarizePayoffExact,
} from "@/lib/strategy-builder/payoff";
import { sb } from "@/lib/strategy-builder/ui";
import type { StrategyLeg } from "@/lib/strategy-builder/types";
import {
  isUnlimitedMaxLoss,
  isUnlimitedMaxProfit,
} from "@/lib/strategy-builder/trade-metrics";
import { formatIndianMoneyCompact } from "@/lib/format-money-in";

export function StrategyPayoffPanel({
  sectionTitle = "5. Payoff Simulation",
  legs,
  spot,
  atmIv,
  expiryDate,
  lotSize,
  ivShockPct,
  onIvShockChange,
  showToday,
  onShowTodayChange,
  showGreeks,
  onShowGreeksChange,
  spanMargin,
  marginFetching,
  marginQtyStale,
  onRefreshMargin,
  marginError,
  marginWarnings,
}: {
  sectionTitle?: string;
  legs: StrategyLeg[];
  spot: number | null;
  atmIv: number | null;
  expiryDate: string;
  lotSize: number;
  ivShockPct: number;
  onIvShockChange: (v: number) => void;
  showToday: boolean;
  onShowTodayChange: (v: boolean) => void;
  showGreeks: boolean;
  onShowGreeksChange: (v: boolean) => void;
  spanMargin: number | null;
  marginFetching: boolean;
  marginQtyStale: boolean;
  onRefreshMargin: () => void;
  marginError?: string | null;
  marginWarnings?: string[];
}) {
  const T = useMemo(() => expiryDisplayToYears(expiryDate), [expiryDate]);
  const baseSigma = atmIv != null && atmIv > 0 ? atmIv : 0.2;
  const sigma = baseSigma * (1 + ivShockPct / 100);
  const { minS, maxS } = useMemo(
    () => (spot != null ? payoffChartSpotDomain(spot) : { minS: 0, maxS: 1 }),
    [spot],
  );
  const steps = 80;
  const hasStrategyLegs = legs.some((l) => l.lots > 0);

  const { xs, ys, summary, xsToday, ysToday } = useMemo(() => {
    const L = legs.filter((l) => l.lots > 0);
    if (!L.length || spot == null) {
      return {
        xs: [] as number[],
        ys: [] as number[],
        summary: { maxProfit: 0, maxLoss: 0, breakevens: [] as number[] },
        xsToday: [] as number[],
        ysToday: [] as number[],
      };
    }
    const { xs: x1, ys: y1 } = scanPayoffCurve(minS, maxS, steps, L, lotSize);
    const sum = summarizePayoffExact(L, lotSize, spot);
    let xt: number[] = [];
    let yt: number[] = [];
    if (showToday && T > 0) {
      const r = scanMarkToModelCurve(minS, maxS, steps, L, lotSize, T, sigma);
      xt = r.xs;
      yt = r.ys;
    }
    return { xs: x1, ys: y1, summary: sum, xsToday: xt, ysToday: yt };
  }, [legs, minS, maxS, spot, sigma, T, lotSize, showToday]);

  const pop = useMemo(() => {
    if (spot == null || !legs.length) return 0;
    return estimateProbabilityOfProfit(spot, T, sigma, legs, lotSize);
  }, [spot, T, sigma, legs, lotSize]);

  const greeks = useMemo(() => {
    if (spot == null || T <= 0 || !legs.length) {
      return { delta: 0, gamma: 0, vega: 0, thetaPerDay: 0 };
    }
    return portfolioGreeks(spot, legs, lotSize, T, sigma);
  }, [spot, T, sigma, legs, lotSize]);

  const profitClass = "text-up";
  const lossClass = "text-down";
  const payoffTitleId = useId();
  const payoffDescId = useId();

  const payoffDescription = useMemo(() => {
    if (!hasStrategyLegs) {
      return "Payoff chart idle. Add legs to see max profit, max loss, and breakevens.";
    }
    const be =
      summary.breakevens.length > 0
        ? summary.breakevens.map((b) => b.toFixed(0)).join(", ")
        : "none";
    const maxP = isUnlimitedMaxProfit(summary.maxProfit)
      ? "unlimited"
      : formatIndianMoneyCompact(summary.maxProfit);
    const maxL = isUnlimitedMaxLoss(summary.maxLoss)
      ? "unlimited"
      : formatIndianMoneyCompact(summary.maxLoss);
    return `Max profit ${maxP}, max loss ${maxL}, breakevens at ${be}, probability of profit ${pop.toFixed(1)} percent.`;
  }, [hasStrategyLegs, summary, pop]);

  return (
    <section className={`${sb.section} space-y-5`}>
      <h2 className={sb.sectionTitle}>{sectionTitle}</h2>
      <div className="sticky top-0 z-10 -mx-0.5 py-1">
        <div
          className={`${sb.stickyBar} flex flex-wrap gap-x-6 gap-y-3 text-xs`}
        >
          <div>
            <div className="text-[13px] font-semibold uppercase tracking-[.06em] text-faint">
              Max profit
            </div>
            <div className={`font-semibold tabular-nums ${profitClass}`}>
              {hasStrategyLegs ? (
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
            <div className="text-[13px] font-semibold uppercase tracking-[.06em] text-faint">
              Max loss
            </div>
            <div className={`font-semibold tabular-nums ${lossClass}`}>
              {hasStrategyLegs ? (
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
            <div className="text-[13px] font-semibold uppercase tracking-[.06em] text-faint">
              Breakevens
            </div>
            <div className="font-medium text-foreground">
              {hasStrategyLegs && summary.breakevens.length
                ? summary.breakevens.map((b) => b.toFixed(0)).join(", ")
                : "—"}
            </div>
          </div>
          <div>
            <div className="text-[13px] font-semibold uppercase tracking-[.06em] text-faint">
              POP (model)
            </div>
            <div className="font-medium tabular-nums text-foreground">
              {legs.length ? `${pop.toFixed(1)}%` : "—"}
            </div>
          </div>
          <div>
            <div className="text-[13px] font-semibold uppercase tracking-[.06em] text-faint">
              Margin (SPAN)
            </div>
            <div className="font-medium tabular-nums text-foreground">
              {marginFetching ? (
                "…"
              ) : marginQtyStale ? (
                <MarginRefreshIconButton
                  label="Refresh margin (SPAN)"
                  onClick={onRefreshMargin}
                />
              ) : spanMargin != null && Number.isFinite(spanMargin) ? (
                formatIndianMoneyCompact(spanMargin)
              ) : (
                marginError ?? "—"
              )}
            </div>
            {marginWarnings && marginWarnings.length > 0 ? (
              <div className="mt-1 app-alert-error text-[13px]">
                {marginWarnings[0]}
              </div>
            ) : null}
          </div>
          <div>
            <div className="text-[13px] font-semibold uppercase tracking-[.06em] text-faint">
              Spot / IV
            </div>
            <div className="font-medium tabular-nums text-foreground">
              {spot != null ? (
                <>
                  {spot.toFixed(2)} · {(sigma * 100).toFixed(1)}%
                </>
              ) : (
                "—"
              )}
            </div>
          </div>
        </div>
      </div>

      <div className="space-y-4 border-t border-border-soft pt-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h3
            id={payoffTitleId}
            className="text-xs font-semibold uppercase tracking-wide text-faint"
          >
            Payoff chart
          </h3>
          <label className="inline-flex cursor-pointer items-center gap-2 text-xs font-medium text-muted">
            <input
              type="checkbox"
              className="peer sr-only"
              checked={showToday}
              onChange={(e) => onShowTodayChange(e.target.checked)}
            />
            <span className="relative h-6 w-11 rounded-full bg-border transition-colors duration-200 after:absolute after:left-0.5 after:top-0.5 after:size-5 after:rounded-full after:bg-white after:shadow after:transition-transform after:duration-200 peer-checked:bg-accent-strong peer-checked:after:translate-x-[22px] peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-accent" />
            <span>Show today (model)</span>
          </label>
        </div>
        <p id={payoffDescId} className="sr-only">
          {payoffDescription}
        </p>
        <PayoffChart
          key={`${minS}-${maxS}`}
          idle={!hasStrategyLegs}
          xs={xs}
          ys={ys}
          xsToday={showToday ? xsToday : undefined}
          ysToday={showToday ? ysToday : undefined}
          spot={spot}
          breakevens={summary.breakevens}
          minS={minS}
          maxS={maxS}
          labelledBy={payoffTitleId}
          describedBy={payoffDescId}
        />
      </div>

      <div className="space-y-4 border-t border-border-soft pt-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-faint">
              Greeks &amp; IV shock
            </h3>
          </div>
          <div
            className={`${sb.checkboxRow} shrink-0 self-start gap-2 text-xs font-medium leading-snug text-muted`}
          >
            <button
              type="button"
              role="switch"
              aria-checked={showGreeks}
              aria-label="Toggle Show Greeks"
              onClick={() => onShowGreeksChange(!showGreeks)}
              className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition ${
                showGreeks ? "bg-accent-strong" : "bg-border"
              }`}
            >
              <span
                className={`inline-block size-5 transform rounded-full bg-white shadow transition ${
                  showGreeks ? "translate-x-[22px]" : "translate-x-0.5"
                }`}
              />
            </button>
            Show Greeks
          </div>
        </div>
        <IvShockSlider value={ivShockPct} onChange={onIvShockChange} />
        {showGreeks && legs.length ? (
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {(
              [
                { key: "delta", label: "Delta", fmt: greeks.delta.toFixed(4) },
                { key: "gamma", label: "Gamma", fmt: greeks.gamma.toFixed(6) },
                { key: "vega", label: "Vega", fmt: greeks.vega.toFixed(4) },
                {
                  key: "theta",
                  label: "Theta / day",
                  fmt: greeks.thetaPerDay.toFixed(4),
                },
              ] as const
            ).map((g) => (
              <div
                key={g.key}
                className="rounded-[9px] border border-border bg-panel2 px-3 py-2.5"
              >
                <div className="text-[12px] font-semibold uppercase tracking-wide text-faint">
                  {g.label}
                </div>
                <div className="mt-1 font-mono text-sm font-medium tabular-nums tracking-tight text-foreground">
                  {g.fmt}
                </div>
              </div>
            ))}
          </div>
        ) : null}
      </div>
    </section>
  );
}
