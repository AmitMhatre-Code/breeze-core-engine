"use client";

import { useEffect, type CSSProperties } from "react";
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

/** Same theme-swapping accent tokens as OpenPositionsTable's pill buttons. */
const ACCENT_OUTLINE_STYLE: CSSProperties = {
  borderColor: "var(--accent-strong)",
  color: "var(--accent-strong)",
};
const ACCENT_SOLID_STYLE: CSSProperties = {
  backgroundColor: "var(--accent-strong)",
  color: "var(--accent-ink)",
};

type PortfolioHedgePanelProps = {
  group: PortfolioPositionGroup;
  selectedCandidate: StrategyHedgeCandidate | null;
  onSelectCandidate: (c: StrategyHedgeCandidate | null) => void;
  onExecute: () => void;
  onLotSizeChange?: (lotSize: number) => void;
  /** Hide the "Hedge candidates" heading — used inside HedgeCandidatesModal, which has its own title. */
  showHeading?: boolean;
};

/** Auto-fetches candidates for the group's default (span-based) risk budget as soon as it's rendered — no manual entry step, matching the Terminal design. */
export function PortfolioHedgePanel({
  group,
  selectedCandidate,
  onSelectCandidate,
  onExecute,
  onLotSizeChange,
  showHeading = true,
}: PortfolioHedgePanelProps) {
  const maxLoss = defaultMaxLossForGroup(group);

  const hedgeQ = useQuery({
    queryKey: [
      "hedge",
      "strategy-options",
      group.stockCode,
      group.exchangeCode,
      group.expiryDate,
      maxLoss,
    ],
    queryFn: () =>
      postStrategyHedges({
        stock_code: group.stockCode,
        expiry_date: group.expiryDate,
        user_max_loss_preference: maxLoss,
        exchange_code: group.exchangeCode,
      }),
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

  const selectedKey = selectedCandidate
    ? hedgeCandidateKey(selectedCandidate)
    : null;

  return (
    <div className="min-w-0 p-3">
      <div className="mb-3 space-y-1">
        {showHeading ? (
          <h3 className="text-heading font-semibold uppercase tracking-wide text-faint">
            Hedge candidates
          </h3>
        ) : null}
        {summary ? (
          <p className="text-heading text-muted">
            {riskProfileLabel(summary.risk_profile)} · Δ{" "}
            {summary.strategy_delta.toFixed(2)} · Γ{" "}
            {summary.strategy_gamma.toFixed(4)} · Max loss budget{" "}
            {formatIndianMoneyCompact(maxLoss)}
          </p>
        ) : null}
      </div>

      <div className="space-y-2" role="radiogroup" aria-label="Hedge options">
        {hedgeQ.isFetching && !candidates.length ? (
          [0, 1].map((i) => (
            <div key={i} className="h-16 w-full app-skeleton rounded-md border-0" />
          ))
        ) : apiError ? (
          <p className="text-xs text-down">{apiError}</p>
        ) : candidates.length === 0 ? (
          <p className="text-xs app-text-muted">
            No hedge candidates within the default risk budget for this
            strategy.
          </p>
        ) : (
          candidates.map((c) => {
            const key = hedgeCandidateKey(c);
            const checked = selectedKey === key;
            return (
              <div
                key={key}
                role="radio"
                aria-checked={checked}
                tabIndex={0}
                onClick={() => onSelectCandidate(checked ? null : c)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onSelectCandidate(checked ? null : c);
                  }
                }}
                className="flex cursor-pointer flex-col gap-2 rounded-lg border p-3 text-xs transition sm:flex-row sm:items-center sm:justify-between"
                style={
                  checked
                    ? {
                        borderColor: "var(--accent)",
                        backgroundColor: "var(--accent-tint)",
                      }
                    : { borderColor: "var(--border-soft)" }
                }
              >
                <div>
                  <div className="font-semibold text-foreground">
                    Buy {c.strike_price.toLocaleString("en-IN")}{" "}
                    {c.right === "Call" ? "CE" : "PE"}
                  </div>
                  <div className="mt-0.5 tabular-nums text-muted">
                    {c.right === "Call" ? "Caps upside" : "Caps downside"}{" "}
                    · +
                    {formatIndianMoneyCompact(c.estimated_margin_relief, {
                      shortSuffix: true,
                      skipK: true,
                    })}{" "}
                    margin relief ·{" "}
                    {formatIndianMoneyCompact(c.net_premium_cost, {
                      shortSuffix: true,
                      skipK: true,
                    })}{" "}
                    premium
                  </div>
                </div>
                {checked ? (
                  <button
                    type="button"
                    title="Click to unselect"
                    className="inline-flex shrink-0 items-center justify-center self-start rounded-lg px-3 py-1 text-heading font-bold uppercase tracking-wide transition hover:brightness-95 sm:self-center"
                    style={ACCENT_SOLID_STYLE}
                    onClick={(e) => {
                      e.stopPropagation();
                      onSelectCandidate(null);
                    }}
                  >
                    Selected
                  </button>
                ) : (
                  <button
                    type="button"
                    className="inline-flex shrink-0 items-center justify-center self-start rounded-lg border px-3 py-1 text-xs font-semibold transition hover:bg-[var(--accent-tint)] sm:self-center"
                    style={ACCENT_OUTLINE_STYLE}
                    onClick={(e) => {
                      e.stopPropagation();
                      onSelectCandidate(c);
                    }}
                  >
                    Select
                  </button>
                )}
              </div>
            );
          })
        )}
      </div>

      <button
        type="button"
        className="mt-4 inline-flex w-full items-center justify-center rounded-lg px-4 py-2.5 text-sm font-bold transition hover:brightness-95 disabled:cursor-not-allowed disabled:opacity-50"
        style={ACCENT_SOLID_STYLE}
        disabled={!selectedCandidate}
        onClick={onExecute}
      >
        Execute hedge
      </button>
    </div>
  );
}
