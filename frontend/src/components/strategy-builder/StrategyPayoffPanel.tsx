"use client";

import { useMemo } from "react";
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

  const profitClass = "text-emerald-700 dark:text-emerald-400";
  const lossClass = "text-red-700 dark:text-red-400";

  return (
    <section className={`${sb.section} space-y-5`}>
      <h2 className={sb.sectionTitle}>{sectionTitle}</h2>
      <div className="sticky top-0 z-10 -mx-0.5 py-1">
        <div
          className={`${sb.stickyBar} flex flex-wrap gap-x-6 gap-y-3 text-xs`}
        >
          <div>
            <div className="text-[11px] font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
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
            <div className="text-[11px] font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
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
            <div className="text-[11px] font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
              Breakevens
            </div>
            <div className="font-medium text-zinc-800 dark:text-zinc-200">
              {hasStrategyLegs && summary.breakevens.length
                ? summary.breakevens.map((b) => b.toFixed(0)).join(", ")
                : "—"}
            </div>
          </div>
          <div>
            <div className="text-[11px] font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
              POP (model)
            </div>
            <div className="font-medium tabular-nums text-zinc-800 dark:text-zinc-200">
              {legs.length ? `${pop.toFixed(1)}%` : "—"}
            </div>
          </div>
          <div>
            <div className="text-[11px] font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
              Margin (SPAN)
            </div>
            <div className="font-medium tabular-nums text-zinc-800 dark:text-zinc-200">
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
              <div className="mt-1 app-alert-error text-[11px]">
                {marginWarnings[0]}
              </div>
            ) : null}
          </div>
          <div>
            <div className="text-[11px] font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
              Spot / IV
            </div>
            <div className="font-medium tabular-nums text-zinc-800 dark:text-zinc-200">
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

      <div className="space-y-4 border-t border-zinc-200 pt-4 dark:border-zinc-800">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
            Payoff chart
          </h3>
          <label className="inline-flex cursor-pointer items-center gap-2 text-xs font-medium text-zinc-700 dark:text-zinc-300">
            <input
              type="checkbox"
              className="peer sr-only"
              checked={showToday}
              onChange={(e) => onShowTodayChange(e.target.checked)}
            />
            <span className="relative h-5 w-9 rounded-full bg-zinc-300 transition-colors duration-200 after:absolute after:left-0.5 after:top-0.5 after:h-4 after:w-4 after:rounded-full after:bg-white after:shadow-sm after:transition-transform after:duration-200 peer-checked:bg-sky-600 peer-checked:after:translate-x-4 peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-sky-500 dark:bg-zinc-700 dark:peer-checked:bg-sky-500" />
            <span>Show today (model)</span>
          </label>
        </div>
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
        />
      </div>

      <div className="space-y-4 border-t border-zinc-200 pt-4 dark:border-zinc-800">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
              Greeks &amp; IV shock
            </h3>
          </div>
          <div
            className={`${sb.checkboxRow} shrink-0 self-start gap-2 text-xs font-medium leading-snug text-zinc-600 dark:text-zinc-400`}
          >
            <button
              type="button"
              role="switch"
              aria-checked={showGreeks}
              aria-label="Toggle Show Greeks"
              onClick={() => onShowGreeksChange(!showGreeks)}
              className={`relative inline-flex h-5 w-9 items-center rounded-full transition ${
                showGreeks ? "bg-sky-600" : "bg-zinc-300 dark:bg-zinc-700"
              }`}
            >
              <span
                className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition ${
                  showGreeks ? "translate-x-4" : "translate-x-0.5"
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
                className="rounded-md border border-zinc-200/90 bg-gradient-to-b from-white to-zinc-50/90 px-3 py-2.5 shadow-sm ring-1 ring-zinc-950/[0.03] dark:border-zinc-800 dark:from-zinc-900/90 dark:to-zinc-950/70 dark:ring-white/[0.04]"
              >
                <div className="text-[10px] font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                  {g.label}
                </div>
                <div className="mt-1 font-mono text-sm font-medium tabular-nums tracking-tight text-zinc-900 dark:text-zinc-50">
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
