"use client";

import { useEffect } from "react";
import { InfoPopover } from "@/components/ui/InfoPopover";
import { aggressiveLimitPopoverParagraphs } from "@/lib/help/topic-content";
import { sb } from "@/lib/strategy-builder/ui";
import {
  useAggressiveOrderConfig,
} from "@/lib/use-aggressive-limit-order-enabled";
import type { AggressiveOrderMode } from "@/lib/aggressive-order";

function LightningBoltIcon({ filled }: { filled?: boolean }) {
  return (
    <svg
      width="15"
      height="15"
      viewBox="0 0 24 24"
      fill={filled ? "currentColor" : "none"}
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z" />
    </svg>
  );
}

export function AggressiveLimitOrderField({
  aggressive,
  price,
  mode = "limit_tolerance",
  tolerancePct,
  onAggressiveChange,
  onPriceChange,
  onModeChange,
  onTolerancePctChange,
  ariaLabel = "Aggressive limit order",
}: {
  aggressive: boolean;
  price: string;
  mode?: AggressiveOrderMode;
  tolerancePct?: number;
  onAggressiveChange: (checked: boolean) => void;
  onPriceChange: (price: string) => void;
  onModeChange?: (mode: AggressiveOrderMode) => void;
  onTolerancePctChange?: (pct: number) => void;
  ariaLabel?: string;
}) {
  const paragraphs = aggressiveLimitPopoverParagraphs();
  const { enabled, defaultTolerancePct, maxTolerancePct } =
    useAggressiveOrderConfig();
  const isAggressive = enabled && aggressive;
  const effectiveTolerance = tolerancePct ?? defaultTolerancePct;

  // Feature off (backend AGGRESSIVE_LIMIT_ORDER_ENABLED=false); clear any stale toggle state.
  useEffect(() => {
    if (!enabled && aggressive) onAggressiveChange(false);
  }, [enabled, aggressive, onAggressiveChange]);

  const showModeControls = isAggressive && onModeChange != null;

  return (
    <div className="flex flex-col gap-1.5">
      <label className={sb.fieldLabel}>
        <span className="inline-flex items-center gap-1.5">
          Price (₹)
          {enabled && (
            <InfoPopover
              title="Aggressive limit"
              ariaLabel="Aggressive limit help"
              learnMoreTopicId="aggressive-limit"
            >
              {paragraphs.map((p, i) => (
                <p key={i}>{p}</p>
              ))}
            </InfoPopover>
          )}
        </span>
        <div className="relative mt-1.5">
          <input
            type="number"
            min={0}
            step={0.05}
            disabled={isAggressive}
            aria-label="Limit price"
            className={`${sb.input} tabular-nums pr-11 disabled:cursor-not-allowed disabled:opacity-50`}
            value={isAggressive ? "" : price}
            onChange={(e) => onPriceChange(e.target.value)}
            placeholder={
              isAggressive
                ? mode === "market"
                  ? "Market order"
                  : "Aggressive limit (from LTP)"
                : "0"
            }
          />
          {enabled && (
            <button
              type="button"
              aria-pressed={aggressive}
              aria-label={`${ariaLabel}. Toggle aggressive order.`}
              title="Aggressive order"
              onClick={() => onAggressiveChange(!aggressive)}
              className="absolute right-1.5 top-1/2 flex size-7 -translate-y-1/2 items-center justify-center rounded-[7px] transition focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
              style={{ color: "var(--amber)" }}
            >
              <LightningBoltIcon filled={aggressive} />
            </button>
          )}
        </div>
      </label>

      {showModeControls && (
        <div className="flex flex-wrap items-center gap-2">
          <div
            role="group"
            aria-label="Aggressive order style"
            className="inline-flex overflow-hidden rounded-lg border border-border-soft"
          >
            {(
              [
                ["limit_tolerance", "Limit + tol"],
                ["market", "Market"],
              ] as [AggressiveOrderMode, string][]
            ).map(([m, label]) => (
              <button
                key={m}
                type="button"
                aria-pressed={mode === m}
                onClick={() => onModeChange?.(m)}
                className={`px-2.5 py-1 text-xs font-medium transition ${
                  mode === m
                    ? "bg-amber-tint text-amber-on-tint"
                    : "text-muted hover:bg-border-soft"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
          {mode === "limit_tolerance" && (
            <label className="inline-flex items-center gap-1.5 text-xs text-muted">
              <span>Tolerance</span>
              <input
                type="number"
                min={0}
                max={maxTolerancePct}
                step={0.5}
                aria-label="Aggressive limit tolerance percent"
                className={`${sb.input} h-8 w-16 tabular-nums`}
                value={String(effectiveTolerance)}
                onChange={(e) => {
                  const v = parseFloat(e.target.value);
                  if (!Number.isFinite(v)) {
                    onTolerancePctChange?.(0);
                    return;
                  }
                  onTolerancePctChange?.(
                    Math.max(0, Math.min(maxTolerancePct, v)),
                  );
                }}
              />
              <span>%</span>
            </label>
          )}
          <span className="text-xs text-muted">
            {mode === "market"
              ? "Sent as a market order (may be rejected until ICICI enables it)."
              : `Limit priced ${effectiveTolerance}% past LTP to fill.`}
          </span>
        </div>
      )}
    </div>
  );
}
