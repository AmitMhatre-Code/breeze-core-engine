"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { PayoffChart } from "@/components/shared/payoff/PayoffChart";
import { InfinitySymbol } from "@/components/shared/payoff/InfinitySymbol";
import { PayoffScenarioControls } from "@/components/shared/payoff/PayoffScenarioControls";
import { formatIndianMoneyCompact } from "@/lib/format-money-in";
import type { PortfolioPositionRecord } from "@/lib/portfolio";
import { chainLotSize, rowsToStrategyLegs } from "@/lib/portfolio/legsFromRows";
import { atmSigmaFromChain } from "@/lib/strategy-builder/chainIv";
import { payoffQuoteQueryOptions } from "@/lib/strategy-builder/chain-query";
import { usePnlRecomputeRefetchMs } from "@/lib/portfolio/usePnlRecomputeRefetchMs";
import { expiryDisplayToYears } from "@/lib/strategy-builder/expiry";
import {
  estimateProbabilityOfProfit,
  PAYOFF_CHART_SPOT_HALFBAND,
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
import type { StrategyLeg } from "@/lib/strategy-builder/types";

/** Default view shows at least ±10% around spot; widens to fit any strike further out. */
const DEFAULT_VIEW_HALFBAND = 0.1;

type Props = {
  stockCode: string;
  exchangeCode: string;
  expiryDisplay: string;
  rows: PortfolioPositionRecord[];
  /** Proposed protective wing to overlay on payoff (hedge preview). */
  proposedLeg?: StrategyLeg | null;
  /**
   * Stable per-group WS feed subscription id (see `useGroupSubscriptionHolders`).
   * Rendered only while the group is expanded, so this keeps the chain "hot"
   * server-side and polls at the user's P&L recalc interval (see
   * `usePnlRecomputeRefetchMs`) for as long as it stays open.
   */
  holderId: string;
};

const profitClass = "text-up";
const lossClass = "text-down";
const PAYOFF_STEPS = 401;

/** Payoff + POP for one underlying/expiry bucket (chain fetch for lot size, spot, IV, live LTP). */
export function PortfolioGroupPayoffPanel({
  stockCode,
  exchangeCode,
  expiryDisplay,
  rows,
  proposedLeg = null,
  holderId,
}: Props) {
  /** null = tracking the live/actual DTE for this expiry. */
  const [dteOverrideDays, setDteOverrideDays] = useState<number | null>(null);
  const [ivShockPct, setIvShockPct] = useState(0);

  const refetchIntervalMs = usePnlRecomputeRefetchMs();
  const cq = useQuery({
    ...payoffQuoteQueryOptions({
      queryKeyPrefix: ["portfolio", "group-payoff-chain"],
      stock_code: stockCode,
      expiry_date: expiryDisplay,
      exchange_code: exchangeCode,
      subscription_holder: holderId,
      refetchIntervalMs,
    }),
    enabled: Boolean(stockCode && expiryDisplay && rows.length > 0),
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

  const spot = chainSuccess?.spot_price ?? null;
  const T = expiryDisplayToYears(expiryDisplay || "01-Jan-2099");
  const sigma = chainSuccess ? atmSigmaFromChain(chainSuccess, T) : 0.22;

  const liveDteDays = Math.max(0, Math.round(T * 365));
  const dteDays = dteOverrideDays ?? liveDteDays;
  const tEffective = dteDays / 365;
  const sigmaEffective = sigma * (1 + ivShockPct / 100);

  const legStrikes = useMemo(
    () =>
      Array.from(
        new Set(
          legs
            .map((l) => l.strike)
            .filter((k): k is number => Number.isFinite(k)),
        ),
      ).sort((a, b) => a - b),
    [legs],
  );

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

  const { xs, ys, summary, pop, xsToday, ysToday } = useMemo(() => {
    const steps = PAYOFF_STEPS;
    if (!legs.length) {
      return {
        xs: [] as number[],
        ys: [] as number[],
        summary: { maxProfit: 0, maxLoss: 0, breakevens: [] } as PayoffSummary,
        pop: 0,
        xsToday: [] as number[],
        ysToday: [] as number[],
      };
    }
    const { xs: x1, ys: y1 } = scanPayoffCurve(
      minS,
      maxS,
      steps,
      legs,
      lotSize,
    );
    const { xs: x2, ys: y2 } = scanMarkToModelCurve(
      minS,
      maxS,
      steps,
      legs,
      lotSize,
      tEffective,
      sigmaEffective,
    );
    const exactSummary = summarizePayoffExact(legs, lotSize, spot);
    const popVal =
      spot != null ? estimateProbabilityOfProfit(spot, T, sigma, legs, lotSize) : 0;
    return {
      xs: x1,
      ys: y1,
      summary: exactSummary,
      pop: popVal,
      xsToday: x2,
      ysToday: y2,
    };
  }, [legs, minS, maxS, spot, lotSize, T, sigma, tEffective, sigmaEffective]);

  const hasLegs = legs.length > 0;

  if (cq.isLoading) {
    return <div className="p-3 text-sm app-text-muted">Loading payoff…</div>;
  }

  if (cq.isError) {
    return (
      <div className="p-3 text-sm text-down">
        {cq.error instanceof Error
          ? cq.error.message
          : "Unable to load chain for payoff."}
      </div>
    );
  }

  if (cq.data && cq.data.Status !== 200) {
    return (
      <div className="p-3 text-sm text-down">
        {String(cq.data.Error ?? "Chain request failed.")}
      </div>
    );
  }

  return (
    <div className="min-w-0 p-3">
      <h3 className="mb-2 text-heading font-semibold uppercase tracking-wide text-faint">
        Group payoff
      </h3>
      <PayoffChart
        key={`${stockCode}-${expiryDisplay}-${minS}-${maxS}-${proposedLeg?.id ?? "none"}`}
        idle={!hasLegs}
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
      />
      {hasLegs ? (
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
      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
        <span className="text-muted">
          Max profit{" "}
          <span className={`font-semibold tabular-nums ${profitClass}`}>
            {hasLegs ? (
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
          <span className={`font-semibold tabular-nums ${lossClass}`}>
            {hasLegs ? (
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
          <span className="font-semibold tabular-nums text-foreground">
            {hasLegs && summary.breakevens.length
              ? summary.breakevens.map((b) => b.toFixed(0)).join(" · ")
              : "—"}
          </span>
        </span>
        <span className="text-muted">
          PoP{" "}
          <span className="font-semibold tabular-nums text-foreground">
            {hasLegs ? `${pop.toFixed(1)}%` : "—"}
          </span>
        </span>
      </div>
      {hasProposedHedge ? (
        <p className="mt-2 text-heading font-medium leading-relaxed text-accent-strong">
          Includes proposed hedge leg (Buy {proposedLeg!.right}{" "}
          {proposedLeg!.strike}).
        </p>
      ) : null}
    </div>
  );
}
