"use client";

import { useId, useMemo, useState } from "react";
import { InfinitySymbol } from "@/components/shared/payoff/InfinitySymbol";
import { PayoffChart } from "@/components/shared/payoff/PayoffChart";
import { PayoffScenarioControls } from "@/components/shared/payoff/PayoffScenarioControls";
import { blendedSigmaForLegs, sigmaForLeg, type SigmaSmiles } from "@/lib/strategy-builder/chainIv";
import { expiryDisplayToYears } from "@/lib/strategy-builder/expiry";
import {
  estimateProbabilityOfProfit,
  PAYOFF_CHART_SPOT_HALFBAND,
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

/** Default view shows at least ±10% around spot; widens to fit any leg strike further out. */
const DEFAULT_VIEW_HALFBAND = 0.1;
const PAYOFF_STEPS = 401;
const profitClass = "text-up";
const lossClass = "text-down";

/** Payoff panel styled to match PortfolioGroupPayoffPanel: compact chart, What-if T+0 scenario controls, inline stat line. */
export function StrategyPayoffPanel({
  sectionTitle = "5. Payoff Simulation",
  legs,
  spot,
  atmIv,
  sigmaSmiles = null,
  expiryDate,
  lotSize,
}: {
  sectionTitle?: string;
  legs: StrategyLeg[];
  spot: number | null;
  atmIv: number | null;
  /** Per-strike IV smile (skew-aware); falls back to flat `atmIv` where a strike lacks trusted anchors. */
  sigmaSmiles?: SigmaSmiles | null;
  expiryDate: string;
  lotSize: number;
}) {
  /** null = tracking the live/actual DTE for this expiry. */
  const [dteOverrideDays, setDteOverrideDays] = useState<number | null>(null);
  const [ivShockPct, setIvShockPct] = useState(0);
  const [showGreeks, setShowGreeks] = useState(false);

  const T = useMemo(() => expiryDisplayToYears(expiryDate), [expiryDate]);
  const sigma = atmIv != null && atmIv > 0 ? atmIv : 0.2;

  const liveDteDays = Math.max(0, Math.round(T * 365));
  const dteDays = dteOverrideDays ?? liveDteDays;
  const tEffective = dteDays / 365;

  const { minS, maxS } = useMemo(
    () => (spot != null ? payoffChartSpotDomain(spot) : { minS: 0, maxS: 1 }),
    [spot],
  );
  const hasStrategyLegs = legs.some((l) => l.lots > 0);

  const legStrikes = useMemo(
    () =>
      Array.from(
        new Set(
          legs
            .filter((l) => l.lots > 0)
            .map((l) => l.strike)
            .filter((k): k is number => Number.isFinite(k)),
        ),
      ).sort((a, b) => a - b),
    [legs],
  );

  /** Widens past ±10% only far enough to keep every leg strike on-screen by default. */
  const defaultSpanFraction = useMemo(() => {
    if (spot == null || !Number.isFinite(spot) || spot <= 0) return 0.25;
    const maxStrikeDistFrac = legStrikes.reduce(
      (acc, k) => Math.max(acc, Math.abs(k - spot) / spot),
      0,
    );
    const desiredHalfBand = Math.max(
      DEFAULT_VIEW_HALFBAND,
      maxStrikeDistFrac * 1.15,
    );
    return Math.min(1, Math.max(0.12, desiredHalfBand / PAYOFF_CHART_SPOT_HALFBAND));
  }, [spot, legStrikes]);

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
    const { xs: x1, ys: y1 } = scanPayoffCurve(minS, maxS, PAYOFF_STEPS, L, lotSize);
    const { xs: x2, ys: y2 } = scanMarkToModelCurve(
      minS,
      maxS,
      PAYOFF_STEPS,
      L,
      lotSize,
      tEffective,
      (leg) => sigmaForLeg(sigmaSmiles, leg, spot, sigma) * (1 + ivShockPct / 100),
    );
    const sum = summarizePayoffExact(L, lotSize, spot);
    return { xs: x1, ys: y1, summary: sum, xsToday: x2, ysToday: y2 };
  }, [legs, minS, maxS, spot, lotSize, tEffective, sigma, sigmaSmiles, ivShockPct]);

  const pop = useMemo(() => {
    if (spot == null || !legs.length) return 0;
    return estimateProbabilityOfProfit(
      spot,
      T,
      blendedSigmaForLegs(sigmaSmiles, legs, spot, lotSize, sigma),
      legs,
      lotSize,
    );
  }, [spot, T, sigma, sigmaSmiles, legs, lotSize]);

  const greeks = useMemo(() => {
    if (spot == null || tEffective <= 0 || !legs.length) {
      return { delta: 0, gamma: 0, vega: 0, thetaPerDay: 0 };
    }
    return portfolioGreeks(
      spot,
      legs,
      lotSize,
      tEffective,
      (leg) => sigmaForLeg(sigmaSmiles, leg, spot, sigma) * (1 + ivShockPct / 100),
    );
  }, [spot, tEffective, sigma, sigmaSmiles, ivShockPct, legs, lotSize]);

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
    <div className="space-y-4">
      <h2 id={payoffTitleId} className={sb.sectionTitle}>
        {sectionTitle}
      </h2>
      <p id={payoffDescId} className="sr-only">
        {payoffDescription}
      </p>

      <PayoffChart
        key={`${minS}-${maxS}`}
        idle={!hasStrategyLegs}
        xs={xs}
        ys={ys}
        xsToday={xsToday}
        ysToday={ysToday}
        spot={spot}
        breakevens={summary.breakevens}
        strikes={legStrikes}
        minS={minS}
        maxS={maxS}
        height={240}
        defaultSpanFraction={defaultSpanFraction}
        compact
        labelledBy={payoffTitleId}
        describedBy={payoffDescId}
      />

      {hasStrategyLegs ? (
        <PayoffScenarioControls
          dteDays={dteDays}
          liveDteDays={liveDteDays}
          onDteChange={setDteOverrideDays}
          ivShockPct={ivShockPct}
          onIvShockChange={setIvShockPct}
          onReset={() => {
            setDteOverrideDays(null);
            setIvShockPct(0);
          }}
        />
      ) : null}

      <div className="flex flex-wrap items-center gap-x-5 gap-y-1.5 text-xs">
        <span className="text-muted">
          Max profit{" "}
          <span className={`font-mono font-semibold tabular-nums ${profitClass}`}>
            {hasStrategyLegs ? (
              isUnlimitedMaxProfit(summary.maxProfit) ? (
                <InfinitySymbol />
              ) : (
                formatIndianMoneyCompact(summary.maxProfit)
              )
            ) : (
              "—"
            )}
          </span>
        </span>
        <span className="text-muted">
          Max loss{" "}
          <span className={`font-mono font-semibold tabular-nums ${lossClass}`}>
            {hasStrategyLegs ? (
              isUnlimitedMaxLoss(summary.maxLoss) ? (
                <InfinitySymbol />
              ) : (
                formatIndianMoneyCompact(summary.maxLoss)
              )
            ) : (
              "—"
            )}
          </span>
        </span>
        <span className="text-muted">
          Breakevens{" "}
          <span className="font-mono font-semibold tabular-nums text-foreground">
            {hasStrategyLegs && summary.breakevens.length
              ? summary.breakevens.map((b) => b.toFixed(0)).join(" · ")
              : "—"}
          </span>
        </span>
        <span className="text-muted">
          PoP{" "}
          <span className="font-mono font-semibold tabular-nums text-foreground">
            {hasStrategyLegs ? `${pop.toFixed(1)}%` : "—"}
          </span>
        </span>
      </div>

      <div className="space-y-3 border-t border-border-soft pt-3">
        <div className="flex items-center justify-between gap-3">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-faint">
            Greeks
          </h3>
          <button
            type="button"
            role="switch"
            aria-checked={showGreeks}
            aria-label="Toggle Show Greeks"
            onClick={() => setShowGreeks((v) => !v)}
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
        </div>
        {showGreeks && hasStrategyLegs ? (
          <div className="flex flex-wrap gap-x-6 gap-y-2 text-xs">
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
