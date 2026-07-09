"use client";

import { useId, useMemo } from "react";
import { InfinitySymbol } from "@/components/shared/payoff/InfinitySymbol";
import { PayoffChart } from "@/components/shared/payoff/PayoffChart";
import { formatIndianMoneyCompact } from "@/lib/format-money-in";
import { expiryDisplayToYears } from "@/lib/strategy-builder/expiry";
import {
  estimateProbabilityOfProfit,
  payoffChartSpotDomain,
  portfolioGreeks,
  scanMarkToModelCurve,
  scanPayoffCurve,
  summarizePayoffExact,
} from "@/lib/strategy-builder/payoff";
import {
  isUnlimitedMaxLoss,
  isUnlimitedMaxProfit,
} from "@/lib/strategy-builder/trade-metrics";
import type { StrategyLeg } from "@/lib/strategy-builder/types";

function ToggleSwitch({
  checked,
  label,
  onChange,
}: {
  checked: boolean;
  label: string;
  onChange: (v: boolean) => void;
}) {
  return (
    <label className="inline-flex cursor-pointer items-center gap-2 text-xs font-medium text-muted">
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={label}
        onClick={() => onChange(!checked)}
        className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition ${
          checked ? "bg-accent-strong" : "bg-border"
        }`}
      >
        <span
          className={`inline-block size-5 transform rounded-full bg-white shadow transition ${
            checked ? "translate-x-[22px]" : "translate-x-0.5"
          }`}
        />
      </button>
      {label}
    </label>
  );
}

function IvShockMiniSlider({
  value,
  onChange,
}: {
  value: number;
  onChange: (v: number) => void;
}) {
  const min = -20;
  const max = 20;
  const pct = ((value - min) / (max - min)) * 100;
  return (
    <label className="block min-w-0 max-w-xs">
      <div className="mb-1 flex items-center justify-between gap-2 text-hint font-medium text-muted">
        <span>IV shock</span>
        <span className="font-mono tabular-nums text-foreground">
          {value === 0 ? "0%" : `${value > 0 ? "+" : ""}${value}%`}
        </span>
      </div>
      <div className="relative">
        <div className="pointer-events-none absolute top-1/2 left-0 h-1 w-full -translate-y-1/2 rounded-full bg-track" />
        <div
          className="pointer-events-none absolute top-1/2 left-0 h-1 -translate-y-1/2 rounded-full bg-accent-strong"
          style={{ width: `${pct}%` }}
        />
        <input
          type="range"
          min={min}
          max={max}
          value={value}
          onChange={(e) => onChange(Number(e.target.value))}
          className="sb-range-slim sb-range-xs relative z-0 block w-full"
          aria-label="Implied volatility shock percent"
        />
      </div>
    </label>
  );
}

function StatCell({
  label,
  value,
  tone = "foreground",
}: {
  label: string;
  value: React.ReactNode;
  tone?: "up" | "down" | "accent" | "foreground";
}) {
  const toneClass =
    tone === "up"
      ? "text-up"
      : tone === "down"
        ? "text-down"
        : tone === "accent"
          ? "text-accent-strong"
          : "text-foreground";
  return (
    <div className="bg-panel px-4 py-3">
      <div className="text-micro font-bold uppercase tracking-[.06em] text-faint">
        {label}
      </div>
      <div className={`mt-1 font-mono text-[17px] font-bold tabular-nums sm:text-[19px] ${toneClass}`}>
        {value}
      </div>
    </div>
  );
}

export function BasketPayoffPanel({
  sectionLabel,
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
}: {
  sectionLabel: string;
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
    <div>
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border-soft px-[18px] py-3.5">
        <span className="text-hint font-bold uppercase tracking-[.07em] text-faint">
          {sectionLabel}
        </span>
        <div className="flex flex-wrap items-center gap-4">
          <ToggleSwitch
            checked={showToday}
            label="Show today's curve"
            onChange={onShowTodayChange}
          />
          <ToggleSwitch
            checked={showGreeks}
            label="Show Greeks"
            onChange={onShowGreeksChange}
          />
        </div>
      </div>

      <div className="grid grid-cols-2 gap-px bg-border-soft sm:grid-cols-4">
        <StatCell
          label="Max profit"
          tone="up"
          value={
            hasStrategyLegs ? (
              isUnlimitedMaxProfit(summary.maxProfit) ? (
                <InfinitySymbol />
              ) : (
                formatIndianMoneyCompact(summary.maxProfit)
              )
            ) : (
              "—"
            )
          }
        />
        <StatCell
          label="Max loss"
          tone="down"
          value={
            hasStrategyLegs ? (
              isUnlimitedMaxLoss(summary.maxLoss) ? (
                <InfinitySymbol />
              ) : (
                formatIndianMoneyCompact(summary.maxLoss)
              )
            ) : (
              "—"
            )
          }
        />
        <StatCell
          label="Breakevens"
          value={
            hasStrategyLegs && summary.breakevens.length
              ? summary.breakevens.map((b) => b.toFixed(0)).join(" · ")
              : "—"
          }
        />
        <StatCell
          label="Probability of profit"
          tone="accent"
          value={legs.length ? `${pop.toFixed(1)}%` : "—"}
        />
      </div>

      <div className="space-y-4 p-[18px]">
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
        <div className="flex flex-wrap gap-4 text-hint text-muted">
          <span className="flex items-center gap-[5px]">
            <span className="inline-block h-[2px] w-3.5 bg-up" aria-hidden />
            At expiry
          </span>
          {showToday ? (
            <span className="flex items-center gap-[5px]">
              <span className="inline-block h-[2px] w-3.5 bg-accent/70" aria-hidden />
              Today (mark-to-model)
            </span>
          ) : null}
        </div>

        {hasStrategyLegs ? (
          <IvShockMiniSlider value={ivShockPct} onChange={onIvShockChange} />
        ) : null}

        {showGreeks && hasStrategyLegs ? (
          <div className="flex flex-wrap gap-x-6 gap-y-2 border-t border-border-soft pt-3 text-xs">
            <span className="text-muted">
              Delta{" "}
              <span className="font-mono font-semibold tabular-nums text-foreground">
                {greeks.delta.toFixed(4)}
              </span>
            </span>
            <span className="text-muted">
              Gamma{" "}
              <span className="font-mono font-semibold tabular-nums text-foreground">
                {greeks.gamma.toFixed(6)}
              </span>
            </span>
            <span className="text-muted">
              Vega{" "}
              <span className="font-mono font-semibold tabular-nums text-foreground">
                {greeks.vega.toFixed(4)}
              </span>
            </span>
            <span className="text-muted">
              Theta / day{" "}
              <span className="font-mono font-semibold tabular-nums text-foreground">
                {greeks.thetaPerDay.toFixed(4)}
              </span>
            </span>
          </div>
        ) : null}
      </div>
    </div>
  );
}
