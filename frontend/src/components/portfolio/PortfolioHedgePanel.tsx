"use client";

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { formatIndianMoneyCompact } from "@/lib/format-money-in";
import {
  postStrategyHedges,
  type StrategyHedgeCandidate,
} from "@/lib/hedge/api";
import { hedgeCandidateKey } from "@/lib/hedge/legs";
import type { PortfolioPositionGroup } from "@/lib/portfolio/groupPositions";
import type { PortfolioPositionRecord } from "@/lib/portfolio";

const DEFAULT_MAX_LOSS = 50_000;

function parseSpan(row: PortfolioPositionRecord): number {
  const v = row.span_margin_required;
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string") {
    const n = Number(v.replace(/,/g, "").trim());
    return Number.isFinite(n) ? n : 0;
  }
  return 0;
}

export function defaultMaxLossForGroup(group: PortfolioPositionGroup): number {
  const spanSum = group.rows.reduce((s, r) => s + parseSpan(r), 0);
  if (spanSum > 0) return Math.round(spanSum);
  return DEFAULT_MAX_LOSS;
}

function riskProfileLabel(profile: string): string {
  switch (profile) {
    case "net_short_call":
      return "Net short call — unlimited upside risk";
    case "net_short_put":
      return "Net short put — unlimited downside risk";
    case "multi_leg_reopt":
      return "Multi-leg — re-optimize wings";
    case "defined":
      return "Defined risk";
    default:
      return profile;
  }
}

type PortfolioHedgePanelProps = {
  group: PortfolioPositionGroup;
  selectedCandidate: StrategyHedgeCandidate | null;
  onSelectCandidate: (c: StrategyHedgeCandidate | null) => void;
  onExecute: () => void;
  onLotSizeChange?: (lotSize: number) => void;
};

export function PortfolioHedgePanel({
  group,
  selectedCandidate,
  onSelectCandidate,
  onExecute,
  onLotSizeChange,
}: PortfolioHedgePanelProps) {
  const [maxLossInput, setMaxLossInput] = useState(() =>
    String(defaultMaxLossForGroup(group)),
  );
  const [submittedMaxLoss, setSubmittedMaxLoss] = useState<number | null>(null);
  const [inputError, setInputError] = useState<string | null>(null);

  const hedgeQ = useQuery({
    queryKey: [
      "hedge",
      "strategy-options",
      group.stockCode,
      group.exchangeCode,
      group.expiryDate,
      submittedMaxLoss,
    ],
    queryFn: () =>
      postStrategyHedges({
        stock_code: group.stockCode,
        expiry_date: group.expiryDate,
        user_max_loss_preference: submittedMaxLoss!,
        exchange_code: group.exchangeCode,
      }),
    enabled: submittedMaxLoss != null && submittedMaxLoss > 0,
    staleTime: 15_000,
  });

  const success = hedgeQ.data?.Success;
  const summary = success?.summary;
  const candidates = success?.candidates ?? [];

  useEffect(() => {
    if (summary?.lot_size && summary.lot_size > 0) {
      onLotSizeChange?.(summary.lot_size);
    }
  }, [summary?.lot_size, onLotSizeChange]);

  const apiError =
    hedgeQ.data && hedgeQ.data.Status !== 200
      ? hedgeQ.data.Error || "Unable to load hedge candidates"
      : hedgeQ.error instanceof Error
        ? hedgeQ.error.message
        : null;

  const handleFindOptions = () => {
    const parsed = Number(maxLossInput.replace(/,/g, "").trim());
    if (!Number.isFinite(parsed) || parsed <= 0) {
      setInputError("Enter a maximum loss amount greater than zero.");
      return;
    }
    setInputError(null);
    onSelectCandidate(null);
    setSubmittedMaxLoss(parsed);
  };

  const selectedKey = selectedCandidate
    ? hedgeCandidateKey(selectedCandidate)
    : null;

  return (
    <div className="border-t border-zinc-200/80 bg-white/60 px-3 py-4 dark:border-zinc-700/80 dark:bg-zinc-950/30 sm:px-4">
      <div className="mb-3 space-y-1">
        <h4 className="text-xs font-semibold uppercase tracking-wide text-zinc-700 dark:text-zinc-200">
          Hedge strategy
        </h4>
        {summary ? (
          <p className="text-[11px] text-zinc-600 dark:text-zinc-400">
            {riskProfileLabel(summary.risk_profile)} · Δ{" "}
            {summary.strategy_delta.toFixed(2)} · Γ{" "}
            {summary.strategy_gamma.toFixed(4)}
          </p>
        ) : null}
      </div>

      <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
        <label className="min-w-0 flex-1 space-y-1">
          <span className="text-xs font-medium text-zinc-700 dark:text-zinc-300">
            Maximum loss you are willing to take (₹)
          </span>
          <input
            type="text"
            inputMode="numeric"
            value={maxLossInput}
            onChange={(e) => setMaxLossInput(e.target.value)}
            className="app-input w-full tabular-nums"
            placeholder="e.g. 50000"
            aria-invalid={inputError != null}
          />
        </label>
        <button
          type="button"
          className="app-btn-secondary shrink-0 px-4 py-2 text-sm font-medium"
          onClick={handleFindOptions}
        >
          Find options
        </button>
      </div>
      {inputError ? (
        <p className="mt-2 text-xs text-red-600 dark:text-red-400">{inputError}</p>
      ) : null}

      <div className="mt-4 space-y-2">
        {submittedMaxLoss == null ? (
          <p className="text-xs app-text-muted">
            Enter your max loss budget and click Find options to see protective
            wings.
          </p>
        ) : hedgeQ.isFetching && !candidates.length ? (
          <div className="space-y-2">
            {[0, 1, 2].map((i) => (
              <div key={i} className="h-10 w-full app-skeleton rounded-sm border-0" />
            ))}
          </div>
        ) : apiError ? (
          <p className="text-xs text-red-600 dark:text-red-400">{apiError}</p>
        ) : candidates.length === 0 ? (
          <p className="text-xs app-text-muted">
            No hedge candidates within your risk budget for this strategy.
          </p>
        ) : (
          <div className="space-y-2" role="radiogroup" aria-label="Hedge options">
            {candidates.map((c) => {
              const key = hedgeCandidateKey(c);
              const checked = selectedKey === key;
              return (
                <label
                  key={key}
                  className={`flex cursor-pointer flex-col gap-2 rounded-md border p-3 text-xs transition sm:flex-row sm:items-center sm:justify-between ${
                    checked
                      ? "border-sky-400 bg-sky-50/80 ring-1 ring-sky-500/30 dark:border-sky-600 dark:bg-sky-950/40"
                      : "border-zinc-200/90 bg-white/80 hover:border-zinc-300 dark:border-zinc-700 dark:bg-zinc-900/40"
                  }`}
                >
                  <div className="flex items-start gap-2">
                    <input
                      type="radio"
                      name={`hedge-${group.key}`}
                      checked={checked}
                      onChange={() => onSelectCandidate(c)}
                      className="mt-0.5"
                    />
                    <div>
                      <div className="font-medium text-zinc-900 dark:text-zinc-100">
                        Buy {c.right} {c.strike_price}
                        <span className="ml-2 font-normal text-zinc-500 dark:text-zinc-400">
                          (covers short {c.short_strike})
                        </span>
                      </div>
                      <div className="mt-1 tabular-nums text-zinc-600 dark:text-zinc-400">
                        LTP {formatIndianMoneyCompact(c.ltp)} · Premium{" "}
                        {formatIndianMoneyCompact(c.net_premium_cost)} · Max
                        loss {formatIndianMoneyCompact(c.max_loss_estimate)} ·
                        Margin relief{" "}
                        <span className="text-emerald-700 dark:text-emerald-400">
                          {formatIndianMoneyCompact(c.estimated_margin_relief)}
                        </span>
                      </div>
                    </div>
                  </div>
                </label>
              );
            })}
          </div>
        )}
      </div>

      <div className="mt-4 flex justify-end">
        <button
          type="button"
          className="app-btn-primary px-4 py-2 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-50"
          disabled={!selectedCandidate}
          onClick={onExecute}
        >
          Execute hedge
        </button>
      </div>
    </div>
  );
}
