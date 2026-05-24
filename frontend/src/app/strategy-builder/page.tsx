"use client";

import type { ReactNode, RefObject } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { AppShell } from "@/components/layout/AppShell";
import { RevokedTradingPageGuard } from "@/components/license/RevokedTradingPageGuard";
import { AsyncLabelSpan } from "@/components/ui/AsyncLabelSpan";
import {
  OptionChainTable,
  scrollOptionChainAtmIntoView,
} from "@/components/order/OptionChainTable";
import { OptionChainUnderlyingSearch } from "@/components/order/OptionChainUnderlyingSearch";
import { OrderExecutionConfirmDialog } from "@/components/order/OrderExecutionConfirmDialog";
import { RateLimitPauseOverlay } from "@/components/order/RateLimitPauseOverlay";
import { ExpirySelectPill } from "@/components/strategy-builder/ExpirySelectPill";
import { OptionStrategyIcon } from "@/components/strategy-builder/OptionStrategyIcon";
import { ReadymadeSetupTooltip } from "@/components/strategy-builder/ReadymadeSetupTooltip";
import { PayoffChart } from "@/components/strategy-builder/PayoffChart";
import { apiClient } from "@/lib/api-client";
import { useBreakChunkQty } from "@/lib/use-break-chunk-qty";
import { useRateLimitCountdown } from "@/lib/use-rate-limit-countdown";
import { atmSigmaFromChain } from "@/lib/strategy-builder/chainIv";
import {
  expiryDisplayToYears,
  sortExpiryDatesAsc,
} from "@/lib/strategy-builder/expiry";
import { sb } from "@/lib/strategy-builder/ui";
import {
  estimateProbabilityOfProfit,
  payoffChartSpotDomain,
  portfolioGreeks,
  scanMarkToModelCurve,
  scanPayoffCurve,
  summarizePayoffExact,
} from "@/lib/strategy-builder/payoff";
import {
  applyTemplate,
  buildTemplateContext,
  outlookPillClassName,
  outlookPillLabel,
  type TemplateId,
  STRATEGY_TEMPLATES,
} from "@/lib/strategy-builder/templates";
import type {
  ChainApiResponse,
  ChainRow,
  MarginApiResponse,
  OptionRight,
  OrderSide,
  Outlook,
  StrategyLeg,
  UnderlyingsApiResponse,
} from "@/lib/strategy-builder/types";
import { formatIndianMoneyCompact, moneyToneClass } from "@/lib/format-money-in";
import { ltpAsOrderPrice } from "@/lib/order-confirm";

/** Readymade card selection (Naked / Covered Shorts are separate from `STRATEGY_TEMPLATES`). */
type ReadymadeSelection =
  | "naked-shorts"
  | "covered-shorts"
  | "build-your-own"
  | TemplateId;

/** Strategy Builder only: caps broker load (standalone uncovered-shorts page may use higher top). */
const STRATEGY_BUILDER_OPTIONS_TO_LIST_MAX = 5;

/** Shrinks payoff glyphs so outlook pills can sit in the top-right without crowding. */
const READYMADE_GRAPH_AREA =
  "flex min-h-0 w-full flex-1 items-center justify-center px-0.5 pt-0.5";
const READYMADE_GRAPH_BOX =
  "flex h-[100%] w-[100%] max-h-full min-h-0 items-center justify-center [&_svg]:max-h-full";

function ReadymadeOutlookPill({ outlook }: { outlook: Outlook | undefined }) {
  if (!outlook) return null;
  return (
    <span
      className={`pointer-events-none absolute right-1.5 top-1.5 z-10 rounded-full px-[5px] py-px text-[10px] font-semibold leading-none tracking-tight ${outlookPillClassName(outlook)}`}
      aria-hidden
    >
      {outlookPillLabel(outlook)}
    </span>
  );
}

type UncoveredSidePayload = {
  Status?: number;
  Success?: Record<string, unknown>[];
  Error?: string | null;
};

type UncoveredScanResponse = {
  ce_options: UncoveredSidePayload;
  pe_options: UncoveredSidePayload;
};

function parseNum(v: unknown): number {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string") {
    const n = parseFloat(v.replace(/,/g, ""));
    return Number.isFinite(n) ? n : NaN;
  }
  return NaN;
}

function optionMidFromApiLeg(
  side: Record<string, unknown> | null | undefined,
): number | undefined {
  if (!side) return undefined;
  const ltp = parseNum(side.ltp);
  if (Number.isFinite(ltp) && ltp > 0) return ltp;
  const bid = parseNum(side.best_bid_price);
  const ask = parseNum(side.best_offer_price);
  if (Number.isFinite(bid) && Number.isFinite(ask) && bid > 0 && ask > 0) {
    return 0.5 * (bid + ask);
  }
  return undefined;
}

const OTM_SLIDER_MIN = 0;
const OTM_SLIDER_MAX = 20;
/** Long strangle: expected spot move targets (negative = down, positive = up). */
const STRANGLE_MOVE_MIN = -30;
const STRANGLE_MOVE_MAX = 30;
const MARGIN_LACS_MAX = 999_999;

/** Quantity-only signature — price edits keep cached margin valid for refresh UI. */
function legsQtySignature(legs: StrategyLeg[]): string {
  return JSON.stringify(legs.map((l) => [l.id, l.lots]));
}

function legMarginEntryMatches(
  leg: StrategyLeg,
  entry: { lots: number; span: number | null } | undefined,
): boolean {
  if (!entry) return false;
  return entry.lots === leg.lots;
}

function MarginRefreshIconButton({
  onClick,
  disabled,
  label,
  title: titleProp,
}: {
  onClick: () => void;
  disabled?: boolean;
  label: string;
  title?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      title={titleProp ?? label}
      className="inline-flex h-7 w-7 items-center justify-center rounded-md text-zinc-600 hover:bg-zinc-200/80 hover:text-zinc-900 disabled:cursor-not-allowed disabled:text-zinc-300 disabled:hover:bg-transparent dark:text-zinc-400 dark:hover:bg-zinc-700/80 dark:hover:text-zinc-100 dark:disabled:text-zinc-600 dark:disabled:hover:bg-transparent"
    >
      <svg
        className="h-4 w-4"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden
      >
        <path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8" />
        <path d="M21 3v5h-5" />
        <path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16" />
        <path d="M21 21v-5h-5" />
      </svg>
    </button>
  );
}

function UncoveredNumberStepper({
  label,
  value,
  onChange,
  min,
  max,
  suffix,
  compact = false,
}: {
  label: string;
  /** `null` = empty (not set). */
  value: number | null;
  onChange: (v: number | null) => void;
  min: number;
  max: number;
  suffix?: ReactNode;
  /** Inline / grid layouts (e.g. Naked Shorts parameters). */
  compact?: boolean;
}) {
  const stepBtn = compact
    ? "flex h-8 w-8 shrink-0 items-center justify-center border-0 bg-zinc-100 text-base font-light leading-none text-zinc-700 transition hover:bg-zinc-200 active:bg-zinc-300 disabled:cursor-not-allowed disabled:text-zinc-300 disabled:hover:bg-zinc-100 disabled:active:bg-zinc-100 dark:bg-zinc-800 dark:text-zinc-200 dark:hover:bg-zinc-700 dark:active:bg-zinc-600 dark:disabled:text-zinc-600 dark:disabled:hover:bg-zinc-800 dark:disabled:active:bg-zinc-800"
    : "flex h-11 w-11 shrink-0 items-center justify-center border-0 bg-zinc-100 text-xl font-light leading-none text-zinc-700 transition hover:bg-zinc-200 active:bg-zinc-300 disabled:cursor-not-allowed disabled:text-zinc-300 disabled:hover:bg-zinc-100 disabled:active:bg-zinc-100 dark:bg-zinc-800 dark:text-zinc-200 dark:hover:bg-zinc-700 dark:active:bg-zinc-600 dark:disabled:text-zinc-600 dark:disabled:hover:bg-zinc-800 dark:disabled:active:bg-zinc-800";

  const round = compact ? "rounded-lg" : "rounded-md";
  const roundL = compact ? "rounded-none rounded-l-lg" : "rounded-none rounded-l-xl";
  const roundR = compact ? "rounded-none rounded-r-lg" : "rounded-none rounded-r-xl";
  const minH = compact ? "min-h-8" : "min-h-11";
  const inputPad = compact ? "py-1" : "py-2";
  const inputText = "text-center text-sm font-semibold tabular-nums";

  return (
    <div className="min-w-0">
      <span
        className={
          compact
            ? "mb-1 block text-[11px] font-medium text-zinc-500 dark:text-zinc-400"
            : sb.fieldLabel
        }
      >
        {label}
      </span>
      <div
        className={`flex overflow-hidden border border-zinc-200/90 shadow-sm dark:border-zinc-700 ${round}`}
      >
        <button
          type="button"
          className={`${stepBtn} ${roundL}`}
          onClick={() =>
            value != null && onChange(Math.max(min, value - 1))
          }
          disabled={value == null || value <= min}
          aria-label={`Decrease ${label}`}
        >
          −
        </button>
        <div
          className={`flex ${minH} min-w-0 flex-1 items-stretch border-x border-zinc-200/90 bg-white dark:border-zinc-700 dark:bg-zinc-950`}
        >
          <input
            type="number"
            min={min}
            max={max}
            value={value === null ? "" : value}
            onChange={(e) => {
              const raw = e.target.value;
              if (raw === "") {
                onChange(null);
                return;
              }
              const n = parseInt(raw, 10);
              if (!Number.isFinite(n)) return;
              onChange(Math.min(max, Math.max(min, n)));
            }}
            onBlur={() => {
              if (value === null) return;
              onChange(Math.min(max, Math.max(min, value)));
            }}
            className={`min-w-0 flex-1 border-0 bg-transparent ${inputPad} ${inputText} text-zinc-900 outline-none focus:ring-0 dark:text-zinc-100 [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none`}
            aria-label={label}
          />
          {suffix != null ? (
            <div
              className={
                compact
                  ? "flex shrink-0 items-center border-l border-zinc-200/90 bg-zinc-50/90 px-2 text-[10px] font-semibold uppercase tracking-wide text-zinc-500 dark:border-zinc-700 dark:bg-zinc-900/50 dark:text-zinc-400"
                  : "flex shrink-0 items-center border-l border-zinc-200/90 bg-zinc-50/90 px-3 text-xs font-semibold tracking-wide text-zinc-500 dark:border-zinc-700 dark:bg-zinc-900/50 dark:text-zinc-400"
              }
            >
              {suffix}
            </div>
          ) : null}
        </div>
        <button
          type="button"
          className={`${stepBtn} ${roundR}`}
          onClick={() => {
            if (value === null) onChange(min);
            else onChange(Math.min(max, value + 1));
          }}
          disabled={value != null && value >= max}
          aria-label={`Increase ${label}`}
        >
          +
        </button>
      </div>
    </div>
  );
}

function OtmRangeValueKnob({
  value,
  pct,
  compact,
  variant,
}: {
  value: number;
  pct: number;
  compact: boolean;
  variant: "min" | "max";
}) {
  const z = variant === "max" ? "z-[45]" : "z-[40]";
  const inset = compact ? "0.875rem" : "1rem";
  const sizeCls = compact
    ? "h-7 min-w-[1.75rem] px-0.5 text-[9px]"
    : "h-8 min-w-[2rem] px-1 text-[10px]";
  return (
    <div
      className={`pointer-events-none absolute top-1/2 ${z} flex ${sizeCls} -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full border-[3px] border-blue-600 bg-white font-bold tabular-nums leading-none text-zinc-900 shadow-[0_0_0_1px_rgb(37_99_235/0.12),0_3px_8px_rgb(15_23_42/0.16)] dark:border-blue-500 dark:bg-zinc-900 dark:text-zinc-50 dark:shadow-[0_0_0_1px_rgb(59_130_246/0.2),0_3px_8px_rgb(0_0_0/0.35)]`}
      style={{
        left: `clamp(${inset}, ${pct}%, calc(100% - ${inset}))`,
      }}
      aria-hidden
    >
      {value}%
    </div>
  );
}

function UncoveredOtmRangeSlider({
  label,
  minValue,
  maxValue,
  onMinChange,
  onMaxChange,
  minAriaLabel,
  maxAriaLabel,
  compact = false,
}: {
  label: string;
  minValue: number;
  maxValue: number;
  onMinChange: (v: number) => void;
  onMaxChange: (v: number) => void;
  minAriaLabel: string;
  maxAriaLabel: string;
  compact?: boolean;
}) {
  const minPct =
    ((minValue - OTM_SLIDER_MIN) / (OTM_SLIDER_MAX - OTM_SLIDER_MIN)) * 100;
  const maxPct =
    ((maxValue - OTM_SLIDER_MIN) / (OTM_SLIDER_MAX - OTM_SLIDER_MIN)) * 100;
  const thumbInteractiveCls =
    "[&::-webkit-slider-thumb]:pointer-events-auto [&::-moz-range-thumb]:pointer-events-auto";

  if (compact) {
    return (
      <div className="min-w-0 w-full">
        <span className="mb-1 block text-[11px] font-medium text-zinc-500 dark:text-zinc-400">
          {label}
        </span>
        <div className="relative flex h-8 w-full shrink-0 items-center">
          <div className="relative h-6 w-full overflow-visible">
            <div className="pointer-events-none absolute top-1/2 h-1.5 w-full -translate-y-1/2 rounded-full bg-zinc-200 dark:bg-zinc-700/85" />
            <div
              className="pointer-events-none absolute top-1/2 h-1.5 -translate-y-1/2 rounded-full bg-blue-600 dark:bg-blue-500"
              style={{ left: `${minPct}%`, width: `${Math.max(0, maxPct - minPct)}%` }}
            />
            <input
              type="range"
              min={OTM_SLIDER_MIN}
              max={OTM_SLIDER_MAX}
              value={minValue}
              onChange={(e) => onMinChange(Math.min(Number(e.target.value), maxValue))}
              className={`sb-range-slim sb-range-otm pointer-events-none absolute inset-0 z-20 w-full min-w-0 bg-transparent ${thumbInteractiveCls}`}
              aria-label={minAriaLabel}
              aria-valuetext={`${minValue} percent OTM minimum`}
            />
            <input
              type="range"
              min={OTM_SLIDER_MIN}
              max={OTM_SLIDER_MAX}
              value={maxValue}
              onChange={(e) => onMaxChange(Math.max(Number(e.target.value), minValue))}
              className={`sb-range-slim sb-range-otm pointer-events-none absolute inset-0 z-30 w-full min-w-0 bg-transparent ${thumbInteractiveCls}`}
              aria-label={maxAriaLabel}
              aria-valuetext={`${maxValue} percent OTM maximum`}
            />
            <OtmRangeValueKnob
              value={minValue}
              pct={minPct}
              compact
              variant="min"
            />
            <OtmRangeValueKnob
              value={maxValue}
              pct={maxPct}
              compact
              variant="max"
            />
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-w-0">
      <div className="mb-2 text-xs font-medium text-zinc-600 dark:text-zinc-400">
        {label}
      </div>
      <div className="relative h-6 w-full overflow-visible">
        <div className="pointer-events-none absolute top-1/2 h-1.5 w-full -translate-y-1/2 rounded-full bg-zinc-200 dark:bg-zinc-700/85" />
        <div
          className="pointer-events-none absolute top-1/2 h-1.5 -translate-y-1/2 rounded-full bg-blue-600 dark:bg-blue-500"
          style={{ left: `${minPct}%`, width: `${Math.max(0, maxPct - minPct)}%` }}
        />
        <input
          type="range"
          min={OTM_SLIDER_MIN}
          max={OTM_SLIDER_MAX}
          value={minValue}
          onChange={(e) => onMinChange(Math.min(Number(e.target.value), maxValue))}
          className={`sb-range-slim sb-range-otm pointer-events-none absolute inset-0 z-20 w-full bg-transparent ${thumbInteractiveCls}`}
          aria-label={minAriaLabel}
          aria-valuetext={`${minValue} percent OTM minimum`}
        />
        <input
          type="range"
          min={OTM_SLIDER_MIN}
          max={OTM_SLIDER_MAX}
          value={maxValue}
          onChange={(e) => onMaxChange(Math.max(Number(e.target.value), minValue))}
          className={`sb-range-slim sb-range-otm pointer-events-none absolute inset-0 z-30 w-full bg-transparent ${thumbInteractiveCls}`}
          aria-label={maxAriaLabel}
          aria-valuetext={`${maxValue} percent OTM maximum`}
        />
        <OtmRangeValueKnob
          value={minValue}
          pct={minPct}
          compact={false}
          variant="min"
        />
        <OtmRangeValueKnob
          value={maxValue}
          pct={maxPct}
          compact={false}
          variant="max"
        />
      </div>
    </div>
  );
}

function StrangleMovePctKnob({
  value,
  pct,
  compact,
  variant,
}: {
  value: number;
  pct: number;
  compact: boolean;
  variant: "down" | "up";
}) {
  const z = variant === "up" ? "z-[45]" : "z-[40]";
  const inset = compact ? "0.875rem" : "1rem";
  const sizeCls = compact
    ? "h-7 min-w-[1.75rem] px-0.5 text-[9px]"
    : "h-8 min-w-[2rem] px-1 text-[10px]";
  const label = value === 0 ? "0%" : `${value > 0 ? "+" : ""}${value}%`;
  return (
    <div
      className={`pointer-events-none absolute top-1/2 ${z} flex ${sizeCls} -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full border-[3px] border-blue-600 bg-white font-bold tabular-nums leading-none text-zinc-900 shadow-[0_0_0_1px_rgb(37_99_235/0.12),0_3px_8px_rgb(15_23_42/0.16)] dark:border-blue-500 dark:bg-zinc-900 dark:text-zinc-50 dark:shadow-[0_0_0_1px_rgb(59_130_246/0.2),0_3px_8px_rgb(0_0_0/0.35)]`}
      style={{
        left: `clamp(${inset}, ${pct}%, calc(100% - ${inset}))`,
      }}
      aria-hidden
    >
      {label}
    </div>
  );
}

function StrangleMoveRangeSlider({
  label,
  downPct,
  upPct,
  onDownChange,
  onUpChange,
  compact = false,
}: {
  label: string;
  downPct: number;
  upPct: number;
  onDownChange: (v: number) => void;
  onUpChange: (v: number) => void;
  compact?: boolean;
}) {
  const span = STRANGLE_MOVE_MAX - STRANGLE_MOVE_MIN;
  const downClamped = Math.min(
    -1,
    Math.max(STRANGLE_MOVE_MIN, downPct),
  );
  const upClamped = Math.min(
    STRANGLE_MOVE_MAX,
    Math.max(1, upPct),
  );
  const minPct = ((downClamped - STRANGLE_MOVE_MIN) / span) * 100;
  const maxPct = ((upClamped - STRANGLE_MOVE_MIN) / span) * 100;
  const thumbInteractiveCls =
    "[&::-webkit-slider-thumb]:pointer-events-auto [&::-moz-range-thumb]:pointer-events-auto";

  if (compact) {
    return (
      <div className="min-w-0 w-full">
        <span className="mb-1 block text-[11px] font-medium text-zinc-500 dark:text-zinc-400">
          {label}
        </span>
        <div className="relative flex h-8 w-full shrink-0 items-center">
          <div className="relative h-6 w-full overflow-visible">
            <div className="pointer-events-none absolute top-1/2 h-1.5 w-full -translate-y-1/2 rounded-full bg-zinc-200 dark:bg-zinc-700/85" />
            <div
              className="pointer-events-none absolute top-1/2 h-1.5 -translate-y-1/2 rounded-full bg-blue-600 dark:bg-blue-500"
              style={{
                left: `${minPct}%`,
                width: `${Math.max(0, maxPct - minPct)}%`,
              }}
            />
            <input
              type="range"
              min={STRANGLE_MOVE_MIN}
              max={STRANGLE_MOVE_MAX}
              step={1}
              value={downClamped}
              onChange={(e) => {
                const v = Number(e.target.value);
                onDownChange(
                  Math.min(-1, Math.max(STRANGLE_MOVE_MIN, v)),
                );
              }}
              className={`sb-range-slim sb-range-otm pointer-events-none absolute inset-0 z-20 w-full min-w-0 bg-transparent ${thumbInteractiveCls}`}
              aria-label="Expected move to the downside (negative percent)"
              aria-valuetext={`${downClamped} percent from spot`}
            />
            <input
              type="range"
              min={STRANGLE_MOVE_MIN}
              max={STRANGLE_MOVE_MAX}
              step={1}
              value={upClamped}
              onChange={(e) => {
                const v = Number(e.target.value);
                onUpChange(Math.max(1, Math.min(STRANGLE_MOVE_MAX, v)));
              }}
              className={`sb-range-slim sb-range-otm pointer-events-none absolute inset-0 z-30 w-full min-w-0 bg-transparent ${thumbInteractiveCls}`}
              aria-label="Expected move to the upside (positive percent)"
              aria-valuetext={`+${upClamped} percent from spot`}
            />
            <StrangleMovePctKnob
              value={downClamped}
              pct={minPct}
              compact
              variant="down"
            />
            <StrangleMovePctKnob
              value={upClamped}
              pct={maxPct}
              compact
              variant="up"
            />
          </div>
        </div>
        <div className="mt-1 flex justify-between text-[10px] font-medium tabular-nums text-zinc-400 dark:text-zinc-500">
          <span>−30%</span>
          <span className="text-zinc-500 dark:text-zinc-400">0</span>
          <span>+30%</span>
        </div>
      </div>
    );
  }

  return (
    <div className="min-w-0">
      <div className="mb-2 text-xs font-medium text-zinc-600 dark:text-zinc-400">
        {label}
      </div>
      <div className="relative h-6 w-full overflow-visible">
        <div className="pointer-events-none absolute top-1/2 h-1.5 w-full -translate-y-1/2 rounded-full bg-zinc-200 dark:bg-zinc-700/85" />
        <div
          className="pointer-events-none absolute top-1/2 h-1.5 -translate-y-1/2 rounded-full bg-blue-600 dark:bg-blue-500"
          style={{
            left: `${minPct}%`,
            width: `${Math.max(0, maxPct - minPct)}%`,
          }}
        />
        <input
          type="range"
          min={STRANGLE_MOVE_MIN}
          max={STRANGLE_MOVE_MAX}
          step={1}
          value={downClamped}
          onChange={(e) => {
            const v = Number(e.target.value);
            onDownChange(
              Math.min(-1, Math.max(STRANGLE_MOVE_MIN, v)),
            );
          }}
          className={`sb-range-slim sb-range-otm pointer-events-none absolute inset-0 z-20 w-full bg-transparent ${thumbInteractiveCls}`}
          aria-label="Expected move to the downside (negative percent)"
        />
        <input
          type="range"
          min={STRANGLE_MOVE_MIN}
          max={STRANGLE_MOVE_MAX}
          step={1}
          value={upClamped}
          onChange={(e) => {
            const v = Number(e.target.value);
            onUpChange(Math.max(1, Math.min(STRANGLE_MOVE_MAX, v)));
          }}
          className={`sb-range-slim sb-range-otm pointer-events-none absolute inset-0 z-30 w-full bg-transparent ${thumbInteractiveCls}`}
          aria-label="Expected move to the upside (positive percent)"
        />
        <StrangleMovePctKnob
          value={downClamped}
          pct={minPct}
          compact={false}
          variant="down"
        />
        <StrangleMovePctKnob
          value={upClamped}
          pct={maxPct}
          compact={false}
          variant="up"
        />
      </div>
      <div className="mt-1 flex justify-between text-[10px] font-medium tabular-nums text-zinc-400 dark:text-zinc-500">
        <span>−30%</span>
        <span className="text-zinc-500 dark:text-zinc-400">0</span>
        <span>+30%</span>
      </div>
    </div>
  );
}

/** Wing distance for long call butterfly — same 0–20% band as Naked Shorts OTM sliders. */
function ButterflyWingDistanceSlider({
  label,
  value,
  onChange,
  compact = false,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
  compact?: boolean;
}) {
  const min = OTM_SLIDER_MIN;
  const max = OTM_SLIDER_MAX;
  const clamped = Math.min(max, Math.max(min, Math.round(value)));
  const pct = ((clamped - min) / (max - min)) * 100;

  const track = (
    <>
      <div className="pointer-events-none absolute top-1/2 h-1.5 w-full -translate-y-1/2 rounded-full bg-zinc-200 dark:bg-zinc-700/85" />
      <div
        className="pointer-events-none absolute top-1/2 h-1.5 -translate-y-1/2 rounded-full bg-blue-600 dark:bg-blue-500"
        style={{ left: 0, width: `${pct}%` }}
      />
      <input
        type="range"
        min={min}
        max={max}
        step={1}
        value={clamped}
        onChange={(e) => onChange(Number(e.target.value))}
        className="sb-range-slim sb-range-otm relative z-[25] w-full min-w-0"
        aria-label={label}
        aria-valuetext={`${clamped} percent wing distance`}
      />
      <OtmRangeValueKnob
        value={clamped}
        pct={pct}
        compact={compact}
        variant="min"
      />
    </>
  );

  if (compact) {
    return (
      <div className="min-w-0 w-full max-w-xl">
        <span className="mb-1 block text-[11px] font-medium text-zinc-500 dark:text-zinc-400">
          {label}
        </span>
        <div className="relative h-6 w-full overflow-visible">{track}</div>
        <div className="mt-1 flex justify-between text-[10px] font-medium tabular-nums text-zinc-400 dark:text-zinc-500">
          <span>0%</span>
          <span>20%</span>
        </div>
      </div>
    );
  }

  return (
    <div className="min-w-0">
      <div className="mb-2 text-xs font-medium text-zinc-600 dark:text-zinc-400">
        {label}
      </div>
      <div className="relative h-6 w-full overflow-visible">{track}</div>
      <div className="mt-1 flex justify-between text-[10px] font-medium tabular-nums text-zinc-400 dark:text-zinc-500">
        <span>0%</span>
        <span>20%</span>
      </div>
    </div>
  );
}

function IvShockSlider({
  value,
  onChange,
}: {
  value: number;
  onChange: (v: number) => void;
}) {
  const min = -20;
  const max = 20;
  const pct = ((value - min) / (max - min)) * 100;
  const badgeText =
    value === 0 ? "0%" : `${value >= 0 ? "+" : ""}${value}%`;

  return (
    <div className="app-card-muted min-w-0 p-4">
      <div className="mb-3 flex flex-wrap items-end justify-between gap-2">
        <div>
          <div className="text-xs font-semibold text-zinc-800 dark:text-zinc-100">
            Implied volatility shock
          </div>
          <p className="mt-0.5 max-w-xl text-[11px] leading-relaxed text-zinc-500 dark:text-zinc-400">
            Adjust modeled IV for the chart and Greeks. Range −20% to +20%
            relative to chain IV.
          </p>
        </div>
        <button
          type="button"
          className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-zinc-200/90 bg-white text-zinc-600 shadow-sm transition hover:border-zinc-300 hover:bg-zinc-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-500/40 disabled:pointer-events-none disabled:cursor-not-allowed disabled:border-zinc-200 disabled:bg-zinc-100 disabled:text-zinc-400 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:border-zinc-600 dark:hover:bg-zinc-800 dark:disabled:border-zinc-700 dark:disabled:bg-zinc-800 dark:disabled:text-zinc-500"
          onClick={() => onChange(0)}
          disabled={value === 0}
          aria-label="Reset IV shock"
          title="Reset IV shock"
        >
          <svg
            className="h-3.5 w-3.5"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden
          >
            <path d="M3 12a9 9 0 1 0 2.64-6.36" />
            <path d="M3 3v6h6" />
          </svg>
        </button>
      </div>
      <div className="relative pt-7">
        <div
          className="pointer-events-none absolute top-0 z-10 -translate-x-1/2 whitespace-nowrap rounded-full border border-sky-200/80 bg-sky-50 px-2.5 py-1 text-xs font-semibold tabular-nums text-sky-900 shadow-sm ring-1 ring-sky-500/10 dark:border-sky-800/80 dark:bg-sky-950/80 dark:text-sky-100 dark:ring-sky-400/15"
          style={{
            left: `clamp(1.25rem, ${pct}%, calc(100% - 1.25rem))`,
          }}
          aria-hidden
        >
          {badgeText}
        </div>
        <div className="pointer-events-none absolute top-1/2 left-0 h-1.5 w-full -translate-y-1/2 rounded-full bg-zinc-200 dark:bg-zinc-700/85" />
        <div
          className="pointer-events-none absolute top-1/2 left-0 h-1.5 -translate-y-1/2 rounded-full bg-blue-600 dark:bg-blue-500"
          style={{ width: `${pct}%` }}
          aria-hidden
        />
        <input
          type="range"
          min={min}
          max={max}
          value={value}
          onChange={(e) => onChange(Number(e.target.value))}
          className="sb-range-slim relative z-0 w-full"
          aria-label="IV shock percent"
          aria-valuetext={`${value >= 0 ? "+" : ""}${value} percent`}
        />
        <div className="mt-2 flex justify-between text-[10px] font-medium tabular-nums text-zinc-400 dark:text-zinc-500">
          <span>−20%</span>
          <span className="text-zinc-500 dark:text-zinc-400">0</span>
          <span>+20%</span>
        </div>
      </div>
    </div>
  );
}

const BOOK_LAKH = 100_000;

function formatBookQtyInL(n: number): string {
  if (!Number.isFinite(n)) return "—";
  return `${(n / BOOK_LAKH).toLocaleString("en-IN", {
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  })} L`;
}

function formatPremiumFullInr(p: number): string {
  if (!Number.isFinite(p)) return "—";
  return `₹${p.toLocaleString("en-IN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

function buildUncoveredScanRoiTooltip(
  premium: number,
  carryPct: number,
  dte: number,
  spanTotal: number,
): string {
  const lines = [
    "Annualised ROI on SPAN (%):",
    "(premium ÷ DTE) × 365 ÷ span × 100",
    "",
    `Premium (bid × sized qty) = ${formatPremiumFullInr(premium)}`,
    `DTE (days to expiry) = ${dte}`,
    `span (total SPAN for this line) = ${formatPremiumFullInr(spanTotal)}`,
    "",
    `(${premium.toLocaleString("en-IN", { maximumFractionDigits: 2 })} ÷ ${dte}) × 365 ÷ ${spanTotal.toLocaleString("en-IN", { maximumFractionDigits: 2 })} × 100`,
    `≈ ${carryPct.toFixed(2)}%`,
  ];
  return lines.join("\n");
}

/** Backend sends DD-Mon-YYYY; show compact DD-Mmm for titles (e.g. 09-Mar-2026 → 09-Mar). */
function formatExpiryDdMmm(raw: unknown): string | null {
  const s = String(raw ?? "").trim();
  if (!s) return null;
  const parts = s.split("-");
  if (parts.length >= 2 && /^\d{1,2}$/.test(parts[0]!) && /^[A-Za-z]{3}$/.test(parts[1]!)) {
    const day = parts[0]!.padStart(2, "0");
    const mon =
      parts[1]!.slice(0, 1).toUpperCase() + parts[1]!.slice(1).toLowerCase();
    return `${day}-${mon}`;
  }
  return null;
}

/** e.g. NIFTY-24-Mar-25600-CE (expiry segment DD-Mmm from chain display). */
/** Key for matching scan-option cards to legs (stock + expiry + strike + right). */
function uncoveredContractKey(
  stock: string,
  expiry: string,
  strike: number,
  right: OptionRight,
): string {
  return `${stock.trim().toUpperCase()}|${expiry.trim()}|${strike}|${right}`;
}

/** Strike + right + order side — for Build Your Own chain ticks vs duplicate B/S. */
function buildYourOwnSlotKey(
  stock: string,
  expiry: string,
  strike: number,
  right: OptionRight,
  side: OrderSide,
): string {
  return `${stock.trim().toUpperCase()}|${expiry.trim()}|${strike}|${right}|${side}`;
}

function uncoveredScanOptionKey(opt: Record<string, unknown>): string {
  const stock = String(opt.stock_code ?? "").trim();
  const expiry = String(opt.expiry_date ?? "").trim();
  const k = parseNum(opt.strike_price);
  const rightStr = String(opt.right ?? "Call");
  const right: OptionRight = /^p/i.test(rightStr) ? "Put" : "Call";
  return uncoveredContractKey(
    stock,
    expiry,
    Number.isFinite(k) ? k : NaN,
    right,
  );
}

function formatOptionSymbolLabel(
  stock: string,
  expiryDisplay: string,
  strike: number,
  right: OptionRight,
): string {
  const sym = right === "Call" ? "CE" : "PE";
  const expShort = (() => {
    const p = expiryDisplay.trim().split("-");
    if (p.length >= 2 && /^\d{1,2}$/.test(p[0]!) && /^[A-Za-z]{3}/.test(p[1]!)) {
      const day = p[0]!.padStart(2, "0");
      const mon =
        p[1]!.slice(0, 1).toUpperCase() + p[1]!.slice(1, 3).toLowerCase();
      return `${day}-${mon}`;
    }
    return expiryDisplay.trim() || "—";
  })();
  const k = Number.isFinite(strike)
    ? Math.round(strike).toString()
    : "—";
  return `${stock || "—"}-${expShort}-${k}-${sym}`;
}

function snapQuantityToLotMultiple(qty: number, lotSize: number): number {
  if (!Number.isFinite(qty) || lotSize <= 0) return Math.max(lotSize, 1);
  const lots = Math.max(1, Math.round(qty / lotSize));
  return lots * lotSize;
}

function parseSpanMarginFromResponse(
  m: MarginApiResponse | undefined,
): number | null {
  if (m?.Status !== 200 || !m.Success) return null;
  const v = parseNum(
    (m.Success as { span_margin_required?: unknown }).span_margin_required,
  );
  return Number.isFinite(v) ? v : null;
}

function LegPositionChip({ side }: { side: OrderSide }) {
  const buy = side === "Buy";
  return (
    <span
      className={
        buy
          ? "inline-flex shrink-0 rounded-full border border-emerald-600/80 bg-emerald-600/15 px-2 py-0.5 text-sm font-semibold text-emerald-800 dark:border-emerald-500/70 dark:bg-emerald-500/15 dark:text-emerald-200"
          : "inline-flex shrink-0 rounded-full border border-red-600/80 bg-red-600/15 px-2 py-0.5 text-sm font-semibold text-red-800 dark:border-red-500/70 dark:bg-red-500/15 dark:text-red-200"
      }
    >
      {side}
    </span>
  );
}

/** Text-controlled qty; empty / 0 lots until user sets size; snaps to lot multiple on blur. */
function LegQuantityInput({
  legId,
  lots,
  lotSize,
  onLotsChange,
  className,
}: {
  legId: string;
  lots: number;
  lotSize: number;
  onLotsChange: (newLots: number) => void;
  className: string;
}) {
  const ls = lotSize > 0 ? lotSize : 1;
  const [text, setText] = useState(() =>
    Number.isFinite(lots) && lots > 0 ? String(Math.round(lots * ls)) : "",
  );

  useEffect(() => {
    const next = Number.isFinite(lots) && lots > 0 ? String(Math.round(lots * ls)) : "";
    const timer = setTimeout(() => {
      setText(next);
    }, 0);
    return () => clearTimeout(timer);
  }, [legId, lots, ls]);

  return (
    <input
      type="text"
      inputMode="numeric"
      autoComplete="off"
      aria-label="Quantity"
      className={className}
      value={text}
      onChange={(e) => {
        const t = e.target.value.replace(/,/g, "");
        if (t === "" || /^\d+$/.test(t)) {
          setText(t);
        }
      }}
      onBlur={() => {
        const t = text.replace(/,/g, "").trim();
        if (t === "") {
          onLotsChange(0);
          setText("");
          return;
        }
        const raw = parseInt(t, 10);
        if (!Number.isFinite(raw) || raw <= 0) {
          onLotsChange(0);
          setText("");
          return;
        }
        const snapped = snapQuantityToLotMultiple(raw, ls);
        const newLots = Math.max(1, Math.round(snapped / ls));
        onLotsChange(newLots);
        setText(String(newLots * ls));
      }}
    />
  );
}

function formatPremiumIntegerInr(p: number): string {
  if (!Number.isFinite(p)) return "—";
  const r = Math.round(p);
  return `₹${r.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
}

function formatNetPremiumCompactInr(v: number): string {
  if (!Number.isFinite(v)) return "—";
  const abs = formatIndianMoneyCompact(Math.abs(v));
  return v < 0 ? `(${abs})` : abs;
}

function UncoveredScanOptionCard({
  opt,
  legAdded,
  onAddLeg,
  omitAddButton = false,
}: {
  opt: Record<string, unknown>;
  legAdded: boolean;
  onAddLeg: () => void;
  /** When true, render metrics only (e.g. Covered Shorts pair row supplies its own Add control). */
  omitAddButton?: boolean;
}) {
  const prem = parseNum(opt.premium);
  const cr = parseNum(opt.carry_returns);
  const strike = parseNum(opt.strike_price);
  const qty = parseNum(opt.quantity);
  const ltp = parseNum(opt.ltp);
  const bid = parseNum(opt.best_bid_price);
  const ask = parseNum(opt.best_offer_price);
  const buyQty = parseNum(opt.total_buy_qty);
  const sellQty = parseNum(opt.total_sell_qty);
  const ratioRaw = opt.buy_sell_ratio;
  const ratioStr =
    typeof ratioRaw === "number" && Number.isFinite(ratioRaw)
      ? ratioRaw.toLocaleString("en-IN", { maximumFractionDigits: 2 })
      : typeof ratioRaw === "string" && ratioRaw.trim()
        ? ratioRaw.trim()
        : "—";

  const stock = String(opt.stock_code ?? "").trim();
  const rightRaw = String(opt.right ?? "");
  const abbr = /^p/i.test(rightRaw) ? "PE" : "CE";
  const expShort = formatExpiryDdMmm(opt.expiry_date);
  const strikeLine = Number.isFinite(strike)
    ? (() => {
        const expSeg = expShort ? `${expShort} ` : "";
        const strikeSeg = `${strike.toLocaleString("en-IN")} ${abbr}`;
        return stock
          ? `${stock} ${expSeg}${strikeSeg}`.trim()
          : `${expSeg}${strikeSeg}`.trim();
      })()
    : expShort
      ? `${expShort} — ${abbr}`
      : `— ${abbr}`;

  const qtyLabel = Number.isFinite(qty)
    ? qty.toLocaleString("en-IN", { maximumFractionDigits: 0 })
    : "—";

  const premLabel = formatPremiumIntegerInr(prem);
  const roiLabel = Number.isFinite(cr) ? `${cr.toFixed(1)}%` : "—";
  const dteRoi = parseNum(opt.days_to_expiry);
  const spanRoi = parseNum(opt.span_margin_total);
  const roiTooltip =
    Number.isFinite(prem) &&
    Number.isFinite(cr) &&
    Number.isFinite(dteRoi) &&
    Number.isFinite(spanRoi) &&
    dteRoi > 0 &&
    spanRoi > 0
      ? buildUncoveredScanRoiTooltip(prem, cr, dteRoi, spanRoi)
      : [
          "Annualised ROI on SPAN (%): (premium ÷ DTE) × 365 ÷ span × 100.",
          "Run an uncovered scan to attach premium, DTE, and span to this card.",
        ].join("\n");

  const fmtPx = (n: number) =>
    Number.isFinite(n) ? n.toLocaleString("en-IN", { maximumFractionDigits: 2 }) : "—";

  const vBar = (
    <span
      className="mx-1.5 h-4 w-px shrink-0 self-center bg-zinc-200 dark:bg-zinc-600"
      aria-hidden
    />
  );

  return (
    <div className="w-fit min-w-0 max-w-[min(100%,25rem)] rounded-md border border-zinc-200/80 bg-white/90 p-2.5 shadow-sm backdrop-blur-sm dark:border-zinc-700/80 dark:bg-zinc-950/60">
      <div className="space-y-2">
        <div className="flex min-w-0 flex-nowrap items-center gap-x-2 overflow-hidden text-sm leading-snug">
          <div className="min-w-0 flex-1 shrink truncate font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
            {strikeLine}
          </div>
          {vBar}
          <div className="shrink-0 tabular-nums font-medium text-zinc-600 dark:text-zinc-300">
            ×{qtyLabel}
          </div>
          {vBar}
          <div
            className="shrink-0 rounded-full bg-emerald-500/14 px-2.5 py-1 text-xs font-semibold tabular-nums text-emerald-800 ring-1 ring-emerald-600/20 dark:bg-emerald-500/12 dark:text-emerald-300 dark:ring-emerald-400/30"
            title={roiTooltip}
          >
            {premLabel}{" "}
            <span className="font-normal text-emerald-700/90 dark:text-emerald-400/90">
              ({roiLabel})
            </span>
          </div>
        </div>

        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 text-xs text-zinc-500 dark:text-zinc-400">
          <span className="shrink-0 tabular-nums whitespace-nowrap">
            <span className="font-medium text-zinc-400 dark:text-zinc-500">
              LTP
            </span>{" "}
            <span className="font-semibold text-zinc-800 dark:text-zinc-200">
              {fmtPx(ltp)}
            </span>
          </span>
          <span className="shrink-0 tabular-nums whitespace-nowrap">
            <span className="font-medium text-zinc-400 dark:text-zinc-500">
              Bid
            </span>{" "}
            {fmtPx(bid)}
            <span className="mx-1 text-zinc-300 dark:text-zinc-600">/</span>
            <span className="font-medium text-zinc-400 dark:text-zinc-500">
              Offer
            </span>{" "}
            {fmtPx(ask)}
          </span>
        </div>

        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 border-t border-zinc-100 pt-1.5 text-xs text-zinc-500 dark:border-zinc-800 dark:text-zinc-400">
          <span className="min-w-0 shrink-0 tabular-nums whitespace-nowrap">
            <span className="font-medium text-zinc-400 dark:text-zinc-500">
              Buy
            </span>{" "}
            <span className="font-semibold text-zinc-800 dark:text-zinc-200">
              {formatBookQtyInL(buyQty)}
            </span>
            <span className="mx-1 text-zinc-300 dark:text-zinc-600">/</span>
            <span className="font-medium text-zinc-400 dark:text-zinc-500">
              Sell
            </span>{" "}
            <span className="font-semibold text-zinc-800 dark:text-zinc-200">
              {formatBookQtyInL(sellQty)}
            </span>
          </span>
          <span className="shrink-0 tabular-nums whitespace-nowrap">
            <span className="font-medium text-zinc-400 dark:text-zinc-500">
              B:S
            </span>{" "}
            <span className="font-semibold text-zinc-800 dark:text-zinc-200">
              {ratioStr}
            </span>
          </span>
        </div>
      </div>

      {!omitAddButton ? (
        <div className="mt-2.5 space-y-2">
          <button
            type="button"
            disabled={legAdded}
            className={
              legAdded
                ? "w-full cursor-not-allowed rounded-lg border border-zinc-200 bg-zinc-100 py-2.5 text-sm font-semibold text-zinc-500 dark:border-zinc-700 dark:bg-zinc-800/80 dark:text-zinc-400"
                : "w-full rounded-lg border border-sky-600 bg-transparent py-2.5 text-sm font-semibold text-sky-700 transition hover:bg-sky-600 hover:text-white focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-500/50 dark:border-sky-500 dark:text-sky-300 dark:hover:bg-sky-600 dark:hover:text-white"
            }
            onClick={onAddLeg}
          >
            {legAdded ? "Leg Added" : "Add Leg"}
          </button>
        </div>
      ) : null}
    </div>
  );
}

type HedgeMatchPayload = {
  Status?: number;
  Error?: string;
  best?: Record<string, unknown> | null;
};

function HedgeSuggestionCard({ best, error }: { best: Record<string, unknown> | null; error?: string }) {
  if (!best) {
    return (
      <div className="flex min-h-[6rem] w-fit min-w-[10rem] max-w-[min(100%,22rem)] flex-1 flex-col justify-center rounded-md border border-dashed border-zinc-300/90 bg-zinc-50/80 p-2.5 text-xs text-zinc-600 dark:border-zinc-600 dark:bg-zinc-900/40 dark:text-zinc-400">
        <div className="font-semibold text-zinc-700 dark:text-zinc-300">Hedge unavailable</div>
        {error ? <p className="mt-1 leading-snug">{error}</p> : null}
      </div>
    );
  }
  const strike = parseNum(best.strike_price);
  const hq = parseNum(best.hedge_quantity);
  const hp = parseNum(best.hedge_premium);
  const offer = parseNum(best.best_offer_price);
  const ltp = parseNum(best.ltp);
  const stock = String(best.stock_code ?? "").trim();
  const rightRaw = String(best.right ?? "");
  const abbr = /^p/i.test(rightRaw) ? "PE" : "CE";
  const expShort = formatExpiryDdMmm(best.expiry_date);
  const strikeLine = Number.isFinite(strike)
    ? `${stock ? `${stock} ` : ""}${expShort ? `${expShort} ` : ""}${strike.toLocaleString("en-IN")} ${abbr}`.trim()
    : "—";

  const fmtPx = (n: number) =>
    Number.isFinite(n) ? n.toLocaleString("en-IN", { maximumFractionDigits: 2 }) : "—";

  return (
    <div className="w-fit min-w-0 max-w-[min(100%,25rem)] flex-1 rounded-md border border-sky-200/70 bg-sky-50/50 p-2.5 shadow-sm dark:border-sky-900/40 dark:bg-sky-950/25">
      <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-sky-800/90 dark:text-sky-300/90">
        Buy hedge (lowest hedge cost)
      </div>
      <div className="text-sm font-semibold leading-snug text-zinc-900 dark:text-zinc-50">
        {strikeLine}
      </div>
      <div className="mt-2 space-y-0.5 text-xs text-zinc-600 dark:text-zinc-400">
        <div className="tabular-nums">
          <span className="font-medium text-zinc-500">Qty</span>{" "}
          {Number.isFinite(hq) ? hq.toLocaleString("en-IN", { maximumFractionDigits: 0 }) : "—"}
        </div>
        <div className="tabular-nums">
          <span className="font-medium text-zinc-500">Hedge premium</span>{" "}
          {formatPremiumIntegerInr(hp)}
        </div>
        <div className="tabular-nums">
          <span className="font-medium text-zinc-500">Offer</span> {fmtPx(offer)}{" "}
          <span className="text-zinc-400">·</span> <span className="font-medium text-zinc-500">LTP</span>{" "}
          {fmtPx(ltp)}
        </div>
      </div>
    </div>
  );
}

function coveredPairNetPremiumLabel(opt: Record<string, unknown>): {
  text: string;
  className: string;
} {
  const prem = parseNum(opt.premium);
  const hm = opt.hedge_match as HedgeMatchPayload | undefined;
  const best = hm?.best;
  const hp = best ? parseNum(best.hedge_premium) : NaN;
  if (!Number.isFinite(prem) || !Number.isFinite(hp)) {
    return { text: "—", className: "text-zinc-900 dark:text-zinc-50" };
  }
  const net = prem - hp;
  if (!Number.isFinite(net)) {
    return { text: "—", className: "text-zinc-900 dark:text-zinc-50" };
  }
  return { text: formatIndianMoneyCompact(net), className: moneyToneClass(net) };
}

function isCoveredPairInLegs(
  opt: Record<string, unknown>,
  legs: StrategyLeg[],
): boolean {
  const k = parseNum(opt.strike_price);
  const rightStr = String(opt.right ?? "Call");
  const right: OptionRight = /^p/i.test(rightStr) ? "Put" : "Call";
  const hm = opt.hedge_match as HedgeMatchPayload | undefined;
  const hedge = hm?.best;
  if (!hedge || !Number.isFinite(k)) return false;
  const hk = parseNum(hedge.strike_price);
  if (!Number.isFinite(hk)) return false;
  const hasShort = legs.some(
    (l) => l.side === "Sell" && l.strike === k && l.right === right,
  );
  const hasBuy = legs.some(
    (l) => l.side === "Buy" && l.strike === hk && l.right === right,
  );
  return hasShort && hasBuy;
}

export default function StrategyBuilderPage() {
  const { secondsRemaining } = useRateLimitCountdown();
  const [stockCode, setStockCode] = useState("");
  const [expiryDate, setExpiryDate] = useState("");
  const [legs, setLegs] = useState<StrategyLeg[]>([]);
  const [executePreviewOpen, setExecutePreviewOpen] = useState(false);
  const [ivShockPct, setIvShockPct] = useState(0);
  const [showGreeks, setShowGreeks] = useState(false);
  const [showToday, setShowToday] = useState(true);
  const [nakedPrompt, setNakedPrompt] = useState<TemplateId | null>(null);
  const [selectedReadymade, setSelectedReadymade] =
    useState<ReadymadeSelection | null>(null);
  const [comingSoonTooltipId, setComingSoonTooltipId] = useState<
    TemplateId | null
  >(null);
  const [scanLimits, setScanLimits] = useState<number | null>(null);
  const [scanTop, setScanTop] = useState<number | null>(null);
  const [scanOtmCallMin, setScanOtmCallMin] = useState(6);
  const [scanOtmCallMax, setScanOtmCallMax] = useState(12);
  const [scanOtmPutMin, setScanOtmPutMin] = useState(6);
  const [scanOtmPutMax, setScanOtmPutMax] = useState(12);
  const [scanProvisionElm, setScanProvisionElm] = useState(false);
  const [bullCallMinPop, setBullCallMinPop] = useState<number | null>(65);
  const [bearCallMinPop, setBearCallMinPop] = useState<number | null>(65);
  const [bearPutMinPop, setBearPutMinPop] = useState<number | null>(65);
  const [bullPutMinPop, setBullPutMinPop] = useState<number | null>(65);
  const [longStrangleDownPct, setLongStrangleDownPct] = useState(-3);
  const [longStrangleUpPct, setLongStrangleUpPct] = useState(3);
  const [longStrangleFetched, setLongStrangleFetched] = useState(false);
  const [longStrangleApplied, setLongStrangleApplied] = useState<{
    downPct: number;
    upPct: number;
    upTarget: number;
    downTarget: number;
    callStrike: number;
    putStrike: number;
    callPremium: number | undefined;
    putPremium: number | undefined;
    atmIvPct: number;
    ivRangeLow: number;
    ivRangeHigh: number;
    maxCallOiStrike: number | null;
    maxPutOiStrike: number | null;
  } | null>(null);
  const [shortStrangleDownPct, setShortStrangleDownPct] = useState(-3);
  const [shortStrangleUpPct, setShortStrangleUpPct] = useState(3);
  const [shortStrangleFetched, setShortStrangleFetched] = useState(false);
  const [shortStrangleApplied, setShortStrangleApplied] = useState<{
    downPct: number;
    upPct: number;
    upTarget: number;
    downTarget: number;
    callStrike: number;
    putStrike: number;
    callPremium: number | undefined;
    putPremium: number | undefined;
    atmIvPct: number;
    ivRangeLow: number;
    ivRangeHigh: number;
    maxCallOiStrike: number | null;
    maxPutOiStrike: number | null;
  } | null>(null);
  const [ironCondorDownPct, setIronCondorDownPct] = useState(-3);
  const [ironCondorUpPct, setIronCondorUpPct] = useState(3);
  const [ironCondorFetched, setIronCondorFetched] = useState(false);
  const [ironCondorApplied, setIronCondorApplied] = useState<{
    downPct: number;
    upPct: number;
    longPutStrike: number;
    shortPutStrike: number;
    shortCallStrike: number;
    longCallStrike: number;
    longPutPremium: number | undefined;
    shortPutPremium: number | undefined;
    shortCallPremium: number | undefined;
    longCallPremium: number | undefined;
    atmIvPct: number;
    ivRangeLow: number;
    ivRangeHigh: number;
    maxCallOiStrike: number | null;
    maxPutOiStrike: number | null;
  } | null>(null);
  const [ironButterflyWingPct, setIronButterflyWingPct] = useState(5);
  const [ironButterflyFetched, setIronButterflyFetched] = useState(false);
  const [ironButterflyApplied, setIronButterflyApplied] = useState<{
    wingPct: number;
    lowStrike: number;
    midStrike: number;
    highStrike: number;
    longPutPremium: number | undefined;
    midPutPremium: number | undefined;
    midCallPremium: number | undefined;
    longCallPremium: number | undefined;
    atmIvPct: number;
    ivRangeLow: number;
    ivRangeHigh: number;
    maxCallOiStrike: number | null;
    maxPutOiStrike: number | null;
  } | null>(null);
  const [butterflyWingPct, setButterflyWingPct] = useState(5);
  const [butterflyFetched, setButterflyFetched] = useState(false);
  const [butterflyApplied, setButterflyApplied] = useState<{
    wingPct: number;
    lowStrike: number;
    midStrike: number;
    highStrike: number;
    lowPremium: number | undefined;
    midPremium: number | undefined;
    highPremium: number | undefined;
    atmIvPct: number;
    ivRangeLow: number;
    ivRangeHigh: number;
    maxCallOiStrike: number | null;
    maxPutOiStrike: number | null;
  } | null>(null);
  const [uncoveredScanResult, setUncoveredScanResult] =
    useState<UncoveredScanResponse | null>(null);
  const [scanError, setScanError] = useState<string | null>(null);
  useEffect(() => {
    if (!comingSoonTooltipId) return;
    const t = window.setTimeout(() => setComingSoonTooltipId(null), 2400);
    return () => window.clearTimeout(t);
  }, [comingSoonTooltipId]);
  const selectedReadymadeRef = useRef<ReadymadeSelection | null>(null);
  selectedReadymadeRef.current = selectedReadymade;
  const [segmentExchange, setSegmentExchange] = useState<"NFO" | "BFO">("NFO");
  const { chunkQty, setChunkQty, defaultsQuery: chunkDefaultsQ, chunkReady } =
    useBreakChunkQty({
      stockCode,
      exchangeCode: segmentExchange,
      expiryDisplay: expiryDate,
      enabled: executePreviewOpen,
    });
  const [strategyMarginValidSig, setStrategyMarginValidSig] = useState<
    string | null
  >(null);
  const [legMarginCache, setLegMarginCache] = useState<
    Record<
      string,
      { lots: number; span: number | null; error?: string }
    >
  >({});
  const [legMarginFetchingId, setLegMarginFetchingId] = useState<string | null>(
    null,
  );
  const buildOwnChainScrollRef = useRef<HTMLDivElement>(null);
  const parametersSectionRef = useRef<HTMLElement | null>(null);
  const proposedTradesSectionRef = useRef<HTMLElement | null>(null);
  const legsSectionRef = useRef<HTMLElement | null>(null);
  const [proposedTradesInView, setProposedTradesInView] = useState(false);
  const [legsInView, setLegsInView] = useState(false);
  const onSegmentChange = (ex: "NFO" | "BFO") => {
    if (ex === segmentExchange) return;
    setSegmentExchange(ex);
    setStockCode("");
    setExpiryDate("");
    setLegs([]);
    setSelectedReadymade(null);
    setUncoveredScanResult(null);
  };

  useEffect(() => {
    if (
      selectedReadymade === "naked-shorts" ||
      selectedReadymade === "covered-shorts"
    ) {
      setScanTop((t) =>
        t != null && t > STRATEGY_BUILDER_OPTIONS_TO_LIST_MAX
          ? STRATEGY_BUILDER_OPTIONS_TO_LIST_MAX
          : t,
      );
    }
  }, [selectedReadymade]);

  const uq = useQuery({
    queryKey: ["strategy-builder", "underlyings", segmentExchange],
    queryFn: () =>
      apiClient.get<UnderlyingsApiResponse>(
        `/strategy-builder/underlyings?exchange_code=${segmentExchange}`,
      ),
  });

  const expiryOptions = useMemo(() => {
    const raw = uq.data?.underlyings ?? [];
    const u = raw.find((x) => x.stock_code === stockCode);
    return sortExpiryDatesAsc(u?.expiry_dates ?? []);
  }, [uq.data?.underlyings, stockCode]);

  const uncoveredScanMut = useMutation({
    mutationFn: async () => {
      const ex = segmentExchange;
      if (scanLimits == null || scanTop == null) {
        throw new Error("Margin to deploy and Options to list are required");
      }
      if (scanOtmCallMin > scanOtmCallMax || scanOtmPutMin > scanOtmPutMax) {
        throw new Error("OTM range invalid: min must be less than or equal to max");
      }
      const q = new URLSearchParams({
        stock_code: stockCode.trim(),
        expiry_date: expiryDate.trim(),
        limits: String(scanLimits),
        top: String(scanTop),
        otm_call_min: String(scanOtmCallMin),
        otm_call_max: String(scanOtmCallMax),
        otm_put_min: String(scanOtmPutMin),
        otm_put_max: String(scanOtmPutMax),
        exchange_code: ex,
        strategy_builder: "true",
      });
      if (scanProvisionElm) q.set("provision_elm", "on");
      const mode = selectedReadymadeRef.current;
      const path =
        mode === "covered-shorts"
          ? "/uncovered-shorts/covered-shorts-scan"
          : "/uncovered-shorts/scan";
      return apiClient.get<UncoveredScanResponse>(
        `${path}?${q.toString()}`,
      );
    },
    onSuccess: (data) => {
      const ceStatus = Number(data.ce_options?.Status ?? 0);
      const peStatus = Number(data.pe_options?.Status ?? 0);
      const ceErr = String(data.ce_options?.Error ?? "");
      const peErr = String(data.pe_options?.Error ?? "");
      const mergedErr = [ceErr, peErr].filter(Boolean).join(" ");
      if (
        ceStatus === 429 ||
        peStatus === 429 ||
        /limit exceed|api call per minute|daily limit|5000/i.test(mergedErr)
      ) {
        setUncoveredScanResult(null);
        setScanError(
          "Breeze API rate/daily limit reached. Please slow down scan activity and retry after some time.",
        );
        return;
      }
      setUncoveredScanResult(data);
      setScanError(null);
    },
    onError: (e) => {
      setScanError(e instanceof Error ? e.message : "Scan failed");
    },
  });

  const cq = useQuery({
    queryKey: [
      "strategy-builder",
      "chain",
      stockCode,
      expiryDate,
      segmentExchange,
    ],
    queryFn: ({ signal }) => {
      const q = new URLSearchParams({
        stock_code: stockCode,
        exchange_code: segmentExchange,
        expiry_date: expiryDate,
      });
      return apiClient.get<ChainApiResponse>(
        `/strategy-builder/chain?${q.toString()}`,
        signal,
      );
    },
    enabled: Boolean(stockCode && expiryDate),
  });

  const chainSuccess = cq.data?.Status === 200 ? cq.data?.Success : null;
  const lotSize = useMemo(() => {
    if (!chainSuccess?.chain_rows?.length) return 1;
    const row = chainSuccess.chain_rows[0];
    const ls = parseNum(row.call?.lot_size) || parseNum(row.put?.lot_size);
    return Number.isFinite(ls) && ls > 0 ? Math.round(ls) : 1;
  }, [chainSuccess]);

  const strategyExecuteLegs = useMemo(
    () =>
      legs
        .filter((l) => l.lots > 0)
        .map((l) => ({
          strike: l.strike,
          right: l.right,
          side: l.side,
          quantity: Math.round(l.lots * lotSize),
          premiumPerUnit: l.premiumPerUnit ?? 0,
        })),
    [legs, lotSize],
  );

  const strikes = useMemo(
    () =>
      (chainSuccess?.chain_rows ?? [])
        .map((r) => r.strike_price)
        .sort((a, b) => a - b),
    [chainSuccess],
  );

  useEffect(() => {
    if (
      (selectedReadymade !== "build-your-own" &&
        selectedReadymade !== "bull_call_spread" &&
        selectedReadymade !== "bear_call_spread" &&
        selectedReadymade !== "bear_put_spread" &&
        selectedReadymade !== "bull_put_spread" &&
        selectedReadymade !== "long_straddle" &&
        selectedReadymade !== "short_straddle" &&
        selectedReadymade !== "long_strangle" &&
        selectedReadymade !== "short_strangle" &&
        selectedReadymade !== "iron_condor" &&
        selectedReadymade !== "iron_butterfly" &&
        selectedReadymade !== "long_call_butterfly") ||
      !chainSuccess
    ) {
      return;
    }
    if (selectedReadymade === "long_strangle" && !longStrangleFetched) return;
    if (selectedReadymade === "short_strangle" && !shortStrangleFetched) return;
    if (selectedReadymade === "iron_condor" && !ironCondorFetched) return;
    if (selectedReadymade === "iron_butterfly" && !ironButterflyFetched) return;
    if (selectedReadymade === "long_call_butterfly" && !butterflyFetched) return;
    const t = requestAnimationFrame(() => {
      scrollOptionChainAtmIntoView(buildOwnChainScrollRef.current);
    });
    return () => cancelAnimationFrame(t);
  }, [
    selectedReadymade,
    chainSuccess,
    longStrangleFetched,
    shortStrangleFetched,
    ironCondorFetched,
    ironButterflyFetched,
    butterflyFetched,
  ]);

  const hasProposedTradesChainRows =
    (chainSuccess?.chain_rows?.length ?? 0) > 0;

  useEffect(() => {
    const proposedEl = proposedTradesSectionRef.current;
    const legsEl = legsSectionRef.current;
    if (!hasProposedTradesChainRows || !proposedEl || !legsEl) {
      setProposedTradesInView(false);
      setLegsInView(false);
      return;
    }
    const obsProposed = new IntersectionObserver(
      ([e]) => setProposedTradesInView(Boolean(e?.isIntersecting)),
      { threshold: 0 },
    );
    const obsLegs = new IntersectionObserver(
      ([e]) => setLegsInView(Boolean(e?.isIntersecting)),
      { threshold: 0 },
    );
    obsProposed.observe(proposedEl);
    obsLegs.observe(legsEl);
    return () => {
      obsProposed.disconnect();
      obsLegs.disconnect();
    };
  }, [hasProposedTradesChainRows]);

  const showChainJumpNav =
    hasProposedTradesChainRows && proposedTradesInView && !legsInView;

  const scrollToStrategySection = useCallback(
    (ref: RefObject<HTMLElement | null>) => {
      const el = ref.current;
      if (!el) return;
      const prefersReduced =
        typeof window !== "undefined" &&
        window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      el.scrollIntoView({
        behavior: prefersReduced ? "auto" : "smooth",
        block: "start",
      });
    },
    [],
  );

  const T = expiryDisplayToYears(expiryDate || "01-Jan-2099");
  const baseSigma = chainSuccess ? atmSigmaFromChain(chainSuccess, T) : 0.22;
  const sigma = baseSigma * (1 + ivShockPct / 100);

  const spot = chainSuccess?.spot_price ?? null;
  const { minS, maxS } = useMemo(() => {
    if (spot != null && Number.isFinite(spot) && spot > 0) {
      return payoffChartSpotDomain(spot);
    }
    if (!strikes.length) {
      return { minS: 0, maxS: 1 };
    }
    const lo = Math.min(...strikes);
    const hi = Math.max(...strikes);
    const pad = (hi - lo) * 0.15;
    return { minS: Math.max(0, lo - pad), maxS: hi + pad };
  }, [strikes, spot]);

  const { xs, ys, summary, xsToday, ysToday } = useMemo(() => {
    const steps = 80;
    const L = legs;
    if (!L.length) {
      return {
        xs: [] as number[],
        ys: [] as number[],
        summary: summarizePayoffExact([], lotSize, spot),
        xsToday: [] as number[],
        ysToday: [] as number[],
      };
    }
    const { xs: x1, ys: y1 } = scanPayoffCurve(minS, maxS, steps, L, lotSize);
    const sum = summarizePayoffExact(L, lotSize, spot);
    let xt: number[] = [];
    let yt: number[] = [];
    if (showToday && spot != null && T > 0 && L.length) {
      const r = scanMarkToModelCurve(
        minS,
        maxS,
        steps,
        L,
        lotSize,
        T,
        sigma,
      );
      xt = r.xs;
      yt = r.ys;
    }
    return {
      xs: x1,
      ys: y1,
      summary: sum,
      xsToday: xt,
      ysToday: yt,
    };
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

  /** Structural identity only — qty/price edits do not invalidate the query key. */
  const marginLegKeyStructural = useMemo(
    () =>
      JSON.stringify({
        segmentExchange,
        stockCode,
        expiryDate,
        lotSize,
        legs: legs.map((l) => ({
          id: l.id,
          strike: l.strike,
          right: l.right,
          side: l.side,
        })),
      }),
    [segmentExchange, stockCode, expiryDate, lotSize, legs],
  );

  const legsWithQtyForMargin = useMemo(
    () => legs.filter((l) => l.lots > 0),
    [legs],
  );

  const marginQ = useQuery({
    queryKey: ["strategy-builder", "margin", marginLegKeyStructural],
    queryFn: () =>
      apiClient.post<MarginApiResponse>("/strategy-builder/margin", {
        legs: legsWithQtyForMargin.map((l) => ({
          stock_code: stockCode,
          exchange_code: segmentExchange,
          expiry_date: expiryDate,
          product_type: "Options",
          right: l.right,
          strike_price: String(l.strike),
          quantity: String(Math.round(l.lots * lotSize)),
          price: String(l.premiumPerUnit ?? 0),
          action: l.side,
        })),
      }),
    enabled: Boolean(
      stockCode && expiryDate && legsWithQtyForMargin.length > 0,
    ),
    staleTime: 5000,
  });

  const marginState = marginQ.data;
  const spanMargin = parseSpanMarginFromResponse(marginState);
  const strategyBuilderMarginWarnings = useMemo(() => {
    const raw = (marginState?.Success as { warnings?: unknown } | undefined)?.warnings;
    if (!Array.isArray(raw)) return [];
    return raw
      .map((w) => {
        if (!w || typeof w !== "object") return "";
        return String((w as Record<string, unknown>).message ?? "").trim();
      })
      .filter(Boolean);
  }, [marginState]);
  const scanFallbackWarnings = useMemo(() => {
    const out: string[] = [];
    const collect = (side: UncoveredSidePayload | undefined) => {
      const warnings = (side as { Warnings?: unknown } | undefined)?.Warnings;
      if (!Array.isArray(warnings)) return;
      for (const w of warnings) {
        if (!w || typeof w !== "object") continue;
        const msg = String((w as Record<string, unknown>).message ?? "").trim();
        if (msg) out.push(msg);
      }
    };
    collect(uncoveredScanResult?.ce_options);
    collect(uncoveredScanResult?.pe_options);
    return out;
  }, [uncoveredScanResult]);

  const { refetch: refetchStrategyMargin } = marginQ;

  useEffect(() => {
    if (marginQ.isFetching || !marginQ.isSuccess || !marginState) return;
    // `legs` intentionally read from closure when a fetch completes (dataUpdatedAt),
    // not listed in deps — listing `legs` would incorrectly mark margin fresh on qty edits.
    setStrategyMarginValidSig(legsQtySignature(legs));
    // eslint-disable-next-line react-hooks/exhaustive-deps -- sync valid sig only when margin fetch finishes
  }, [marginQ.isFetching, marginQ.dataUpdatedAt, marginQ.isSuccess, marginState]);

  const strategyMarginQtyStale =
    marginQ.isSuccess &&
    strategyMarginValidSig !== null &&
    strategyMarginValidSig !== legsQtySignature(legs);

  useEffect(() => {
    if (!executePreviewOpen || legs.length === 0) return;
    void refetchStrategyMargin();
  }, [executePreviewOpen, legs.length, refetchStrategyMargin]);

  const fetchLegMargin = useCallback(
    async (leg: StrategyLeg) => {
      if (!stockCode || !expiryDate || leg.lots <= 0) return;
      setLegMarginFetchingId(leg.id);
      try {
        const res = await apiClient.post<MarginApiResponse>(
          "/strategy-builder/margin",
          {
            legs: [
              {
                stock_code: stockCode,
                exchange_code: segmentExchange,
                expiry_date: expiryDate,
                product_type: "Options",
                right: leg.right,
                strike_price: String(leg.strike),
                quantity: String(Math.round(leg.lots * lotSize)),
                price: String(leg.premiumPerUnit ?? 0),
                action: leg.side,
              },
            ],
          },
        );
        const span = parseSpanMarginFromResponse(res);
        setLegMarginCache((prev) => ({
          ...prev,
          [leg.id]: {
            lots: leg.lots,
            span,
            error:
              span != null && Number.isFinite(span)
                ? undefined
                : String(res.Error ?? "—"),
          },
        }));
      } catch {
        setLegMarginCache((prev) => ({
          ...prev,
          [leg.id]: {
            lots: leg.lots,
            span: null,
            error: "Margin request failed",
          },
        }));
      } finally {
        setLegMarginFetchingId(null);
      }
    },
    [stockCode, expiryDate, segmentExchange, lotSize],
  );

  const chainReady = Boolean(chainSuccess);
  const hasStrategyLegs = legs.length > 0;
  const bullCallSuggestion = useMemo(() => {
    if (
      selectedReadymade !== "bull_call_spread" ||
      !chainSuccess ||
      spot == null ||
      T <= 0
    ) {
      return null;
    }

    const minPop = Math.min(99, Math.max(1, bullCallMinPop ?? 65));
    const stdMove = spot * baseSigma * Math.sqrt(T);
    const rangeLow = Math.max(0, spot - stdMove);
    const rangeHigh = spot + stdMove;
    const rows = [...chainSuccess.chain_rows].sort(
      (a, b) => a.strike_price - b.strike_price,
    );
    const callRows = rows.filter((r) => r.call);
    if (!callRows.length) return null;

    const sellRow =
      callRows.find((r) => r.strike_price > rangeHigh) ??
      callRows[callRows.length - 1];
    if (!sellRow?.call) return null;
    const sellStrike = sellRow.strike_price;
    const sellPremium = optionMidFromApiLeg(sellRow.call);
    if (sellPremium == null) return null;

    const buyCandidates = callRows.filter((r) => r.strike_price < sellStrike);
    if (!buyCandidates.length) return null;

    let chosen:
      | { strike: number; premium: number; pop: number }
      | null = null;
    let bestPopCandidate:
      | { strike: number; premium: number; pop: number }
      | null = null;

    for (let i = buyCandidates.length - 1; i >= 0; i -= 1) {
      const row = buyCandidates[i];
      const premium = optionMidFromApiLeg(row.call);
      if (premium == null) continue;
      const legsForPop: StrategyLeg[] = [
        {
          id: "auto-bull-call-buy",
          side: "Buy",
          right: "Call",
          strike: row.strike_price,
          lots: 1,
          premiumPerUnit: premium,
        },
        {
          id: "auto-bull-call-sell",
          side: "Sell",
          right: "Call",
          strike: sellStrike,
          lots: 1,
          premiumPerUnit: sellPremium,
        },
      ];
      const candidatePop = estimateProbabilityOfProfit(
        spot,
        T,
        baseSigma,
        legsForPop,
        lotSize,
      );
      const candidate = { strike: row.strike_price, premium, pop: candidatePop };
      if (!bestPopCandidate || candidate.pop > bestPopCandidate.pop) {
        bestPopCandidate = candidate;
      }
      if (candidatePop >= minPop) {
        chosen = candidate;
        break;
      }
    }

    const buy = chosen ?? bestPopCandidate;
    if (!buy) return null;

    let maxCallOi = -Infinity;
    let maxPutOi = -Infinity;
    let maxCallOiStrike: number | null = null;
    let maxPutOiStrike: number | null = null;
    for (const r of rows) {
      const cOi = parseNum(r.call?.open_interest);
      if (Number.isFinite(cOi) && cOi > maxCallOi) {
        maxCallOi = cOi;
        maxCallOiStrike = r.strike_price;
      }
      const pOi = parseNum(r.put?.open_interest);
      if (Number.isFinite(pOi) && pOi > maxPutOi) {
        maxPutOi = pOi;
        maxPutOiStrike = r.strike_price;
      }
    }

    return {
      minPop,
      atmIvPct: baseSigma * 100,
      ivRangeLow: rangeLow,
      ivRangeHigh: rangeHigh,
      buyStrike: buy.strike,
      sellStrike,
      buyPremium: buy.premium,
      sellPremium,
      modelPop: buy.pop,
      maxCallOiStrike,
      maxPutOiStrike,
    };
  }, [selectedReadymade, chainSuccess, spot, T, bullCallMinPop, baseSigma, lotSize]);

  const fetchBullCallTrades = useCallback(() => {
    if (!bullCallSuggestion) return;
    setLegs([
      {
        id: "auto-bull-call-buy",
        side: "Buy",
        right: "Call",
        strike: bullCallSuggestion.buyStrike,
        lots: 1,
        premiumPerUnit: bullCallSuggestion.buyPremium,
      },
      {
        id: "auto-bull-call-sell",
        side: "Sell",
        right: "Call",
        strike: bullCallSuggestion.sellStrike,
        lots: 1,
        premiumPerUnit: bullCallSuggestion.sellPremium,
      },
    ]);
  }, [bullCallSuggestion]);

  const bearCallSuggestion = useMemo(() => {
    if (
      selectedReadymade !== "bear_call_spread" ||
      !chainSuccess ||
      spot == null ||
      T <= 0
    ) {
      return null;
    }

    const minPop = Math.min(99, Math.max(1, bearCallMinPop ?? 65));
    const stdMove = spot * baseSigma * Math.sqrt(T);
    const rangeLow = Math.max(0, spot - stdMove);
    const rangeHigh = spot + stdMove;
    const rows = [...chainSuccess.chain_rows].sort(
      (a, b) => a.strike_price - b.strike_price,
    );
    const callRows = rows.filter((r) => r.call);
    if (!callRows.length) return null;

    const buyRow =
      callRows.find((r) => r.strike_price > rangeHigh) ??
      callRows[callRows.length - 1];
    if (!buyRow?.call) return null;
    const buyStrike = buyRow.strike_price;
    const buyPremium = optionMidFromApiLeg(buyRow.call);
    if (buyPremium == null) return null;

    const sellCandidates = callRows.filter((r) => r.strike_price < buyStrike);
    if (!sellCandidates.length) return null;

    let chosen:
      | { strike: number; premium: number; pop: number }
      | null = null;
    let bestPopCandidate:
      | { strike: number; premium: number; pop: number }
      | null = null;

    for (let i = sellCandidates.length - 1; i >= 0; i -= 1) {
      const row = sellCandidates[i];
      const premium = optionMidFromApiLeg(row.call);
      if (premium == null) continue;
      const legsForPop: StrategyLeg[] = [
        {
          id: "auto-bear-call-sell",
          side: "Sell",
          right: "Call",
          strike: row.strike_price,
          lots: 1,
          premiumPerUnit: premium,
        },
        {
          id: "auto-bear-call-buy",
          side: "Buy",
          right: "Call",
          strike: buyStrike,
          lots: 1,
          premiumPerUnit: buyPremium,
        },
      ];
      const candidatePop = estimateProbabilityOfProfit(
        spot,
        T,
        baseSigma,
        legsForPop,
        lotSize,
      );
      const candidate = { strike: row.strike_price, premium, pop: candidatePop };
      if (!bestPopCandidate || candidate.pop > bestPopCandidate.pop) {
        bestPopCandidate = candidate;
      }
      if (candidatePop >= minPop) {
        chosen = candidate;
        break;
      }
    }

    const sell = chosen ?? bestPopCandidate;
    if (!sell) return null;

    let maxCallOi = -Infinity;
    let maxPutOi = -Infinity;
    let maxCallOiStrike: number | null = null;
    let maxPutOiStrike: number | null = null;
    for (const r of rows) {
      const cOi = parseNum(r.call?.open_interest);
      if (Number.isFinite(cOi) && cOi > maxCallOi) {
        maxCallOi = cOi;
        maxCallOiStrike = r.strike_price;
      }
      const pOi = parseNum(r.put?.open_interest);
      if (Number.isFinite(pOi) && pOi > maxPutOi) {
        maxPutOi = pOi;
        maxPutOiStrike = r.strike_price;
      }
    }

    return {
      minPop,
      atmIvPct: baseSigma * 100,
      ivRangeLow: rangeLow,
      ivRangeHigh: rangeHigh,
      buyStrike,
      sellStrike: sell.strike,
      buyPremium,
      sellPremium: sell.premium,
      modelPop: sell.pop,
      maxCallOiStrike,
      maxPutOiStrike,
    };
  }, [selectedReadymade, chainSuccess, spot, T, bearCallMinPop, baseSigma, lotSize]);

  const fetchBearCallTrades = useCallback(() => {
    if (!bearCallSuggestion) return;
    setLegs([
      {
        id: "auto-bear-call-sell",
        side: "Sell",
        right: "Call",
        strike: bearCallSuggestion.sellStrike,
        lots: 1,
        premiumPerUnit: bearCallSuggestion.sellPremium,
      },
      {
        id: "auto-bear-call-buy",
        side: "Buy",
        right: "Call",
        strike: bearCallSuggestion.buyStrike,
        lots: 1,
        premiumPerUnit: bearCallSuggestion.buyPremium,
      },
    ]);
  }, [bearCallSuggestion]);

  const bearPutSuggestion = useMemo(() => {
    if (
      selectedReadymade !== "bear_put_spread" ||
      !chainSuccess ||
      spot == null ||
      T <= 0
    ) {
      return null;
    }

    const minPop = Math.min(99, Math.max(1, bearPutMinPop ?? 65));
    const stdMove = spot * baseSigma * Math.sqrt(T);
    const rangeLow = Math.max(0, spot - stdMove);
    const rangeHigh = spot + stdMove;
    const rows = [...chainSuccess.chain_rows].sort(
      (a, b) => a.strike_price - b.strike_price,
    );
    const putRows = rows.filter((r) => r.put);
    if (!putRows.length) return null;

    const sellRow =
      [...putRows].reverse().find((r) => r.strike_price < rangeLow) ??
      putRows[0];
    if (!sellRow?.put) return null;
    const sellStrike = sellRow.strike_price;
    const sellPremium = optionMidFromApiLeg(sellRow.put);
    if (sellPremium == null) return null;

    const buyCandidates = putRows.filter((r) => r.strike_price > sellStrike);
    if (!buyCandidates.length) return null;

    let chosen:
      | { strike: number; premium: number; pop: number }
      | null = null;
    let bestPopCandidate:
      | { strike: number; premium: number; pop: number }
      | null = null;

    for (let i = 0; i < buyCandidates.length; i += 1) {
      const row = buyCandidates[i];
      const premium = optionMidFromApiLeg(row.put);
      if (premium == null) continue;
      const legsForPop: StrategyLeg[] = [
        {
          id: "auto-bear-put-buy",
          side: "Buy",
          right: "Put",
          strike: row.strike_price,
          lots: 1,
          premiumPerUnit: premium,
        },
        {
          id: "auto-bear-put-sell",
          side: "Sell",
          right: "Put",
          strike: sellStrike,
          lots: 1,
          premiumPerUnit: sellPremium,
        },
      ];
      const candidatePop = estimateProbabilityOfProfit(
        spot,
        T,
        baseSigma,
        legsForPop,
        lotSize,
      );
      const candidate = { strike: row.strike_price, premium, pop: candidatePop };
      if (!bestPopCandidate || candidate.pop > bestPopCandidate.pop) {
        bestPopCandidate = candidate;
      }
      if (candidatePop >= minPop) {
        chosen = candidate;
        break;
      }
    }

    const buy = chosen ?? bestPopCandidate;
    if (!buy) return null;

    let maxCallOi = -Infinity;
    let maxPutOi = -Infinity;
    let maxCallOiStrike: number | null = null;
    let maxPutOiStrike: number | null = null;
    for (const r of rows) {
      const cOi = parseNum(r.call?.open_interest);
      if (Number.isFinite(cOi) && cOi > maxCallOi) {
        maxCallOi = cOi;
        maxCallOiStrike = r.strike_price;
      }
      const pOi = parseNum(r.put?.open_interest);
      if (Number.isFinite(pOi) && pOi > maxPutOi) {
        maxPutOi = pOi;
        maxPutOiStrike = r.strike_price;
      }
    }

    return {
      minPop,
      atmIvPct: baseSigma * 100,
      ivRangeLow: rangeLow,
      ivRangeHigh: rangeHigh,
      buyStrike: buy.strike,
      sellStrike,
      buyPremium: buy.premium,
      sellPremium,
      modelPop: buy.pop,
      maxCallOiStrike,
      maxPutOiStrike,
    };
  }, [selectedReadymade, chainSuccess, spot, T, bearPutMinPop, baseSigma, lotSize]);

  const fetchBearPutTrades = useCallback(() => {
    if (!bearPutSuggestion) return;
    setLegs([
      {
        id: "auto-bear-put-buy",
        side: "Buy",
        right: "Put",
        strike: bearPutSuggestion.buyStrike,
        lots: 1,
        premiumPerUnit: bearPutSuggestion.buyPremium,
      },
      {
        id: "auto-bear-put-sell",
        side: "Sell",
        right: "Put",
        strike: bearPutSuggestion.sellStrike,
        lots: 1,
        premiumPerUnit: bearPutSuggestion.sellPremium,
      },
    ]);
  }, [bearPutSuggestion]);

  const bullPutSuggestion = useMemo(() => {
    if (
      selectedReadymade !== "bull_put_spread" ||
      !chainSuccess ||
      spot == null ||
      T <= 0
    ) {
      return null;
    }

    const minPop = Math.min(99, Math.max(1, bullPutMinPop ?? 65));
    const stdMove = spot * baseSigma * Math.sqrt(T);
    const rangeLow = Math.max(0, spot - stdMove);
    const rangeHigh = spot + stdMove;
    const rows = [...chainSuccess.chain_rows].sort(
      (a, b) => a.strike_price - b.strike_price,
    );
    const putRows = rows.filter((r) => r.put);
    if (!putRows.length) return null;

    const sellRow =
      [...putRows].reverse().find((r) => r.strike_price < rangeLow) ??
      putRows[putRows.length - 1];
    if (!sellRow?.put) return null;
    const sellStrike = sellRow.strike_price;
    const sellPremium = optionMidFromApiLeg(sellRow.put);
    if (sellPremium == null) return null;

    const buyCandidates = putRows.filter((r) => r.strike_price < sellStrike);
    if (!buyCandidates.length) return null;

    let chosen:
      | { strike: number; premium: number; pop: number }
      | null = null;
    let bestPopCandidate:
      | { strike: number; premium: number; pop: number }
      | null = null;

    for (let i = buyCandidates.length - 1; i >= 0; i -= 1) {
      const row = buyCandidates[i];
      const premium = optionMidFromApiLeg(row.put);
      if (premium == null) continue;
      const legsForPop: StrategyLeg[] = [
        {
          id: "auto-bull-put-sell",
          side: "Sell",
          right: "Put",
          strike: sellStrike,
          lots: 1,
          premiumPerUnit: sellPremium,
        },
        {
          id: "auto-bull-put-buy",
          side: "Buy",
          right: "Put",
          strike: row.strike_price,
          lots: 1,
          premiumPerUnit: premium,
        },
      ];
      const candidatePop = estimateProbabilityOfProfit(
        spot,
        T,
        baseSigma,
        legsForPop,
        lotSize,
      );
      const candidate = { strike: row.strike_price, premium, pop: candidatePop };
      if (!bestPopCandidate || candidate.pop > bestPopCandidate.pop) {
        bestPopCandidate = candidate;
      }
      if (candidatePop >= minPop) {
        chosen = candidate;
        break;
      }
    }

    const buy = chosen ?? bestPopCandidate;
    if (!buy) return null;

    let maxCallOi = -Infinity;
    let maxPutOi = -Infinity;
    let maxCallOiStrike: number | null = null;
    let maxPutOiStrike: number | null = null;
    for (const r of rows) {
      const cOi = parseNum(r.call?.open_interest);
      if (Number.isFinite(cOi) && cOi > maxCallOi) {
        maxCallOi = cOi;
        maxCallOiStrike = r.strike_price;
      }
      const pOi = parseNum(r.put?.open_interest);
      if (Number.isFinite(pOi) && pOi > maxPutOi) {
        maxPutOi = pOi;
        maxPutOiStrike = r.strike_price;
      }
    }

    return {
      minPop,
      atmIvPct: baseSigma * 100,
      ivRangeLow: rangeLow,
      ivRangeHigh: rangeHigh,
      buyStrike: buy.strike,
      sellStrike,
      buyPremium: buy.premium,
      sellPremium,
      modelPop: buy.pop,
      maxCallOiStrike,
      maxPutOiStrike,
    };
  }, [selectedReadymade, chainSuccess, spot, T, bullPutMinPop, baseSigma, lotSize]);

  const fetchBullPutTrades = useCallback(() => {
    if (!bullPutSuggestion) return;
    setLegs([
      {
        id: "auto-bull-put-sell",
        side: "Sell",
        right: "Put",
        strike: bullPutSuggestion.sellStrike,
        lots: 1,
        premiumPerUnit: bullPutSuggestion.sellPremium,
      },
      {
        id: "auto-bull-put-buy",
        side: "Buy",
        right: "Put",
        strike: bullPutSuggestion.buyStrike,
        lots: 1,
        premiumPerUnit: bullPutSuggestion.buyPremium,
      },
    ]);
  }, [bullPutSuggestion]);

  const atmStraddleProposedSummary = useMemo(() => {
    if (
      (selectedReadymade !== "long_straddle" &&
        selectedReadymade !== "short_straddle") ||
      !chainSuccess?.chain_rows?.length ||
      spot == null ||
      spot <= 0 ||
      T <= 0
    ) {
      return null;
    }
    const rows = [...chainSuccess.chain_rows].sort(
      (a, b) => a.strike_price - b.strike_price,
    );
    const stdMove = spot * baseSigma * Math.sqrt(T);
    const ivRangeLow = Math.max(0, spot - stdMove);
    const ivRangeHigh = spot + stdMove;

    let maxCallOi = -Infinity;
    let maxPutOi = -Infinity;
    let maxCallOiStrike: number | null = null;
    let maxPutOiStrike: number | null = null;
    for (const r of rows) {
      const cOi = parseNum(r.call?.open_interest);
      if (Number.isFinite(cOi) && cOi > maxCallOi) {
        maxCallOi = cOi;
        maxCallOiStrike = r.strike_price;
      }
      const pOi = parseNum(r.put?.open_interest);
      if (Number.isFinite(pOi) && pOi > maxPutOi) {
        maxPutOi = pOi;
        maxPutOiStrike = r.strike_price;
      }
    }

    const ctx = buildTemplateContext(chainSuccess.chain_rows, spot);
    const atmStrike =
      chainSuccess.atm_strike ??
      (ctx != null ? ctx.strikes[ctx.atmIdx] : null);
    if (atmStrike == null) return null;

    return {
      atmIvPct: baseSigma * 100,
      ivRangeLow,
      ivRangeHigh,
      maxCallOiStrike,
      maxPutOiStrike,
      atmStrike,
    };
  }, [selectedReadymade, chainSuccess, spot, T, baseSigma]);

  const longStrangleSuggestion = useMemo(() => {
    if (selectedReadymade !== "long_strangle") return null;
    if (
      !chainSuccess?.chain_rows?.length ||
      spot == null ||
      spot <= 0 ||
      T <= 0
    ) {
      return null;
    }

    const rows = [...chainSuccess.chain_rows].sort(
      (a, b) => a.strike_price - b.strike_price,
    );
    if (!rows.length) return null;

    const downPct = Math.min(
      -1,
      Math.max(STRANGLE_MOVE_MIN, longStrangleDownPct),
    );
    const upPct = Math.min(
      STRANGLE_MOVE_MAX,
      Math.max(1, longStrangleUpPct),
    );
    const upTarget = spot * (1 + upPct / 100);
    const downTarget = spot * (1 + downPct / 100);

    const callRow =
      rows.find((r) => r.strike_price >= upTarget) ?? rows[rows.length - 1];
    const putRow =
      [...rows].reverse().find((r) => r.strike_price <= downTarget) ?? rows[0];

    const stdMove = spot * baseSigma * Math.sqrt(T);
    const ivRangeLow = Math.max(0, spot - stdMove);
    const ivRangeHigh = spot + stdMove;

    let maxCallOi = -Infinity;
    let maxPutOi = -Infinity;
    let maxCallOiStrike: number | null = null;
    let maxPutOiStrike: number | null = null;
    for (const r of rows) {
      const cOi = parseNum(r.call?.open_interest);
      if (Number.isFinite(cOi) && cOi > maxCallOi) {
        maxCallOi = cOi;
        maxCallOiStrike = r.strike_price;
      }
      const pOi = parseNum(r.put?.open_interest);
      if (Number.isFinite(pOi) && pOi > maxPutOi) {
        maxPutOi = pOi;
        maxPutOiStrike = r.strike_price;
      }
    }

    return {
      downPct,
      upPct,
      upTarget,
      downTarget,
      callStrike: callRow.strike_price,
      putStrike: putRow.strike_price,
      callPremium: optionMidFromApiLeg(callRow.call),
      putPremium: optionMidFromApiLeg(putRow.put),
      atmIvPct: baseSigma * 100,
      ivRangeLow,
      ivRangeHigh,
      maxCallOiStrike,
      maxPutOiStrike,
    };
  }, [
    selectedReadymade,
    chainSuccess,
    spot,
    longStrangleDownPct,
    longStrangleUpPct,
    T,
    baseSigma,
  ]);

  const shortStrangleSuggestion = useMemo(() => {
    if (selectedReadymade !== "short_strangle") return null;
    if (
      !chainSuccess?.chain_rows?.length ||
      spot == null ||
      spot <= 0 ||
      T <= 0
    ) {
      return null;
    }

    const rows = [...chainSuccess.chain_rows].sort(
      (a, b) => a.strike_price - b.strike_price,
    );
    if (!rows.length) return null;

    const downPct = Math.min(
      -1,
      Math.max(STRANGLE_MOVE_MIN, shortStrangleDownPct),
    );
    const upPct = Math.min(
      STRANGLE_MOVE_MAX,
      Math.max(1, shortStrangleUpPct),
    );
    const upTarget = spot * (1 + upPct / 100);
    const downTarget = spot * (1 + downPct / 100);

    const callRow =
      rows.find((r) => r.strike_price >= upTarget) ?? rows[rows.length - 1];
    const putRow =
      [...rows].reverse().find((r) => r.strike_price <= downTarget) ?? rows[0];

    const stdMove = spot * baseSigma * Math.sqrt(T);
    const ivRangeLow = Math.max(0, spot - stdMove);
    const ivRangeHigh = spot + stdMove;

    let maxCallOi = -Infinity;
    let maxPutOi = -Infinity;
    let maxCallOiStrike: number | null = null;
    let maxPutOiStrike: number | null = null;
    for (const r of rows) {
      const cOi = parseNum(r.call?.open_interest);
      if (Number.isFinite(cOi) && cOi > maxCallOi) {
        maxCallOi = cOi;
        maxCallOiStrike = r.strike_price;
      }
      const pOi = parseNum(r.put?.open_interest);
      if (Number.isFinite(pOi) && pOi > maxPutOi) {
        maxPutOi = pOi;
        maxPutOiStrike = r.strike_price;
      }
    }

    return {
      downPct,
      upPct,
      upTarget,
      downTarget,
      callStrike: callRow.strike_price,
      putStrike: putRow.strike_price,
      callPremium: optionMidFromApiLeg(callRow.call),
      putPremium: optionMidFromApiLeg(putRow.put),
      atmIvPct: baseSigma * 100,
      ivRangeLow,
      ivRangeHigh,
      maxCallOiStrike,
      maxPutOiStrike,
    };
  }, [
    selectedReadymade,
    chainSuccess,
    spot,
    shortStrangleDownPct,
    shortStrangleUpPct,
    T,
    baseSigma,
  ]);

  useEffect(() => {
    if (
      (selectedReadymade !== "long_straddle" &&
        selectedReadymade !== "short_straddle") ||
      !chainSuccess
    )
      return;
    const ctx = buildTemplateContext(chainSuccess.chain_rows, spot);
    if (!ctx) return;
    setLegs(
      applyTemplate(
        selectedReadymade === "long_straddle" ? "long_straddle" : "short_straddle",
        ctx,
      ),
    );
  }, [selectedReadymade, chainSuccess, spot]);

  useEffect(() => {
    if (selectedReadymade !== "long_strangle") return;
    setLegs([]);
    setLongStrangleFetched(false);
    setLongStrangleApplied(null);
  }, [selectedReadymade]);

  useEffect(() => {
    if (selectedReadymade !== "short_strangle") return;
    setLegs([]);
    setShortStrangleFetched(false);
    setShortStrangleApplied(null);
  }, [selectedReadymade]);

  const fetchLongStrangleTrades = useCallback(() => {
    if (!longStrangleSuggestion) return;
    setLongStrangleApplied(longStrangleSuggestion);
    setLegs([
      {
        id: "auto-long-strangle-call",
        side: "Buy",
        right: "Call",
        strike: longStrangleSuggestion.callStrike,
        lots: 1,
        premiumPerUnit: longStrangleSuggestion.callPremium,
      },
      {
        id: "auto-long-strangle-put",
        side: "Buy",
        right: "Put",
        strike: longStrangleSuggestion.putStrike,
        lots: 1,
        premiumPerUnit: longStrangleSuggestion.putPremium,
      },
    ]);
    setLongStrangleFetched(true);
  }, [longStrangleSuggestion]);

  const handleLongStrangleDownPct = useCallback((v: number) => {
    setLongStrangleDownPct(v);
    setLongStrangleFetched(false);
    setLongStrangleApplied(null);
    setLegs([]);
  }, []);

  const handleLongStrangleUpPct = useCallback((v: number) => {
    setLongStrangleUpPct(v);
    setLongStrangleFetched(false);
    setLongStrangleApplied(null);
    setLegs([]);
  }, []);

  const fetchShortStrangleTrades = useCallback(() => {
    if (!shortStrangleSuggestion) return;
    setShortStrangleApplied(shortStrangleSuggestion);
    setLegs([
      {
        id: "auto-short-strangle-call",
        side: "Sell",
        right: "Call",
        strike: shortStrangleSuggestion.callStrike,
        lots: 1,
        premiumPerUnit: shortStrangleSuggestion.callPremium,
      },
      {
        id: "auto-short-strangle-put",
        side: "Sell",
        right: "Put",
        strike: shortStrangleSuggestion.putStrike,
        lots: 1,
        premiumPerUnit: shortStrangleSuggestion.putPremium,
      },
    ]);
    setShortStrangleFetched(true);
  }, [shortStrangleSuggestion]);

  const handleShortStrangleDownPct = useCallback((v: number) => {
    setShortStrangleDownPct(v);
    setShortStrangleFetched(false);
    setShortStrangleApplied(null);
    setLegs([]);
  }, []);

  const handleShortStrangleUpPct = useCallback((v: number) => {
    setShortStrangleUpPct(v);
    setShortStrangleFetched(false);
    setShortStrangleApplied(null);
    setLegs([]);
  }, []);

  const ironCondorSuggestion = useMemo(() => {
    if (selectedReadymade !== "iron_condor") return null;
    if (
      !chainSuccess?.chain_rows?.length ||
      spot == null ||
      spot <= 0 ||
      T <= 0
    ) {
      return null;
    }

    const rows = [...chainSuccess.chain_rows].sort(
      (a, b) => a.strike_price - b.strike_price,
    );
    if (!rows.length) return null;

    const strikes = rows.map((r) => r.strike_price);

    const downPct = Math.min(
      -1,
      Math.max(STRANGLE_MOVE_MIN, ironCondorDownPct),
    );
    const upPct = Math.min(
      STRANGLE_MOVE_MAX,
      Math.max(1, ironCondorUpPct),
    );
    const upTarget = spot * (1 + upPct / 100);
    const downTarget = spot * (1 + downPct / 100);

    const lpRow =
      [...rows].reverse().find((r) => r.strike_price <= downTarget) ?? rows[0];
    const longPutStrike = lpRow.strike_price;
    const lpIdx = strikes.indexOf(longPutStrike);
    if (lpIdx < 0 || lpIdx >= strikes.length - 1) return null;
    const shortPutStrike = strikes[lpIdx + 1]!;

    const lcRow =
      rows.find((r) => r.strike_price >= upTarget) ?? rows[rows.length - 1]!;
    const longCallStrike = lcRow.strike_price;
    const lcIdx = strikes.indexOf(longCallStrike);
    if (lcIdx <= 0) return null;
    const shortCallStrike = strikes[lcIdx - 1]!;

    if (
      !(
        longPutStrike < shortPutStrike &&
        shortPutStrike < shortCallStrike &&
        shortCallStrike < longCallStrike
      )
    ) {
      return null;
    }

    const rowLongPut = rows.find((r) => r.strike_price === longPutStrike);
    const rowShortPut = rows.find((r) => r.strike_price === shortPutStrike);
    const rowShortCall = rows.find((r) => r.strike_price === shortCallStrike);
    const rowLongCall = rows.find((r) => r.strike_price === longCallStrike);
    if (
      !rowLongPut?.put ||
      !rowShortPut?.put ||
      !rowShortCall?.call ||
      !rowLongCall?.call
    ) {
      return null;
    }

    const stdMove = spot * baseSigma * Math.sqrt(T);
    const ivRangeLow = Math.max(0, spot - stdMove);
    const ivRangeHigh = spot + stdMove;

    let maxCallOi = -Infinity;
    let maxPutOi = -Infinity;
    let maxCallOiStrike: number | null = null;
    let maxPutOiStrike: number | null = null;
    for (const r of rows) {
      const cOi = parseNum(r.call?.open_interest);
      if (Number.isFinite(cOi) && cOi > maxCallOi) {
        maxCallOi = cOi;
        maxCallOiStrike = r.strike_price;
      }
      const pOi = parseNum(r.put?.open_interest);
      if (Number.isFinite(pOi) && pOi > maxPutOi) {
        maxPutOi = pOi;
        maxPutOiStrike = r.strike_price;
      }
    }

    return {
      downPct,
      upPct,
      longPutStrike,
      shortPutStrike,
      shortCallStrike,
      longCallStrike,
      longPutPremium: optionMidFromApiLeg(rowLongPut.put),
      shortPutPremium: optionMidFromApiLeg(rowShortPut.put),
      shortCallPremium: optionMidFromApiLeg(rowShortCall.call),
      longCallPremium: optionMidFromApiLeg(rowLongCall.call),
      atmIvPct: baseSigma * 100,
      ivRangeLow,
      ivRangeHigh,
      maxCallOiStrike,
      maxPutOiStrike,
    };
  }, [
    selectedReadymade,
    chainSuccess,
    spot,
    ironCondorDownPct,
    ironCondorUpPct,
    T,
    baseSigma,
  ]);

  useEffect(() => {
    if (selectedReadymade !== "iron_condor") return;
    setLegs([]);
    setIronCondorFetched(false);
    setIronCondorApplied(null);
  }, [selectedReadymade]);

  const fetchIronCondorTrades = useCallback(() => {
    if (!ironCondorSuggestion) return;
    setIronCondorApplied(ironCondorSuggestion);
    const s = ironCondorSuggestion;
    setLegs([
      {
        id: "auto-iron-condor-long-put",
        side: "Buy",
        right: "Put",
        strike: s.longPutStrike,
        lots: 1,
        premiumPerUnit: s.longPutPremium,
      },
      {
        id: "auto-iron-condor-short-put",
        side: "Sell",
        right: "Put",
        strike: s.shortPutStrike,
        lots: 1,
        premiumPerUnit: s.shortPutPremium,
      },
      {
        id: "auto-iron-condor-short-call",
        side: "Sell",
        right: "Call",
        strike: s.shortCallStrike,
        lots: 1,
        premiumPerUnit: s.shortCallPremium,
      },
      {
        id: "auto-iron-condor-long-call",
        side: "Buy",
        right: "Call",
        strike: s.longCallStrike,
        lots: 1,
        premiumPerUnit: s.longCallPremium,
      },
    ]);
    setIronCondorFetched(true);
  }, [ironCondorSuggestion]);

  const handleIronCondorDownPct = useCallback((v: number) => {
    setIronCondorDownPct(v);
    setIronCondorFetched(false);
    setIronCondorApplied(null);
    setLegs([]);
  }, []);

  const handleIronCondorUpPct = useCallback((v: number) => {
    setIronCondorUpPct(v);
    setIronCondorFetched(false);
    setIronCondorApplied(null);
    setLegs([]);
  }, []);

  const ironButterflySuggestion = useMemo(() => {
    if (selectedReadymade !== "iron_butterfly") return null;
    if (
      !chainSuccess?.chain_rows?.length ||
      spot == null ||
      spot <= 0 ||
      T <= 0
    ) {
      return null;
    }
    const ctx = buildTemplateContext(chainSuccess.chain_rows, spot);
    if (!ctx) return null;
    const { strikes, atmIdx, rowsByStrike } = ctx;
    const midStrike = strikes[atmIdx];
    if (midStrike == null) return null;

    const wingPct = Math.min(
      OTM_SLIDER_MAX,
      Math.max(OTM_SLIDER_MIN, Math.round(ironButterflyWingPct)),
    );

    let lowStrike: number;
    let highStrike: number;

    if (wingPct <= 0) {
      if (atmIdx < 1 || atmIdx >= strikes.length - 1) return null;
      lowStrike = strikes[atmIdx - 1]!;
      highStrike = strikes[atmIdx + 1]!;
    } else {
      const lowTarget = spot * (1 - wingPct / 100);
      const highTarget = spot * (1 + wingPct / 100);
      const below = strikes.filter((s) => s < midStrike);
      const above = strikes.filter((s) => s > midStrike);
      if (!below.length || !above.length) return null;
      lowStrike = below.reduce((best, s) =>
        Math.abs(s - lowTarget) < Math.abs(best - lowTarget) ? s : best,
      );
      highStrike = above.reduce((best, s) =>
        Math.abs(s - highTarget) < Math.abs(best - highTarget) ? s : best,
      );
    }

    if (!(lowStrike < midStrike && midStrike < highStrike)) return null;

    const rowLow = rowsByStrike.get(lowStrike);
    const rowMid = rowsByStrike.get(midStrike);
    const rowHigh = rowsByStrike.get(highStrike);
    if (!rowLow?.put || !rowMid?.put || !rowMid?.call || !rowHigh?.call) {
      return null;
    }

    const stdMove = spot * baseSigma * Math.sqrt(T);
    const ivRangeLow = Math.max(0, spot - stdMove);
    const ivRangeHigh = spot + stdMove;

    let maxCallOi = -Infinity;
    let maxPutOi = -Infinity;
    let maxCallOiStrike: number | null = null;
    let maxPutOiStrike: number | null = null;
    for (const r of chainSuccess.chain_rows) {
      const cOi = parseNum(r.call?.open_interest);
      if (Number.isFinite(cOi) && cOi > maxCallOi) {
        maxCallOi = cOi;
        maxCallOiStrike = r.strike_price;
      }
      const pOi = parseNum(r.put?.open_interest);
      if (Number.isFinite(pOi) && pOi > maxPutOi) {
        maxPutOi = pOi;
        maxPutOiStrike = r.strike_price;
      }
    }

    return {
      wingPct,
      lowStrike,
      midStrike,
      highStrike,
      longPutPremium: optionMidFromApiLeg(rowLow.put),
      midPutPremium: optionMidFromApiLeg(rowMid.put),
      midCallPremium: optionMidFromApiLeg(rowMid.call),
      longCallPremium: optionMidFromApiLeg(rowHigh.call),
      atmIvPct: baseSigma * 100,
      ivRangeLow,
      ivRangeHigh,
      maxCallOiStrike,
      maxPutOiStrike,
    };
  }, [
    selectedReadymade,
    chainSuccess,
    spot,
    T,
    baseSigma,
    ironButterflyWingPct,
  ]);

  useEffect(() => {
    if (selectedReadymade !== "iron_butterfly") return;
    setLegs([]);
    setIronButterflyFetched(false);
    setIronButterflyApplied(null);
  }, [selectedReadymade]);

  const fetchIronButterflyTrades = useCallback(() => {
    if (!ironButterflySuggestion) return;
    setIronButterflyApplied(ironButterflySuggestion);
    const s = ironButterflySuggestion;
    setLegs([
      {
        id: "auto-iron-bfly-long-put",
        side: "Buy",
        right: "Put",
        strike: s.lowStrike,
        lots: 1,
        premiumPerUnit: s.longPutPremium,
      },
      {
        id: "auto-iron-bfly-short-put",
        side: "Sell",
        right: "Put",
        strike: s.midStrike,
        lots: 1,
        premiumPerUnit: s.midPutPremium,
      },
      {
        id: "auto-iron-bfly-short-call",
        side: "Sell",
        right: "Call",
        strike: s.midStrike,
        lots: 1,
        premiumPerUnit: s.midCallPremium,
      },
      {
        id: "auto-iron-bfly-long-call",
        side: "Buy",
        right: "Call",
        strike: s.highStrike,
        lots: 1,
        premiumPerUnit: s.longCallPremium,
      },
    ]);
    setIronButterflyFetched(true);
  }, [ironButterflySuggestion]);

  const handleIronButterflyWingPct = useCallback((v: number) => {
    setIronButterflyWingPct(v);
    setIronButterflyFetched(false);
    setIronButterflyApplied(null);
    setLegs([]);
  }, []);

  const longCallButterflySuggestion = useMemo(() => {
    if (selectedReadymade !== "long_call_butterfly") return null;
    if (
      !chainSuccess?.chain_rows?.length ||
      spot == null ||
      spot <= 0 ||
      T <= 0
    ) {
      return null;
    }
    const ctx = buildTemplateContext(chainSuccess.chain_rows, spot);
    if (!ctx) return null;
    const { strikes, atmIdx, rowsByStrike } = ctx;
    const midStrike = strikes[atmIdx];
    if (midStrike == null) return null;

    const wingPct = Math.min(
      OTM_SLIDER_MAX,
      Math.max(OTM_SLIDER_MIN, Math.round(butterflyWingPct)),
    );

    let lowStrike: number;
    let highStrike: number;

    if (wingPct <= 0) {
      if (atmIdx < 1 || atmIdx >= strikes.length - 1) return null;
      lowStrike = strikes[atmIdx - 1]!;
      highStrike = strikes[atmIdx + 1]!;
    } else {
      const lowTarget = spot * (1 - wingPct / 100);
      const highTarget = spot * (1 + wingPct / 100);
      const below = strikes.filter((s) => s < midStrike);
      const above = strikes.filter((s) => s > midStrike);
      if (!below.length || !above.length) return null;
      lowStrike = below.reduce((best, s) =>
        Math.abs(s - lowTarget) < Math.abs(best - lowTarget) ? s : best,
      );
      highStrike = above.reduce((best, s) =>
        Math.abs(s - highTarget) < Math.abs(best - highTarget) ? s : best,
      );
    }

    if (!(lowStrike < midStrike && midStrike < highStrike)) return null;

    const rowLow = rowsByStrike.get(lowStrike);
    const rowMid = rowsByStrike.get(midStrike);
    const rowHigh = rowsByStrike.get(highStrike);
    if (!rowLow?.call || !rowMid?.call || !rowHigh?.call) return null;

    const stdMove = spot * baseSigma * Math.sqrt(T);
    const ivRangeLow = Math.max(0, spot - stdMove);
    const ivRangeHigh = spot + stdMove;

    let maxCallOi = -Infinity;
    let maxPutOi = -Infinity;
    let maxCallOiStrike: number | null = null;
    let maxPutOiStrike: number | null = null;
    for (const r of chainSuccess.chain_rows) {
      const cOi = parseNum(r.call?.open_interest);
      if (Number.isFinite(cOi) && cOi > maxCallOi) {
        maxCallOi = cOi;
        maxCallOiStrike = r.strike_price;
      }
      const pOi = parseNum(r.put?.open_interest);
      if (Number.isFinite(pOi) && pOi > maxPutOi) {
        maxPutOi = pOi;
        maxPutOiStrike = r.strike_price;
      }
    }

    return {
      wingPct,
      lowStrike,
      midStrike,
      highStrike,
      lowPremium: optionMidFromApiLeg(rowLow.call),
      midPremium: optionMidFromApiLeg(rowMid.call),
      highPremium: optionMidFromApiLeg(rowHigh.call),
      atmIvPct: baseSigma * 100,
      ivRangeLow,
      ivRangeHigh,
      maxCallOiStrike,
      maxPutOiStrike,
    };
  }, [
    selectedReadymade,
    chainSuccess,
    spot,
    T,
    baseSigma,
    butterflyWingPct,
  ]);

  useEffect(() => {
    if (selectedReadymade !== "long_call_butterfly") return;
    setLegs([]);
    setButterflyFetched(false);
    setButterflyApplied(null);
  }, [selectedReadymade]);

  const fetchLongCallButterflyTrades = useCallback(() => {
    if (!longCallButterflySuggestion) return;
    setButterflyApplied(longCallButterflySuggestion);
    const s = longCallButterflySuggestion;
    setLegs([
      {
        id: "auto-butterfly-low",
        side: "Buy",
        right: "Call",
        strike: s.lowStrike,
        lots: 1,
        premiumPerUnit: s.lowPremium,
      },
      {
        id: "auto-butterfly-mid",
        side: "Sell",
        right: "Call",
        strike: s.midStrike,
        lots: 2,
        premiumPerUnit: s.midPremium,
      },
      {
        id: "auto-butterfly-high",
        side: "Buy",
        right: "Call",
        strike: s.highStrike,
        lots: 1,
        premiumPerUnit: s.highPremium,
      },
    ]);
    setButterflyFetched(true);
  }, [longCallButterflySuggestion]);

  const handleButterflyWingPct = useCallback((v: number) => {
    setButterflyWingPct(v);
    setButterflyFetched(false);
    setButterflyApplied(null);
    setLegs([]);
  }, []);

  const applyTemplateId = useCallback(
    (id: TemplateId) => {
      if (!chainSuccess) return;
      const ctx = buildTemplateContext(chainSuccess.chain_rows, spot);
      if (!ctx) return;
      setLegs(applyTemplate(id, ctx));
    },
    [chainSuccess, spot],
  );

  const confirmNaked = () => {
    if (nakedPrompt) applyTemplateId(nakedPrompt);
    setNakedPrompt(null);
  };

  const addUncoveredLeg = useCallback(
    (opt: Record<string, unknown>) => {
      const k = parseNum(opt.strike_price);
      if (!Number.isFinite(k)) return;
      const qty = parseNum(opt.quantity);
      const rightStr = String(opt.right ?? "Call");
      const right: OptionRight = /^p/i.test(rightStr) ? "Put" : "Call";
      const ls = lotSize > 0 ? lotSize : 1;
      const lots =
        Number.isFinite(qty) && ls > 0
          ? Math.max(1, Math.round(qty / ls))
          : 1;
      const ltpV = parseNum(opt.ltp);
      const bidV = parseNum(opt.best_bid_price);
      const premiumPerUnit =
        Number.isFinite(ltpV) && ltpV > 0
          ? ltpV
          : Number.isFinite(bidV) && bidV > 0
            ? bidV
            : undefined;
      setLegs((prev) => [
        ...prev,
        {
          id: `leg-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`,
          side: "Sell",
          right,
          strike: k,
          lots,
          premiumPerUnit,
        },
      ]);
    },
    [lotSize],
  );

  const addCoveredPair = useCallback(
    (opt: Record<string, unknown>) => {
      const k = parseNum(opt.strike_price);
      if (!Number.isFinite(k)) return;
      const rightStr = String(opt.right ?? "Call");
      const right: OptionRight = /^p/i.test(rightStr) ? "Put" : "Call";
      const hm = opt.hedge_match as HedgeMatchPayload | undefined;
      const hedge = hm?.best;
      if (!hedge) return;
      const hk = parseNum(hedge.strike_price);
      if (!Number.isFinite(hk)) return;

      const qtyShort = parseNum(opt.quantity);
      const ls = lotSize > 0 ? lotSize : 1;
      const shortLots =
        Number.isFinite(qtyShort) && ls > 0
          ? Math.max(1, Math.round(qtyShort / ls))
          : 1;

      const hedgeQty = parseNum(hedge.hedge_quantity);
      const hedgeLots =
        Number.isFinite(hedgeQty) && ls > 0
          ? Math.max(1, Math.round(hedgeQty / ls))
          : 1;

      const ltpV = parseNum(opt.ltp);
      const bidV = parseNum(opt.best_bid_price);
      const shortPrem =
        Number.isFinite(ltpV) && ltpV > 0
          ? ltpV
          : Number.isFinite(bidV) && bidV > 0
            ? bidV
            : undefined;

      const ho = parseNum(hedge.best_offer_price);
      const hltp = parseNum(hedge.ltp);
      const hedgePrem =
        Number.isFinite(hltp) && hltp > 0
          ? hltp
          : Number.isFinite(ho) && ho > 0
            ? ho
            : undefined;

      const idBase = `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
      setLegs((prev) => [
        ...prev,
        {
          id: `leg-${idBase}-s`,
          side: "Sell",
          right,
          strike: k,
          lots: shortLots,
          premiumPerUnit: shortPrem,
        },
        {
          id: `leg-${idBase}-h`,
          side: "Buy",
          right,
          strike: hk,
          lots: hedgeLots,
          premiumPerUnit: hedgePrem,
        },
      ]);
    },
    [lotSize],
  );

  const addedUncoveredContractKeys = useMemo(() => {
    const s = new Set<string>();
    for (const l of legs) {
      s.add(uncoveredContractKey(stockCode, expiryDate, l.strike, l.right));
    }
    return s;
  }, [legs, stockCode, expiryDate]);

  const buildYourOwnAddedSlots = useMemo(() => {
    const s = new Set<string>();
    for (const l of legs) {
      s.add(
        buildYourOwnSlotKey(
          stockCode,
          expiryDate,
          l.strike,
          l.right,
          l.side,
        ),
      );
    }
    return s;
  }, [legs, stockCode, expiryDate]);

  const handleStrategyChainBuySell = useCallback(
    (side: OrderSide, row: ChainRow, right: OptionRight) => {
      const apiLeg = right === "Call" ? row.call : row.put;
      if (!apiLeg) return;
      const premStr = ltpAsOrderPrice(apiLeg.ltp);
      const premNum = parseFloat(premStr);
      setLegs((prev) => [
        ...prev,
        {
          id: `leg-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`,
          right,
          side,
          strike: row.strike_price,
          lots: 1,
          premiumPerUnit: Number.isFinite(premNum) ? premNum : undefined,
        },
      ]);
    },
    [],
  );

  const profitClass =
    "text-emerald-700 dark:text-emerald-400";
  const lossClass = "text-red-700 dark:text-red-400";
  const totalsNetPremium = useMemo(
    () =>
      legs.reduce((sum, l) => {
        if (l.lots <= 0 || l.premiumPerUnit == null) return sum;
        const legPremium = l.premiumPerUnit * Math.round(l.lots * lotSize);
        return l.side === "Sell" ? sum + legPremium : sum - legPremium;
      }, 0),
    [legs, lotSize],
  );
  const totalsMargin = useMemo(() => {
    let sum = 0;
    let hasPositiveLots = false;
    let hasMissingFreshMargin = false;
    let hasMarginFetchInFlight = false;
    for (const l of legs) {
      if (l.lots <= 0) continue;
      hasPositiveLots = true;
      if (legMarginFetchingId === l.id) {
        hasMarginFetchInFlight = true;
      }
      const legEntry = legMarginCache[l.id];
      const legMarginFresh = legMarginEntryMatches(l, legEntry);
      if (
        legMarginFresh &&
        legEntry != null &&
        legEntry.span != null &&
        Number.isFinite(legEntry.span)
      ) {
        sum += legEntry.span;
      } else {
        hasMissingFreshMargin = true;
      }
    }
    return {
      sum,
      hasPositiveLots,
      hasMissingFreshMargin,
      hasMarginFetchInFlight,
    };
  }, [legs, legMarginCache, legMarginFetchingId]);

  return (
    <AppShell contentWidth="wide">
      <RevokedTradingPageGuard>
      {secondsRemaining !== null ? (
        <RateLimitPauseOverlay secondsRemaining={secondsRemaining} />
      ) : null}
      <div className="space-y-5">
        <header className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <h1 className="text-xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
              Strategy Builder
            </h1>
            <p className="mt-2 max-w-2xl text-sm leading-relaxed text-zinc-600 dark:text-zinc-400">
              Readymade templates and orchestrated multi-leg placement
            </p>
          </div>
        </header>

        <section className={`${sb.section} relative z-20 space-y-5`}>
          <h2 className={sb.sectionTitle}>1. Underlying &amp; Expiry</h2>
          <div
            className="flex min-h-[2.75rem] flex-col overflow-visible rounded-md border border-zinc-200/90 bg-zinc-100 shadow-sm dark:border-transparent dark:bg-[#1b1c1f] dark:shadow-none sm:flex-row sm:items-center"
            role="toolbar"
            aria-label="Underlying and expiry"
          >
            <div
              className="flex shrink-0 items-center border-b border-zinc-200 dark:border-zinc-700/70 px-2 py-2 sm:border-b-0 sm:border-r sm:border-zinc-200 sm:dark:border-zinc-700/70 sm:py-0 sm:ps-2.5 sm:pe-2"
              role="group"
              aria-label="Exchange segment"
            >
              <div className="inline-flex rounded-lg bg-zinc-200/70 p-0.5 ring-1 ring-zinc-300/70 dark:bg-black/30 dark:ring-zinc-700/70">
                <button
                  type="button"
                  onClick={() => onSegmentChange("NFO")}
                  className={`rounded-md px-3 py-1.5 text-xs font-semibold transition focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-500/45 sm:text-sm ${
                    segmentExchange === "NFO"
                      ? "bg-white text-zinc-900 shadow-sm dark:bg-zinc-700 dark:text-white"
                      : "text-zinc-600 hover:bg-zinc-300/50 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-800/80 dark:hover:text-zinc-200"
                  }`}
                >
                  NSE
                </button>
                <button
                  type="button"
                  onClick={() => onSegmentChange("BFO")}
                  className={`rounded-md px-3 py-1.5 text-xs font-semibold transition focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-500/45 sm:text-sm ${
                    segmentExchange === "BFO"
                      ? "bg-white text-zinc-900 shadow-sm dark:bg-zinc-700 dark:text-white"
                      : "text-zinc-600 hover:bg-zinc-300/50 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-800/80 dark:hover:text-zinc-200"
                  }`}
                >
                  BSE
                </button>
              </div>
            </div>

            <div className="relative z-30 flex min-w-0 max-w-[min(100%,26rem)] flex-1 items-center overflow-visible border-b border-zinc-200 dark:border-zinc-700/70 px-3 py-2 sm:border-b-0 sm:border-r sm:border-zinc-200 sm:dark:border-zinc-700/70 sm:py-2.5">
              <OptionChainUnderlyingSearch
                variant="ticker"
                chainBar
                underlyings={uq.data?.underlyings ?? []}
                value={stockCode}
                disabled={uq.isLoading}
                spot={spot}
                onChange={(code) => {
                  setStockCode(code);
                  setExpiryDate("");
                  setLegs([]);
                  setSelectedReadymade(null);
                  setUncoveredScanResult(null);
                }}
              />
            </div>

            <div className="relative z-20 flex shrink-0 items-center overflow-visible px-3 py-2 sm:py-2.5 sm:pe-3.5">
              <ExpirySelectPill
                layout="toolbar"
                tone="darkToolbar"
                dates={expiryOptions}
                value={expiryDate}
                disabled={!stockCode}
                onChange={(d) => {
                  setExpiryDate(d);
                  setLegs([]);
                  setSelectedReadymade(null);
                  setUncoveredScanResult(null);
                }}
              />
            </div>
          </div>
        </section>

        <section className={`${sb.section} relative z-10 space-y-4`}>
          <h2 className={sb.sectionTitle}>2. Readymade Strategies</h2>
          <div className="flex min-w-0 flex-wrap justify-center gap-3 sm:justify-start">
            <ReadymadeSetupTooltip strategyId="build-your-own">
              <button
                type="button"
                disabled={
                  uq.isLoading ||
                  !stockCode.trim() ||
                  !expiryDate.trim() ||
                  !chainSuccess
                }
                onClick={() => {
                  setScanError(null);
                  setUncoveredScanResult(null);
                  setLegs([]);
                  setSelectedReadymade("build-your-own");
                }}
                className={`${sb.cardTemplate} relative p-0 w-[6.875rem] aspect-square flex flex-col items-center justify-start gap-0 text-center overflow-hidden ${
                  selectedReadymade === "build-your-own"
                    ? "ring-2 ring-sky-500 ring-offset-2 ring-offset-white dark:ring-offset-zinc-900"
                    : ""
                }`}
                aria-pressed={selectedReadymade === "build-your-own"}
              >
                <div className={READYMADE_GRAPH_AREA}>
                  <div className={READYMADE_GRAPH_BOX}>
                    <OptionStrategyIcon templateId="build-your-own" />
                  </div>
                </div>
                <div className="font-medium text-[11px] leading-none whitespace-nowrap w-full h-[1.1rem] flex items-center justify-center text-center">
                  Build Your Own
                </div>
              </button>
            </ReadymadeSetupTooltip>
            <ReadymadeSetupTooltip strategyId="naked-shorts">
              <button
                type="button"
                disabled={
                  uq.isLoading || !stockCode.trim() || !expiryDate.trim()
                }
                onClick={() => {
                  setScanError(null);
                  setUncoveredScanResult(null);
                  setSelectedReadymade("naked-shorts");
                }}
                className={`${sb.cardTemplate} relative p-0 w-[13.75rem] h-[6.875rem] flex flex-col items-center justify-start gap-0 text-center overflow-hidden ${
                  selectedReadymade === "naked-shorts"
                    ? "ring-2 ring-amber-500 ring-offset-2 ring-offset-white dark:ring-offset-zinc-900"
                    : ""
                }`}
                aria-pressed={selectedReadymade === "naked-shorts"}
              >
                <div className={READYMADE_GRAPH_AREA}>
                  <div className={READYMADE_GRAPH_BOX}>
                    <OptionStrategyIcon templateId="naked-shorts" />
                  </div>
                </div>
                <div className="font-medium text-[11px] leading-none whitespace-nowrap w-full h-[1.1rem] flex items-center justify-center text-center">
                  Naked Shorts
                </div>
              </button>
            </ReadymadeSetupTooltip>
            <ReadymadeSetupTooltip strategyId="covered-shorts">
              <button
                type="button"
                disabled={
                  uq.isLoading || !stockCode.trim() || !expiryDate.trim()
                }
                onClick={() => {
                  setScanError(null);
                  setUncoveredScanResult(null);
                  setSelectedReadymade("covered-shorts");
                }}
                className={`${sb.cardTemplate} relative p-0 w-[13.75rem] h-[6.875rem] flex flex-col items-center justify-start gap-0 text-center overflow-hidden ${
                  selectedReadymade === "covered-shorts"
                    ? "ring-2 ring-amber-500 ring-offset-2 ring-offset-white dark:ring-offset-zinc-900"
                    : ""
                }`}
                aria-pressed={selectedReadymade === "covered-shorts"}
              >
                <div className={READYMADE_GRAPH_AREA}>
                  <div className={READYMADE_GRAPH_BOX}>
                    <OptionStrategyIcon templateId="covered-shorts" />
                  </div>
                </div>
                <div className="font-medium text-[11px] leading-none whitespace-nowrap w-full h-[1.1rem] flex items-center justify-center text-center">
                  Covered Shorts (Ratio Spreads)
                </div>
              </button>
            </ReadymadeSetupTooltip>
            {STRATEGY_TEMPLATES.map((t) => (
              <div
                key={t.id}
                className="relative"
                onMouseLeave={() => setComingSoonTooltipId(null)}
              >
                {t.id === "bull_call_spread" ||
                t.id === "bull_put_spread" ||
                t.id === "bear_call_spread" ||
                t.id === "bear_put_spread" ||
                t.id === "long_straddle" ||
                t.id === "long_strangle" ||
                t.id === "short_straddle" ||
                t.id === "short_strangle" ||
                t.id === "iron_condor" ||
                t.id === "iron_butterfly" ||
                t.id === "long_call_butterfly" ? (
                  <ReadymadeSetupTooltip strategyId={t.id}>
                    <button
                      type="button"
                      disabled={
                        uq.isLoading ||
                        !stockCode.trim() ||
                        !expiryDate.trim() ||
                        (t.id !== "long_strangle" &&
                          t.id !== "short_strangle" &&
                          t.id !== "long_call_butterfly" &&
                          t.id !== "iron_condor" &&
                          t.id !== "iron_butterfly" &&
                          !chainSuccess)
                      }
                      className={`${sb.cardTemplate} relative p-0 w-[6.875rem] aspect-square flex flex-col items-center justify-start gap-0 text-center overflow-hidden ${
                        selectedReadymade === t.id
                          ? "ring-2 ring-sky-500 ring-offset-2 ring-offset-white dark:ring-offset-zinc-900"
                          : ""
                      }`}
                      aria-pressed={selectedReadymade === t.id}
                      onClick={() => {
                        setScanError(null);
                        setUncoveredScanResult(null);
                        setLegs([]);
                        setSelectedReadymade(t.id);
                      }}
                    >
                      <ReadymadeOutlookPill outlook={t.outlook[0]} />
                      <div className={READYMADE_GRAPH_AREA}>
                        <div className={READYMADE_GRAPH_BOX}>
                          <OptionStrategyIcon templateId={t.id} />
                        </div>
                      </div>
                      <div className="font-medium text-[11px] leading-none whitespace-nowrap w-full h-[1.1rem] flex items-center justify-center text-center">
                        {t.label}
                      </div>
                    </button>
                  </ReadymadeSetupTooltip>
                ) : (
                  <ReadymadeSetupTooltip strategyId={t.id}>
                    <button
                      type="button"
                      className={`${sb.cardTemplate} relative p-0 w-[6.875rem] aspect-square flex flex-col items-center justify-start gap-0 text-center overflow-hidden opacity-90 border-dotted hover:border-zinc-200/90 hover:shadow-sm`}
                      aria-pressed={false}
                      aria-label={`${t.label} - Coming soon`}
                      onClick={() => setComingSoonTooltipId(t.id)}
                    >
                      <ReadymadeOutlookPill outlook={t.outlook[0]} />
                      <div className={READYMADE_GRAPH_AREA}>
                        <div className={READYMADE_GRAPH_BOX}>
                          <OptionStrategyIcon templateId={t.id} />
                        </div>
                      </div>
                      <div className="font-medium text-[11px] leading-none whitespace-nowrap w-full h-[1.1rem] flex items-center justify-center text-center">
                        {t.label}
                      </div>
                    </button>
                  </ReadymadeSetupTooltip>
                )}
                {comingSoonTooltipId === t.id ? (
                  <div className="pointer-events-none absolute left-1/2 top-0 z-20 -translate-x-1/2 -translate-y-[0.35rem] whitespace-nowrap rounded-lg bg-zinc-950/90 px-2 py-1 text-[10px] font-medium text-white shadow-lg dark:bg-black/80">
                    Coming soon
                  </div>
                ) : null}
              </div>
            ))}
          </div>
          {cq.isError ? (
            <div className="app-alert-error text-xs">
              {cq.error instanceof Error ? cq.error.message : "Chain failed"}
            </div>
          ) : null}
        </section>

        <section
          ref={parametersSectionRef}
          id="strategy-builder-parameters"
          className={`${sb.section} ${
            selectedReadymade == null
              ? "border-dashed border-zinc-300/90 bg-zinc-50/40 py-6 dark:border-zinc-600/80 dark:bg-zinc-950/30"
              : "space-y-4"
          }`}
        >
          <h2 className={sb.sectionTitle}>3. Parameters</h2>
          {selectedReadymade == null ? (
            <p className="text-sm text-zinc-500 dark:text-zinc-400">
              Select a readymade strategy in section 2 to configure parameters.
            </p>
          ) : selectedReadymade === "naked-shorts" ||
            selectedReadymade === "covered-shorts" ? (
            <div className="flex flex-col gap-3">
              <p className="mt-1 flex flex-wrap items-baseline gap-x-1.5 gap-y-0.5 text-xs text-zinc-500 dark:text-zinc-400">
                <span className="font-medium tabular-nums text-zinc-700 dark:text-zinc-300">
                  {segmentExchange === "NFO" ? "NSE" : "BSE"}
                  <span className="mx-1 text-zinc-400 dark:text-zinc-500">
                    ›
                  </span>
                  {stockCode || "—"}
                  <span className="mx-1 text-zinc-400 dark:text-zinc-500">
                    ›
                  </span>
                  {expiryDate || "—"}
                </span>
              </p>

              <div className="grid w-full min-w-0 grid-cols-4 gap-3 sm:gap-4">
                <div className="min-w-0">
                  <UncoveredNumberStepper
                    compact
                    label="Margin to deploy"
                    value={scanLimits}
                    onChange={setScanLimits}
                    min={1}
                    max={MARGIN_LACS_MAX}
                    suffix="Lacs"
                  />
                  <div
                    className={`${sb.checkboxRow} mt-2 gap-2 text-xs font-medium leading-snug text-zinc-600 dark:text-zinc-400`}
                  >
                    <button
                      type="button"
                      role="switch"
                      aria-checked={scanProvisionElm}
                      aria-label="Toggle Provision for ELM"
                      onClick={() => setScanProvisionElm(!scanProvisionElm)}
                      className={`relative inline-flex h-5 w-9 items-center rounded-full transition ${
                        scanProvisionElm ? "bg-sky-600" : "bg-zinc-300 dark:bg-zinc-700"
                      }`}
                    >
                      <span
                        className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition ${
                          scanProvisionElm ? "translate-x-4" : "translate-x-0.5"
                        }`}
                      />
                    </button>
                    Provision for ELM
                  </div>
                </div>
                <div className="min-w-0">
                  <UncoveredNumberStepper
                    compact
                    label="Options to list"
                    value={scanTop}
                    onChange={setScanTop}
                    min={1}
                    max={STRATEGY_BUILDER_OPTIONS_TO_LIST_MAX}
                  />
                </div>
                <div className="min-w-0 space-y-2">
                  <UncoveredOtmRangeSlider
                    compact
                    label="Call OTM range %"
                    minValue={scanOtmCallMin}
                    maxValue={scanOtmCallMax}
                    onMinChange={setScanOtmCallMin}
                    onMaxChange={setScanOtmCallMax}
                    minAriaLabel="Call OTM minimum percent"
                    maxAriaLabel="Call OTM maximum percent"
                  />
                </div>
                <div className="min-w-0 space-y-2">
                  <UncoveredOtmRangeSlider
                    compact
                    label="Put OTM range %"
                    minValue={scanOtmPutMin}
                    maxValue={scanOtmPutMax}
                    onMinChange={setScanOtmPutMin}
                    onMaxChange={setScanOtmPutMax}
                    minAriaLabel="Put OTM minimum percent"
                    maxAriaLabel="Put OTM maximum percent"
                  />
                </div>
              </div>

              {scanError ? (
                <div className="app-alert-error text-xs">{scanError}</div>
              ) : null}
              {!scanError && scanFallbackWarnings.length > 0 ? (
                <div className="app-alert-error text-xs">{scanFallbackWarnings[0]}</div>
              ) : null}
              <div className="flex justify-start">
                <button
                  type="button"
                  disabled={
                    !stockCode.trim() ||
                    !expiryDate.trim() ||
                    scanLimits == null ||
                    scanTop == null ||
                    scanOtmCallMin > scanOtmCallMax ||
                    scanOtmPutMin > scanOtmPutMax ||
                    uncoveredScanMut.isPending
                  }
                  className={`${sb.btnPrimary} inline-flex shrink-0 items-center justify-center px-4 py-2 text-sm`}
                  aria-busy={uncoveredScanMut.isPending}
                  onClick={() => {
                    setScanError(null);
                    uncoveredScanMut.mutate();
                  }}
                >
                  <AsyncLabelSpan
                    busy={uncoveredScanMut.isPending}
                    idleLabel="Fetch Trades"
                    busyLabel="Fetching..."
                  />
                </button>
              </div>
            </div>
          ) : selectedReadymade === "build-your-own" ? (
            <p className="text-sm text-zinc-500 dark:text-zinc-400">
              Not applicable — use the option chain in Proposed Legs.
            </p>
          ) : selectedReadymade === "bull_call_spread" ? (
            <div className="space-y-3">
              <p className="mt-1 flex flex-wrap items-baseline gap-x-1.5 gap-y-0.5 text-xs text-zinc-500 dark:text-zinc-400">
                <span className="font-medium tabular-nums text-zinc-700 dark:text-zinc-300">
                  {segmentExchange === "NFO" ? "NSE" : "BSE"}
                  <span className="mx-1 text-zinc-400 dark:text-zinc-500">›</span>
                  {stockCode || "—"}
                  <span className="mx-1 text-zinc-400 dark:text-zinc-500">›</span>
                  {expiryDate || "—"}
                </span>
              </p>
              <div className="max-w-[18rem]">
                <UncoveredNumberStepper
                  compact
                  label="Minimum POP required"
                  value={bullCallMinPop}
                  onChange={setBullCallMinPop}
                  min={1}
                  max={99}
                  suffix="%"
                />
              </div>
              <div className="flex justify-start">
                <button
                  type="button"
                  disabled={
                    !stockCode.trim() ||
                    !expiryDate.trim() ||
                    !chainSuccess ||
                    !bullCallSuggestion
                  }
                  className={`${sb.btnPrimary} inline-flex shrink-0 items-center justify-center px-4 py-2 text-sm`}
                  onClick={fetchBullCallTrades}
                >
                  Fetch Trades
                </button>
              </div>
              {!bullCallSuggestion ? (
                <p className="text-xs text-zinc-500 dark:text-zinc-400">
                  Unable to compute Bull Call Spread legs from this chain.
                </p>
              ) : null}
            </div>
          ) : selectedReadymade === "bear_call_spread" ? (
            <div className="space-y-3">
              <p className="mt-1 flex flex-wrap items-baseline gap-x-1.5 gap-y-0.5 text-xs text-zinc-500 dark:text-zinc-400">
                <span className="font-medium tabular-nums text-zinc-700 dark:text-zinc-300">
                  {segmentExchange === "NFO" ? "NSE" : "BSE"}
                  <span className="mx-1 text-zinc-400 dark:text-zinc-500">›</span>
                  {stockCode || "—"}
                  <span className="mx-1 text-zinc-400 dark:text-zinc-500">›</span>
                  {expiryDate || "—"}
                </span>
              </p>
              <div className="max-w-[18rem]">
                <UncoveredNumberStepper
                  compact
                  label="Minimum POP required"
                  value={bearCallMinPop}
                  onChange={setBearCallMinPop}
                  min={1}
                  max={99}
                  suffix="%"
                />
              </div>
              <div className="flex justify-start">
                <button
                  type="button"
                  disabled={
                    !stockCode.trim() ||
                    !expiryDate.trim() ||
                    !chainSuccess ||
                    !bearCallSuggestion
                  }
                  className={`${sb.btnPrimary} inline-flex shrink-0 items-center justify-center px-4 py-2 text-sm`}
                  onClick={fetchBearCallTrades}
                >
                  Fetch Trades
                </button>
              </div>
              {!bearCallSuggestion ? (
                <p className="text-xs text-zinc-500 dark:text-zinc-400">
                  Unable to compute Bear Call Spread legs from this chain.
                </p>
              ) : null}
            </div>
          ) : selectedReadymade === "bear_put_spread" ? (
            <div className="space-y-3">
              <p className="mt-1 flex flex-wrap items-baseline gap-x-1.5 gap-y-0.5 text-xs text-zinc-500 dark:text-zinc-400">
                <span className="font-medium tabular-nums text-zinc-700 dark:text-zinc-300">
                  {segmentExchange === "NFO" ? "NSE" : "BSE"}
                  <span className="mx-1 text-zinc-400 dark:text-zinc-500">›</span>
                  {stockCode || "—"}
                  <span className="mx-1 text-zinc-400 dark:text-zinc-500">›</span>
                  {expiryDate || "—"}
                </span>
              </p>
              <div className="max-w-[18rem]">
                <UncoveredNumberStepper
                  compact
                  label="Minimum POP required"
                  value={bearPutMinPop}
                  onChange={setBearPutMinPop}
                  min={1}
                  max={99}
                  suffix="%"
                />
              </div>
              <div className="flex justify-start">
                <button
                  type="button"
                  disabled={
                    !stockCode.trim() ||
                    !expiryDate.trim() ||
                    !chainSuccess ||
                    !bearPutSuggestion
                  }
                  className={`${sb.btnPrimary} inline-flex shrink-0 items-center justify-center px-4 py-2 text-sm`}
                  onClick={fetchBearPutTrades}
                >
                  Fetch Trades
                </button>
              </div>
              {!bearPutSuggestion ? (
                <p className="text-xs text-zinc-500 dark:text-zinc-400">
                  Unable to compute Bear Put Spread legs from this chain.
                </p>
              ) : null}
            </div>
          ) : selectedReadymade === "bull_put_spread" ? (
            <div className="space-y-3">
              <p className="mt-1 flex flex-wrap items-baseline gap-x-1.5 gap-y-0.5 text-xs text-zinc-500 dark:text-zinc-400">
                <span className="font-medium tabular-nums text-zinc-700 dark:text-zinc-300">
                  {segmentExchange === "NFO" ? "NSE" : "BSE"}
                  <span className="mx-1 text-zinc-400 dark:text-zinc-500">›</span>
                  {stockCode || "—"}
                  <span className="mx-1 text-zinc-400 dark:text-zinc-500">›</span>
                  {expiryDate || "—"}
                </span>
              </p>
              <div className="max-w-[18rem]">
                <UncoveredNumberStepper
                  compact
                  label="Minimum POP required"
                  value={bullPutMinPop}
                  onChange={setBullPutMinPop}
                  min={1}
                  max={99}
                  suffix="%"
                />
              </div>
              <div className="flex justify-start">
                <button
                  type="button"
                  disabled={
                    !stockCode.trim() ||
                    !expiryDate.trim() ||
                    !chainSuccess ||
                    !bullPutSuggestion
                  }
                  className={`${sb.btnPrimary} inline-flex shrink-0 items-center justify-center px-4 py-2 text-sm`}
                  onClick={fetchBullPutTrades}
                >
                  Fetch Trades
                </button>
              </div>
              {!bullPutSuggestion ? (
                <p className="text-xs text-zinc-500 dark:text-zinc-400">
                  Unable to compute Bull Put Spread legs from this chain.
                </p>
              ) : null}
            </div>
          ) : selectedReadymade === "long_straddle" ||
            selectedReadymade === "short_straddle" ? (
            <p className="text-sm text-zinc-500 dark:text-zinc-400">
              {selectedReadymade === "long_straddle"
                ? "No parameters required — both legs are buys at the ATM strike (ATM CE and PE)."
                : "No parameters required — both legs are sells at the ATM strike (ATM CE and PE). Uncovered shorts carry significant risk and margin."}
            </p>
          ) : selectedReadymade === "long_strangle" ? (
            <div className="space-y-3">
              <p className="mt-1 flex flex-wrap items-baseline gap-x-1.5 gap-y-0.5 text-xs text-zinc-500 dark:text-zinc-400">
                <span className="font-medium tabular-nums text-zinc-700 dark:text-zinc-300">
                  {segmentExchange === "NFO" ? "NSE" : "BSE"}
                  <span className="mx-1 text-zinc-400 dark:text-zinc-500">›</span>
                  {stockCode || "—"}
                  <span className="mx-1 text-zinc-400 dark:text-zinc-500">›</span>
                  {expiryDate || "—"}
                </span>
              </p>
              <div className="max-w-xl">
                <StrangleMoveRangeSlider
                  compact
                  label="Expected move from spot"
                  downPct={longStrangleDownPct}
                  upPct={longStrangleUpPct}
                  onDownChange={handleLongStrangleDownPct}
                  onUpChange={handleLongStrangleUpPct}
                />
              </div>
              <div className="flex justify-start">
                <button
                  type="button"
                  disabled={
                    !stockCode.trim() ||
                    !expiryDate.trim() ||
                    !chainSuccess ||
                    !longStrangleSuggestion
                  }
                  className={`${sb.btnPrimary} inline-flex shrink-0 items-center justify-center px-4 py-2 text-sm`}
                  onClick={fetchLongStrangleTrades}
                >
                  Fetch Trades
                </button>
              </div>
              {!longStrangleSuggestion ? (
                <p className="text-xs text-zinc-500 dark:text-zinc-400">
                  Load the option chain (section 1) to enable Fetch Trades.
                </p>
              ) : null}
            </div>
          ) : selectedReadymade === "short_strangle" ? (
            <div className="space-y-3">
              <p className="mt-1 flex flex-wrap items-baseline gap-x-1.5 gap-y-0.5 text-xs text-zinc-500 dark:text-zinc-400">
                <span className="font-medium tabular-nums text-zinc-700 dark:text-zinc-300">
                  {segmentExchange === "NFO" ? "NSE" : "BSE"}
                  <span className="mx-1 text-zinc-400 dark:text-zinc-500">›</span>
                  {stockCode || "—"}
                  <span className="mx-1 text-zinc-400 dark:text-zinc-500">›</span>
                  {expiryDate || "—"}
                </span>
              </p>
              <div className="max-w-xl">
                <StrangleMoveRangeSlider
                  compact
                  label="Expected range from spot"
                  downPct={shortStrangleDownPct}
                  upPct={shortStrangleUpPct}
                  onDownChange={handleShortStrangleDownPct}
                  onUpChange={handleShortStrangleUpPct}
                />
              </div>
              <div className="flex justify-start">
                <button
                  type="button"
                  disabled={
                    !stockCode.trim() ||
                    !expiryDate.trim() ||
                    !chainSuccess ||
                    !shortStrangleSuggestion
                  }
                  className={`${sb.btnPrimary} inline-flex shrink-0 items-center justify-center px-4 py-2 text-sm`}
                  onClick={fetchShortStrangleTrades}
                >
                  Fetch Trades
                </button>
              </div>
              {!shortStrangleSuggestion ? (
                <p className="text-xs text-zinc-500 dark:text-zinc-400">
                  Load the option chain (section 1) to enable Fetch Trades.
                </p>
              ) : null}
            </div>
          ) : selectedReadymade === "iron_condor" ? (
            <div className="space-y-3">
              <p className="mt-1 flex flex-wrap items-baseline gap-x-1.5 gap-y-0.5 text-xs text-zinc-500 dark:text-zinc-400">
                <span className="font-medium tabular-nums text-zinc-700 dark:text-zinc-300">
                  {segmentExchange === "NFO" ? "NSE" : "BSE"}
                  <span className="mx-1 text-zinc-400 dark:text-zinc-500">›</span>
                  {stockCode || "—"}
                  <span className="mx-1 text-zinc-400 dark:text-zinc-500">›</span>
                  {expiryDate || "—"}
                </span>
              </p>
              <div className="max-w-xl">
                <StrangleMoveRangeSlider
                  compact
                  label="Short spread range from spot"
                  downPct={ironCondorDownPct}
                  upPct={ironCondorUpPct}
                  onDownChange={handleIronCondorDownPct}
                  onUpChange={handleIronCondorUpPct}
                />
              </div>
              <div className="flex justify-start">
                <button
                  type="button"
                  disabled={
                    !stockCode.trim() ||
                    !expiryDate.trim() ||
                    !chainSuccess ||
                    !ironCondorSuggestion
                  }
                  className={`${sb.btnPrimary} inline-flex shrink-0 items-center justify-center px-4 py-2 text-sm`}
                  onClick={fetchIronCondorTrades}
                >
                  Fetch Trades
                </button>
              </div>
              {!ironCondorSuggestion ? (
                <p className="text-xs text-zinc-500 dark:text-zinc-400">
                  Load the option chain (section 1) to enable Fetch Trades, or
                  widen the range — strikes may not form a valid condor.
                </p>
              ) : null}
            </div>
          ) : selectedReadymade === "iron_butterfly" ? (
            <div className="space-y-3">
              <p className="mt-1 flex flex-wrap items-baseline gap-x-1.5 gap-y-0.5 text-xs text-zinc-500 dark:text-zinc-400">
                <span className="font-medium tabular-nums text-zinc-700 dark:text-zinc-300">
                  {segmentExchange === "NFO" ? "NSE" : "BSE"}
                  <span className="mx-1 text-zinc-400 dark:text-zinc-500">›</span>
                  {stockCode || "—"}
                  <span className="mx-1 text-zinc-400 dark:text-zinc-500">›</span>
                  {expiryDate || "—"}
                </span>
              </p>
              <ButterflyWingDistanceSlider
                compact
                label="Wing distance (% from spot each side)"
                value={ironButterflyWingPct}
                onChange={handleIronButterflyWingPct}
              />
              <div className="flex justify-start">
                <button
                  type="button"
                  disabled={
                    !stockCode.trim() ||
                    !expiryDate.trim() ||
                    !chainSuccess ||
                    !ironButterflySuggestion
                  }
                  className={`${sb.btnPrimary} inline-flex shrink-0 items-center justify-center px-4 py-2 text-sm`}
                  onClick={fetchIronButterflyTrades}
                >
                  Fetch Trades
                </button>
              </div>
              {!ironButterflySuggestion ? (
                <p className="text-xs text-zinc-500 dark:text-zinc-400">
                  Load the option chain (section 1) to enable Fetch Trades, or
                  the chain may not support this wing at ATM.
                </p>
              ) : null}
            </div>
          ) : selectedReadymade === "long_call_butterfly" ? (
            <div className="space-y-3">
              <p className="mt-1 flex flex-wrap items-baseline gap-x-1.5 gap-y-0.5 text-xs text-zinc-500 dark:text-zinc-400">
                <span className="font-medium tabular-nums text-zinc-700 dark:text-zinc-300">
                  {segmentExchange === "NFO" ? "NSE" : "BSE"}
                  <span className="mx-1 text-zinc-400 dark:text-zinc-500">›</span>
                  {stockCode || "—"}
                  <span className="mx-1 text-zinc-400 dark:text-zinc-500">›</span>
                  {expiryDate || "—"}
                </span>
              </p>
              <ButterflyWingDistanceSlider
                compact
                label="Wing distance (% from spot each side)"
                value={butterflyWingPct}
                onChange={handleButterflyWingPct}
              />
              <div className="flex justify-start">
                <button
                  type="button"
                  disabled={
                    !stockCode.trim() ||
                    !expiryDate.trim() ||
                    !chainSuccess ||
                    !longCallButterflySuggestion
                  }
                  className={`${sb.btnPrimary} inline-flex shrink-0 items-center justify-center px-4 py-2 text-sm`}
                  onClick={fetchLongCallButterflyTrades}
                >
                  Fetch Trades
                </button>
              </div>
              {!longCallButterflySuggestion ? (
                <p className="text-xs text-zinc-500 dark:text-zinc-400">
                  Load the option chain (section 1) to enable Fetch Trades, or
                  the chain may not support a call butterfly at this wing
                  setting.
                </p>
              ) : null}
            </div>
          ) : (
            <p className="text-sm text-zinc-600 dark:text-zinc-400">
              No additional parameters for this strategy yet. Strategy-specific
              inputs will appear here as we add them.
            </p>
          )}
        </section>

        <section
          ref={proposedTradesSectionRef}
          id="strategy-builder-proposed-trades"
          className={`${sb.section} space-y-4`}
        >
          <h2 className={sb.sectionTitle}>4. Proposed Trades</h2>
          {selectedReadymade === "build-your-own" ? (
            <div className="space-y-3">
              {cq.isFetching && !chainSuccess ? (
                <p className="text-sm text-zinc-500 dark:text-zinc-400">
                  Loading option chain…
                </p>
              ) : null}
              {cq.isError ? (
                <div className="app-alert-error text-xs">
                  {cq.error instanceof Error
                    ? cq.error.message
                    : "Chain request failed"}
                </div>
              ) : null}
              {cq.data &&
              cq.data.Status !== 200 &&
              (cq.data.Error || "").trim() ? (
                <div className="app-alert-error text-xs">
                  {String(cq.data.Error).trim()}
                </div>
              ) : null}
              {chainSuccess?.chain_rows?.length ? (
                <OptionChainTable
                  chainSuccess={chainSuccess}
                  scrollRef={buildOwnChainScrollRef}
                  mode="strategyBuilder"
                  onStrategyBuySell={handleStrategyChainBuySell}
                  isStrategySlotAdded={(strike, right, side) =>
                    buildYourOwnAddedSlots.has(
                      buildYourOwnSlotKey(
                        stockCode,
                        expiryDate,
                        strike,
                        right,
                        side,
                      ),
                    )
                  }
                />
              ) : !cq.isFetching &&
                stockCode.trim() &&
                expiryDate.trim() &&
                cq.data?.Status === 200 ? (
                <p className="text-sm text-zinc-500 dark:text-zinc-400">
                  No option chain rows for this expiry.
                </p>
              ) : !stockCode.trim() || !expiryDate.trim() ? (
                <p className="text-sm text-zinc-500 dark:text-zinc-400">
                  Set underlying and expiry in section 1 to load the chain.
                </p>
              ) : null}
            </div>
          ) : selectedReadymade === "bull_call_spread" ? (
            <div className="space-y-3">
              {bullCallSuggestion ? (
                <div className="grid gap-2 md:grid-cols-3">
                  <div className="bg-zinc-50/70 px-3 py-2 text-xs dark:border-zinc-700/80 dark:bg-zinc-900/35">
                    <div className="text-zinc-500 dark:text-zinc-400">ATM IV</div>
                    <div className="mt-0.5 font-semibold tabular-nums text-zinc-900 dark:text-zinc-100">
                      {bullCallSuggestion.atmIvPct.toFixed(1)}%
                    </div>
                    <div className="mt-1 text-zinc-500 dark:text-zinc-400">
                      1σ range (IV):{" "}
                      <span className="font-medium tabular-nums text-zinc-700 dark:text-zinc-300">
                        {bullCallSuggestion.ivRangeLow.toFixed(2)} -{" "}
                        {bullCallSuggestion.ivRangeHigh.toFixed(2)}
                      </span>
                    </div>
                  </div>
                  <div className="bg-zinc-50/70 px-3 py-2 text-xs dark:border-zinc-700/80 dark:bg-zinc-900/35">
                    <div className="text-zinc-500 dark:text-zinc-400">
                      Highest OI range (Put - Call)
                    </div>
                    <div className="mt-0.5 font-semibold tabular-nums text-zinc-900 dark:text-zinc-100">
                      {bullCallSuggestion.maxPutOiStrike != null &&
                      bullCallSuggestion.maxCallOiStrike != null
                        ? `${bullCallSuggestion.maxPutOiStrike.toLocaleString("en-IN")} - ${bullCallSuggestion.maxCallOiStrike.toLocaleString("en-IN")}`
                        : "—"}
                    </div>
                  </div>
                  <div className="bg-zinc-50/70 px-3 py-2 text-xs dark:border-zinc-700/80 dark:bg-zinc-900/35">
                    <div className="text-zinc-500 dark:text-zinc-400">
                      Auto-selected legs
                    </div>
                    <div className="mt-0.5 font-semibold tabular-nums text-zinc-900 dark:text-zinc-100">
                      Buy {bullCallSuggestion.buyStrike.toLocaleString("en-IN")} CE
                      {" · "}
                      Sell {bullCallSuggestion.sellStrike.toLocaleString("en-IN")} CE
                    </div>
                    <div className="mt-1 text-zinc-500 dark:text-zinc-400">
                      POP target {bullCallSuggestion.minPop}% · model{" "}
                      <span className="font-medium tabular-nums text-zinc-700 dark:text-zinc-300">
                        {legs.length ? `${pop.toFixed(1)}%` : "—"}
                      </span>
                    </div>
                  </div>
                </div>
              ) : null}
              {cq.isFetching && !chainSuccess ? (
                <p className="text-sm text-zinc-500 dark:text-zinc-400">
                  Loading option chain…
                </p>
              ) : null}
              {cq.isError ? (
                <div className="app-alert-error text-xs">
                  {cq.error instanceof Error
                    ? cq.error.message
                    : "Chain request failed"}
                </div>
              ) : null}
              {chainSuccess?.chain_rows?.length ? (
                <OptionChainTable
                  chainSuccess={chainSuccess}
                  scrollRef={buildOwnChainScrollRef}
                  mode="strategyBuilder"
                  onStrategyBuySell={handleStrategyChainBuySell}
                  isStrategySlotAdded={(strike, right, side) =>
                    buildYourOwnAddedSlots.has(
                      buildYourOwnSlotKey(
                        stockCode,
                        expiryDate,
                        strike,
                        right,
                        side,
                      ),
                    )
                  }
                />
              ) : !cq.isFetching &&
                stockCode.trim() &&
                expiryDate.trim() &&
                cq.data?.Status === 200 ? (
                <p className="text-sm text-zinc-500 dark:text-zinc-400">
                  No option chain rows for this expiry.
                </p>
              ) : null}
            </div>
          ) : selectedReadymade === "bear_call_spread" ? (
            <div className="space-y-3">
              {bearCallSuggestion ? (
                <div className="grid gap-2 md:grid-cols-3">
                  <div className="bg-zinc-50/70 px-3 py-2 text-xs dark:border-zinc-700/80 dark:bg-zinc-900/35">
                    <div className="text-zinc-500 dark:text-zinc-400">ATM IV</div>
                    <div className="mt-0.5 font-semibold tabular-nums text-zinc-900 dark:text-zinc-100">
                      {bearCallSuggestion.atmIvPct.toFixed(1)}%
                    </div>
                    <div className="mt-1 text-zinc-500 dark:text-zinc-400">
                      1σ range (IV):{" "}
                      <span className="font-medium tabular-nums text-zinc-700 dark:text-zinc-300">
                        {bearCallSuggestion.ivRangeLow.toFixed(2)} -{" "}
                        {bearCallSuggestion.ivRangeHigh.toFixed(2)}
                      </span>
                    </div>
                  </div>
                  <div className="bg-zinc-50/70 px-3 py-2 text-xs dark:border-zinc-700/80 dark:bg-zinc-900/35">
                    <div className="text-zinc-500 dark:text-zinc-400">
                      Highest OI range (Put - Call)
                    </div>
                    <div className="mt-0.5 font-semibold tabular-nums text-zinc-900 dark:text-zinc-100">
                      {bearCallSuggestion.maxPutOiStrike != null &&
                      bearCallSuggestion.maxCallOiStrike != null
                        ? `${bearCallSuggestion.maxPutOiStrike.toLocaleString("en-IN")} - ${bearCallSuggestion.maxCallOiStrike.toLocaleString("en-IN")}`
                        : "—"}
                    </div>
                  </div>
                  <div className="bg-zinc-50/70 px-3 py-2 text-xs dark:border-zinc-700/80 dark:bg-zinc-900/35">
                    <div className="text-zinc-500 dark:text-zinc-400">
                      Auto-selected legs
                    </div>
                    <div className="mt-0.5 font-semibold tabular-nums text-zinc-900 dark:text-zinc-100">
                      Sell {bearCallSuggestion.sellStrike.toLocaleString("en-IN")}{" "}
                      CE
                      {" · "}
                      Buy {bearCallSuggestion.buyStrike.toLocaleString("en-IN")} CE
                    </div>
                    <div className="mt-1 text-zinc-500 dark:text-zinc-400">
                      POP target {bearCallSuggestion.minPop}% · model{" "}
                      <span className="font-medium tabular-nums text-zinc-700 dark:text-zinc-300">
                        {legs.length ? `${pop.toFixed(1)}%` : "—"}
                      </span>
                    </div>
                  </div>
                </div>
              ) : null}
              {cq.isFetching && !chainSuccess ? (
                <p className="text-sm text-zinc-500 dark:text-zinc-400">
                  Loading option chain…
                </p>
              ) : null}
              {cq.isError ? (
                <div className="app-alert-error text-xs">
                  {cq.error instanceof Error
                    ? cq.error.message
                    : "Chain request failed"}
                </div>
              ) : null}
              {chainSuccess?.chain_rows?.length ? (
                <OptionChainTable
                  chainSuccess={chainSuccess}
                  scrollRef={buildOwnChainScrollRef}
                  mode="strategyBuilder"
                  onStrategyBuySell={handleStrategyChainBuySell}
                  isStrategySlotAdded={(strike, right, side) =>
                    buildYourOwnAddedSlots.has(
                      buildYourOwnSlotKey(
                        stockCode,
                        expiryDate,
                        strike,
                        right,
                        side,
                      ),
                    )
                  }
                />
              ) : !cq.isFetching &&
                stockCode.trim() &&
                expiryDate.trim() &&
                cq.data?.Status === 200 ? (
                <p className="text-sm text-zinc-500 dark:text-zinc-400">
                  No option chain rows for this expiry.
                </p>
              ) : null}
            </div>
          ) : selectedReadymade === "bear_put_spread" ? (
            <div className="space-y-3">
              {bearPutSuggestion ? (
                <div className="grid gap-2 md:grid-cols-3">
                  <div className="bg-zinc-50/70 px-3 py-2 text-xs dark:border-zinc-700/80 dark:bg-zinc-900/35">
                    <div className="text-zinc-500 dark:text-zinc-400">ATM IV</div>
                    <div className="mt-0.5 font-semibold tabular-nums text-zinc-900 dark:text-zinc-100">
                      {bearPutSuggestion.atmIvPct.toFixed(1)}%
                    </div>
                    <div className="mt-1 text-zinc-500 dark:text-zinc-400">
                      1σ range (IV):{" "}
                      <span className="font-medium tabular-nums text-zinc-700 dark:text-zinc-300">
                        {bearPutSuggestion.ivRangeLow.toFixed(2)} -{" "}
                        {bearPutSuggestion.ivRangeHigh.toFixed(2)}
                      </span>
                    </div>
                  </div>
                  <div className="bg-zinc-50/70 px-3 py-2 text-xs dark:border-zinc-700/80 dark:bg-zinc-900/35">
                    <div className="text-zinc-500 dark:text-zinc-400">
                      Highest OI range (Put - Call)
                    </div>
                    <div className="mt-0.5 font-semibold tabular-nums text-zinc-900 dark:text-zinc-100">
                      {bearPutSuggestion.maxPutOiStrike != null &&
                      bearPutSuggestion.maxCallOiStrike != null
                        ? `${bearPutSuggestion.maxPutOiStrike.toLocaleString("en-IN")} - ${bearPutSuggestion.maxCallOiStrike.toLocaleString("en-IN")}`
                        : "—"}
                    </div>
                  </div>
                  <div className="bg-zinc-50/70 px-3 py-2 text-xs dark:border-zinc-700/80 dark:bg-zinc-900/35">
                    <div className="text-zinc-500 dark:text-zinc-400">
                      Auto-selected legs
                    </div>
                    <div className="mt-0.5 font-semibold tabular-nums text-zinc-900 dark:text-zinc-100">
                      Buy {bearPutSuggestion.buyStrike.toLocaleString("en-IN")} PE
                      {" · "}
                      Sell {bearPutSuggestion.sellStrike.toLocaleString("en-IN")} PE
                    </div>
                    <div className="mt-1 text-zinc-500 dark:text-zinc-400">
                      POP target {bearPutSuggestion.minPop}% · model{" "}
                      <span className="font-medium tabular-nums text-zinc-700 dark:text-zinc-300">
                        {legs.length ? `${pop.toFixed(1)}%` : "—"}
                      </span>
                    </div>
                  </div>
                </div>
              ) : null}
              {cq.isFetching && !chainSuccess ? (
                <p className="text-sm text-zinc-500 dark:text-zinc-400">
                  Loading option chain…
                </p>
              ) : null}
              {cq.isError ? (
                <div className="app-alert-error text-xs">
                  {cq.error instanceof Error
                    ? cq.error.message
                    : "Chain request failed"}
                </div>
              ) : null}
              {chainSuccess?.chain_rows?.length ? (
                <OptionChainTable
                  chainSuccess={chainSuccess}
                  scrollRef={buildOwnChainScrollRef}
                  mode="strategyBuilder"
                  onStrategyBuySell={handleStrategyChainBuySell}
                  isStrategySlotAdded={(strike, right, side) =>
                    buildYourOwnAddedSlots.has(
                      buildYourOwnSlotKey(
                        stockCode,
                        expiryDate,
                        strike,
                        right,
                        side,
                      ),
                    )
                  }
                />
              ) : !cq.isFetching &&
                stockCode.trim() &&
                expiryDate.trim() &&
                cq.data?.Status === 200 ? (
                <p className="text-sm text-zinc-500 dark:text-zinc-400">
                  No option chain rows for this expiry.
                </p>
              ) : null}
            </div>
          ) : selectedReadymade === "bull_put_spread" ? (
            <div className="space-y-3">
              {bullPutSuggestion ? (
                <div className="grid gap-2 md:grid-cols-3">
                  <div className="bg-zinc-50/70 px-3 py-2 text-xs dark:border-zinc-700/80 dark:bg-zinc-900/35">
                    <div className="text-zinc-500 dark:text-zinc-400">ATM IV</div>
                    <div className="mt-0.5 font-semibold tabular-nums text-zinc-900 dark:text-zinc-100">
                      {bullPutSuggestion.atmIvPct.toFixed(1)}%
                    </div>
                    <div className="mt-1 text-zinc-500 dark:text-zinc-400">
                      1σ range (IV):{" "}
                      <span className="font-medium tabular-nums text-zinc-700 dark:text-zinc-300">
                        {bullPutSuggestion.ivRangeLow.toFixed(2)} -{" "}
                        {bullPutSuggestion.ivRangeHigh.toFixed(2)}
                      </span>
                    </div>
                  </div>
                  <div className="bg-zinc-50/70 px-3 py-2 text-xs dark:border-zinc-700/80 dark:bg-zinc-900/35">
                    <div className="text-zinc-500 dark:text-zinc-400">
                      Highest OI range (Put - Call)
                    </div>
                    <div className="mt-0.5 font-semibold tabular-nums text-zinc-900 dark:text-zinc-100">
                      {bullPutSuggestion.maxPutOiStrike != null &&
                      bullPutSuggestion.maxCallOiStrike != null
                        ? `${bullPutSuggestion.maxPutOiStrike.toLocaleString("en-IN")} - ${bullPutSuggestion.maxCallOiStrike.toLocaleString("en-IN")}`
                        : "—"}
                    </div>
                  </div>
                  <div className="bg-zinc-50/70 px-3 py-2 text-xs dark:border-zinc-700/80 dark:bg-zinc-900/35">
                    <div className="text-zinc-500 dark:text-zinc-400">
                      Auto-selected legs
                    </div>
                    <div className="mt-0.5 font-semibold tabular-nums text-zinc-900 dark:text-zinc-100">
                      Sell {bullPutSuggestion.sellStrike.toLocaleString("en-IN")}{" "}
                      PE
                      {" · "}
                      Buy {bullPutSuggestion.buyStrike.toLocaleString("en-IN")} PE
                    </div>
                    <div className="mt-1 text-zinc-500 dark:text-zinc-400">
                      POP target {bullPutSuggestion.minPop}% · model{" "}
                      <span className="font-medium tabular-nums text-zinc-700 dark:text-zinc-300">
                        {legs.length ? `${pop.toFixed(1)}%` : "—"}
                      </span>
                    </div>
                  </div>
                </div>
              ) : null}
              {cq.isFetching && !chainSuccess ? (
                <p className="text-sm text-zinc-500 dark:text-zinc-400">
                  Loading option chain…
                </p>
              ) : null}
              {cq.isError ? (
                <div className="app-alert-error text-xs">
                  {cq.error instanceof Error
                    ? cq.error.message
                    : "Chain request failed"}
                </div>
              ) : null}
              {chainSuccess?.chain_rows?.length ? (
                <OptionChainTable
                  chainSuccess={chainSuccess}
                  scrollRef={buildOwnChainScrollRef}
                  mode="strategyBuilder"
                  onStrategyBuySell={handleStrategyChainBuySell}
                  isStrategySlotAdded={(strike, right, side) =>
                    buildYourOwnAddedSlots.has(
                      buildYourOwnSlotKey(
                        stockCode,
                        expiryDate,
                        strike,
                        right,
                        side,
                      ),
                    )
                  }
                />
              ) : !cq.isFetching &&
                stockCode.trim() &&
                expiryDate.trim() &&
                cq.data?.Status === 200 ? (
                <p className="text-sm text-zinc-500 dark:text-zinc-400">
                  No option chain rows for this expiry.
                </p>
              ) : null}
            </div>
          ) : selectedReadymade === "long_straddle" ||
            selectedReadymade === "short_straddle" ? (
            <div className="space-y-3">
              {atmStraddleProposedSummary ? (
                <div className="grid gap-2 md:grid-cols-3">
                  <div className="bg-zinc-50/70 px-3 py-2 text-xs dark:border-zinc-700/80 dark:bg-zinc-900/35">
                    <div className="text-zinc-500 dark:text-zinc-400">ATM IV</div>
                    <div className="mt-0.5 font-semibold tabular-nums text-zinc-900 dark:text-zinc-100">
                      {atmStraddleProposedSummary.atmIvPct.toFixed(1)}%
                    </div>
                    <div className="mt-1 text-zinc-500 dark:text-zinc-400">
                      1σ range (IV):{" "}
                      <span className="font-medium tabular-nums text-zinc-700 dark:text-zinc-300">
                        {atmStraddleProposedSummary.ivRangeLow.toFixed(2)} -{" "}
                        {atmStraddleProposedSummary.ivRangeHigh.toFixed(2)}
                      </span>
                    </div>
                  </div>
                  <div className="bg-zinc-50/70 px-3 py-2 text-xs dark:border-zinc-700/80 dark:bg-zinc-900/35">
                    <div className="text-zinc-500 dark:text-zinc-400">
                      Highest OI range (Put - Call)
                    </div>
                    <div className="mt-0.5 font-semibold tabular-nums text-zinc-900 dark:text-zinc-100">
                      {atmStraddleProposedSummary.maxPutOiStrike != null &&
                      atmStraddleProposedSummary.maxCallOiStrike != null
                        ? `${atmStraddleProposedSummary.maxPutOiStrike.toLocaleString("en-IN")} - ${atmStraddleProposedSummary.maxCallOiStrike.toLocaleString("en-IN")}`
                        : "—"}
                    </div>
                  </div>
                  <div className="bg-zinc-50/70 px-3 py-2 text-xs dark:border-zinc-700/80 dark:bg-zinc-900/35">
                    <div className="text-zinc-500 dark:text-zinc-400">
                      Auto-selected legs
                    </div>
                    <div className="mt-0.5 font-semibold tabular-nums text-zinc-900 dark:text-zinc-100">
                      {selectedReadymade === "long_straddle" ? "Buy" : "Sell"}{" "}
                      {atmStraddleProposedSummary.atmStrike.toLocaleString("en-IN")}{" "}
                      CE · {selectedReadymade === "long_straddle" ? "Buy" : "Sell"}{" "}
                      {atmStraddleProposedSummary.atmStrike.toLocaleString("en-IN")}{" "}
                      PE
                    </div>
                    <div className="mt-1 text-zinc-500 dark:text-zinc-400">
                      Model POP{" "}
                      <span className="font-medium tabular-nums text-zinc-700 dark:text-zinc-300">
                        {legs.length ? `${pop.toFixed(1)}%` : "—"}
                      </span>
                    </div>
                  </div>
                </div>
              ) : null}
              {cq.isFetching && !chainSuccess ? (
                <p className="text-sm text-zinc-500 dark:text-zinc-400">
                  Loading option chain…
                </p>
              ) : null}
              {cq.isError ? (
                <div className="app-alert-error text-xs">
                  {cq.error instanceof Error
                    ? cq.error.message
                    : "Chain request failed"}
                </div>
              ) : null}
              {cq.data &&
              cq.data.Status !== 200 &&
              (cq.data.Error || "").trim() ? (
                <div className="app-alert-error text-xs">
                  {String(cq.data.Error).trim()}
                </div>
              ) : null}
              {chainSuccess?.chain_rows?.length ? (
                <OptionChainTable
                  chainSuccess={chainSuccess}
                  scrollRef={buildOwnChainScrollRef}
                  mode="strategyBuilder"
                  onStrategyBuySell={handleStrategyChainBuySell}
                  isStrategySlotAdded={(strike, right, side) =>
                    buildYourOwnAddedSlots.has(
                      buildYourOwnSlotKey(
                        stockCode,
                        expiryDate,
                        strike,
                        right,
                        side,
                      ),
                    )
                  }
                />
              ) : !cq.isFetching &&
                stockCode.trim() &&
                expiryDate.trim() &&
                cq.data?.Status === 200 ? (
                <p className="text-sm text-zinc-500 dark:text-zinc-400">
                  No option chain rows for this expiry.
                </p>
              ) : !stockCode.trim() || !expiryDate.trim() ? (
                <p className="text-sm text-zinc-500 dark:text-zinc-400">
                  Set underlying and expiry in section 1 to load the chain.
                </p>
              ) : null}
            </div>
          ) : selectedReadymade === "long_strangle" ? (
            <div className="space-y-3">
              {cq.isFetching && !chainSuccess ? (
                <p className="text-sm text-zinc-500 dark:text-zinc-400">
                  Loading option chain…
                </p>
              ) : null}
              {cq.isError ? (
                <div className="app-alert-error text-xs">
                  {cq.error instanceof Error
                    ? cq.error.message
                    : "Chain request failed"}
                </div>
              ) : null}
              {cq.data &&
              cq.data.Status !== 200 &&
              (cq.data.Error || "").trim() ? (
                <div className="app-alert-error text-xs">
                  {String(cq.data.Error).trim()}
                </div>
              ) : null}
              {!longStrangleFetched ? (
                <p className="text-sm text-zinc-500 dark:text-zinc-400">
                  {!stockCode.trim() || !expiryDate.trim()
                    ? "Set underlying and expiry in section 1 first."
                    : "Set the expected move range in section 3, then click Fetch Trades to view the option chain and proposed legs."}
                </p>
              ) : longStrangleApplied ? (
                <>
                  <div className="grid gap-2 md:grid-cols-3">
                    <div className="bg-zinc-50/70 px-3 py-2 text-xs dark:border-zinc-700/80 dark:bg-zinc-900/35">
                      <div className="text-zinc-500 dark:text-zinc-400">ATM IV</div>
                      <div className="mt-0.5 font-semibold tabular-nums text-zinc-900 dark:text-zinc-100">
                        {longStrangleApplied.atmIvPct.toFixed(1)}%
                      </div>
                      <div className="mt-1 text-zinc-500 dark:text-zinc-400">
                        1σ range (IV):{" "}
                        <span className="font-medium tabular-nums text-zinc-700 dark:text-zinc-300">
                          {longStrangleApplied.ivRangeLow.toFixed(2)} -{" "}
                          {longStrangleApplied.ivRangeHigh.toFixed(2)}
                        </span>
                      </div>
                    </div>
                    <div className="bg-zinc-50/70 px-3 py-2 text-xs dark:border-zinc-700/80 dark:bg-zinc-900/35">
                      <div className="text-zinc-500 dark:text-zinc-400">
                        Highest OI range (Put - Call)
                      </div>
                      <div className="mt-0.5 font-semibold tabular-nums text-zinc-900 dark:text-zinc-100">
                        {longStrangleApplied.maxPutOiStrike != null &&
                        longStrangleApplied.maxCallOiStrike != null
                          ? `${longStrangleApplied.maxPutOiStrike.toLocaleString("en-IN")} - ${longStrangleApplied.maxCallOiStrike.toLocaleString("en-IN")}`
                          : "—"}
                      </div>
                    </div>
                    <div className="bg-zinc-50/70 px-3 py-2 text-xs dark:border-zinc-700/80 dark:bg-zinc-900/35">
                      <div className="text-zinc-500 dark:text-zinc-400">
                        Auto-selected legs
                      </div>
                      <div className="mt-0.5 font-semibold tabular-nums text-zinc-900 dark:text-zinc-100">
                        Buy{" "}
                        {longStrangleApplied.callStrike.toLocaleString("en-IN")} CE
                        {" · "}
                        Buy{" "}
                        {longStrangleApplied.putStrike.toLocaleString("en-IN")} PE
                      </div>
                      <div className="mt-1 text-zinc-500 dark:text-zinc-400">
                        Move targets:{" "}
                        <span className="font-medium tabular-nums text-zinc-700 dark:text-zinc-300">
                          {longStrangleApplied.downPct}% / +
                          {longStrangleApplied.upPct}%
                        </span>
                        {spot != null
                          ? ` · spot ${spot.toLocaleString("en-IN")}`
                          : ""}
                      </div>
                    </div>
                  </div>
                  {chainSuccess?.chain_rows?.length ? (
                    <OptionChainTable
                      chainSuccess={chainSuccess}
                      scrollRef={buildOwnChainScrollRef}
                      mode="strategyBuilder"
                      onStrategyBuySell={handleStrategyChainBuySell}
                      isStrategySlotAdded={(strike, right, side) =>
                        buildYourOwnAddedSlots.has(
                          buildYourOwnSlotKey(
                            stockCode,
                            expiryDate,
                            strike,
                            right,
                            side,
                          ),
                        )
                      }
                    />
                  ) : !cq.isFetching &&
                    stockCode.trim() &&
                    expiryDate.trim() &&
                    cq.data?.Status === 200 ? (
                    <p className="text-sm text-zinc-500 dark:text-zinc-400">
                      No option chain rows for this expiry.
                    </p>
                  ) : null}
                </>
              ) : null}
            </div>
          ) : selectedReadymade === "short_strangle" ? (
            <div className="space-y-3">
              {cq.isFetching && !chainSuccess ? (
                <p className="text-sm text-zinc-500 dark:text-zinc-400">
                  Loading option chain…
                </p>
              ) : null}
              {cq.isError ? (
                <div className="app-alert-error text-xs">
                  {cq.error instanceof Error
                    ? cq.error.message
                    : "Chain request failed"}
                </div>
              ) : null}
              {cq.data &&
              cq.data.Status !== 200 &&
              (cq.data.Error || "").trim() ? (
                <div className="app-alert-error text-xs">
                  {String(cq.data.Error).trim()}
                </div>
              ) : null}
              {!shortStrangleFetched ? (
                <p className="text-sm text-zinc-500 dark:text-zinc-400">
                  {!stockCode.trim() || !expiryDate.trim()
                    ? "Set underlying and expiry in section 1 first."
                    : "Set the range lines in section 3, then click Fetch Trades to view the option chain and proposed legs."}
                </p>
              ) : shortStrangleApplied ? (
                <>
                  <div className="grid gap-2 md:grid-cols-3">
                    <div className="bg-zinc-50/70 px-3 py-2 text-xs dark:border-zinc-700/80 dark:bg-zinc-900/35">
                      <div className="text-zinc-500 dark:text-zinc-400">ATM IV</div>
                      <div className="mt-0.5 font-semibold tabular-nums text-zinc-900 dark:text-zinc-100">
                        {shortStrangleApplied.atmIvPct.toFixed(1)}%
                      </div>
                      <div className="mt-1 text-zinc-500 dark:text-zinc-400">
                        1σ range (IV):{" "}
                        <span className="font-medium tabular-nums text-zinc-700 dark:text-zinc-300">
                          {shortStrangleApplied.ivRangeLow.toFixed(2)} -{" "}
                          {shortStrangleApplied.ivRangeHigh.toFixed(2)}
                        </span>
                      </div>
                    </div>
                    <div className="bg-zinc-50/70 px-3 py-2 text-xs dark:border-zinc-700/80 dark:bg-zinc-900/35">
                      <div className="text-zinc-500 dark:text-zinc-400">
                        Highest OI range (Put - Call)
                      </div>
                      <div className="mt-0.5 font-semibold tabular-nums text-zinc-900 dark:text-zinc-100">
                        {shortStrangleApplied.maxPutOiStrike != null &&
                        shortStrangleApplied.maxCallOiStrike != null
                          ? `${shortStrangleApplied.maxPutOiStrike.toLocaleString("en-IN")} - ${shortStrangleApplied.maxCallOiStrike.toLocaleString("en-IN")}`
                          : "—"}
                      </div>
                    </div>
                    <div className="bg-zinc-50/70 px-3 py-2 text-xs dark:border-zinc-700/80 dark:bg-zinc-900/35">
                      <div className="text-zinc-500 dark:text-zinc-400">
                        Auto-selected legs
                      </div>
                      <div className="mt-0.5 font-semibold tabular-nums text-zinc-900 dark:text-zinc-100">
                        Sell{" "}
                        {shortStrangleApplied.callStrike.toLocaleString("en-IN")}{" "}
                        CE
                        {" · "}
                        Sell{" "}
                        {shortStrangleApplied.putStrike.toLocaleString("en-IN")}{" "}
                        PE
                      </div>
                      <div className="mt-1 text-zinc-500 dark:text-zinc-400">
                        Range lines:{" "}
                        <span className="font-medium tabular-nums text-zinc-700 dark:text-zinc-300">
                          {shortStrangleApplied.downPct}% / +
                          {shortStrangleApplied.upPct}%
                        </span>
                        {spot != null
                          ? ` · spot ${spot.toLocaleString("en-IN")}`
                          : ""}
                      </div>
                    </div>
                  </div>
                  {chainSuccess?.chain_rows?.length ? (
                    <OptionChainTable
                      chainSuccess={chainSuccess}
                      scrollRef={buildOwnChainScrollRef}
                      mode="strategyBuilder"
                      onStrategyBuySell={handleStrategyChainBuySell}
                      isStrategySlotAdded={(strike, right, side) =>
                        buildYourOwnAddedSlots.has(
                          buildYourOwnSlotKey(
                            stockCode,
                            expiryDate,
                            strike,
                            right,
                            side,
                          ),
                        )
                      }
                    />
                  ) : !cq.isFetching &&
                    stockCode.trim() &&
                    expiryDate.trim() &&
                    cq.data?.Status === 200 ? (
                    <p className="text-sm text-zinc-500 dark:text-zinc-400">
                      No option chain rows for this expiry.
                    </p>
                  ) : null}
                </>
              ) : null}
            </div>
          ) : selectedReadymade === "iron_condor" ? (
            <div className="space-y-3">
              {cq.isFetching && !chainSuccess ? (
                <p className="text-sm text-zinc-500 dark:text-zinc-400">
                  Loading option chain…
                </p>
              ) : null}
              {cq.isError ? (
                <div className="app-alert-error text-xs">
                  {cq.error instanceof Error
                    ? cq.error.message
                    : "Chain request failed"}
                </div>
              ) : null}
              {cq.data &&
              cq.data.Status !== 200 &&
              (cq.data.Error || "").trim() ? (
                <div className="app-alert-error text-xs">
                  {String(cq.data.Error).trim()}
                </div>
              ) : null}
              {!ironCondorFetched ? (
                <p className="text-sm text-zinc-500 dark:text-zinc-400">
                  {!stockCode.trim() || !expiryDate.trim()
                    ? "Set underlying and expiry in section 1 first."
                    : "Set the short spread range in section 3, then click Fetch Trades to view the option chain and proposed legs."}
                </p>
              ) : ironCondorApplied ? (
                <>
                  <div className="grid gap-2 md:grid-cols-3">
                    <div className="bg-zinc-50/70 px-3 py-2 text-xs dark:border-zinc-700/80 dark:bg-zinc-900/35">
                      <div className="text-zinc-500 dark:text-zinc-400">ATM IV</div>
                      <div className="mt-0.5 font-semibold tabular-nums text-zinc-900 dark:text-zinc-100">
                        {ironCondorApplied.atmIvPct.toFixed(1)}%
                      </div>
                      <div className="mt-1 text-zinc-500 dark:text-zinc-400">
                        1σ range (IV):{" "}
                        <span className="font-medium tabular-nums text-zinc-700 dark:text-zinc-300">
                          {ironCondorApplied.ivRangeLow.toFixed(2)} -{" "}
                          {ironCondorApplied.ivRangeHigh.toFixed(2)}
                        </span>
                      </div>
                    </div>
                    <div className="bg-zinc-50/70 px-3 py-2 text-xs dark:border-zinc-700/80 dark:bg-zinc-900/35">
                      <div className="text-zinc-500 dark:text-zinc-400">
                        Highest OI range (Put - Call)
                      </div>
                      <div className="mt-0.5 font-semibold tabular-nums text-zinc-900 dark:text-zinc-100">
                        {ironCondorApplied.maxPutOiStrike != null &&
                        ironCondorApplied.maxCallOiStrike != null
                          ? `${ironCondorApplied.maxPutOiStrike.toLocaleString("en-IN")} - ${ironCondorApplied.maxCallOiStrike.toLocaleString("en-IN")}`
                          : "—"}
                      </div>
                    </div>
                    <div className="bg-zinc-50/70 px-3 py-2 text-xs dark:border-zinc-700/80 dark:bg-zinc-900/35">
                      <div className="text-zinc-500 dark:text-zinc-400">
                        Auto-selected legs
                      </div>
                      <div className="mt-0.5 font-semibold tabular-nums text-zinc-900 dark:text-zinc-100">
                        Buy{" "}
                        {ironCondorApplied.longPutStrike.toLocaleString("en-IN")}{" "}
                        PE · Sell{" "}
                        {ironCondorApplied.shortPutStrike.toLocaleString("en-IN")}{" "}
                        PE · Sell{" "}
                        {ironCondorApplied.shortCallStrike.toLocaleString("en-IN")}{" "}
                        CE · Buy{" "}
                        {ironCondorApplied.longCallStrike.toLocaleString("en-IN")}{" "}
                        CE
                      </div>
                      <div className="mt-1 text-zinc-500 dark:text-zinc-400">
                        Short range:{" "}
                        <span className="font-medium tabular-nums text-zinc-700 dark:text-zinc-300">
                          {ironCondorApplied.downPct}% / +
                          {ironCondorApplied.upPct}%
                        </span>
                        {spot != null
                          ? ` · spot ${spot.toLocaleString("en-IN")}`
                          : ""}
                        {" · "}model POP{" "}
                        <span className="font-medium tabular-nums text-zinc-700 dark:text-zinc-300">
                          {legs.length ? `${pop.toFixed(1)}%` : "—"}
                        </span>
                      </div>
                    </div>
                  </div>
                  {chainSuccess?.chain_rows?.length ? (
                    <OptionChainTable
                      chainSuccess={chainSuccess}
                      scrollRef={buildOwnChainScrollRef}
                      mode="strategyBuilder"
                      onStrategyBuySell={handleStrategyChainBuySell}
                      isStrategySlotAdded={(strike, right, side) =>
                        buildYourOwnAddedSlots.has(
                          buildYourOwnSlotKey(
                            stockCode,
                            expiryDate,
                            strike,
                            right,
                            side,
                          ),
                        )
                      }
                    />
                  ) : !cq.isFetching &&
                    stockCode.trim() &&
                    expiryDate.trim() &&
                    cq.data?.Status === 200 ? (
                    <p className="text-sm text-zinc-500 dark:text-zinc-400">
                      No option chain rows for this expiry.
                    </p>
                  ) : null}
                </>
              ) : null}
            </div>
          ) : selectedReadymade === "iron_butterfly" ? (
            <div className="space-y-3">
              {cq.isFetching && !chainSuccess ? (
                <p className="text-sm text-zinc-500 dark:text-zinc-400">
                  Loading option chain…
                </p>
              ) : null}
              {cq.isError ? (
                <div className="app-alert-error text-xs">
                  {cq.error instanceof Error
                    ? cq.error.message
                    : "Chain request failed"}
                </div>
              ) : null}
              {cq.data &&
              cq.data.Status !== 200 &&
              (cq.data.Error || "").trim() ? (
                <div className="app-alert-error text-xs">
                  {String(cq.data.Error).trim()}
                </div>
              ) : null}
              {!ironButterflyFetched ? (
                <p className="text-sm text-zinc-500 dark:text-zinc-400">
                  {!stockCode.trim() || !expiryDate.trim()
                    ? "Set underlying and expiry in section 1 first."
                    : "Set wing distance in section 3, then click Fetch Trades to view the option chain and proposed legs."}
                </p>
              ) : ironButterflyApplied ? (
                <>
                  <div className="grid gap-2 md:grid-cols-3">
                    <div className="bg-zinc-50/70 px-3 py-2 text-xs dark:border-zinc-700/80 dark:bg-zinc-900/35">
                      <div className="text-zinc-500 dark:text-zinc-400">ATM IV</div>
                      <div className="mt-0.5 font-semibold tabular-nums text-zinc-900 dark:text-zinc-100">
                        {ironButterflyApplied.atmIvPct.toFixed(1)}%
                      </div>
                      <div className="mt-1 text-zinc-500 dark:text-zinc-400">
                        1σ range (IV):{" "}
                        <span className="font-medium tabular-nums text-zinc-700 dark:text-zinc-300">
                          {ironButterflyApplied.ivRangeLow.toFixed(2)} -{" "}
                          {ironButterflyApplied.ivRangeHigh.toFixed(2)}
                        </span>
                      </div>
                    </div>
                    <div className="bg-zinc-50/70 px-3 py-2 text-xs dark:border-zinc-700/80 dark:bg-zinc-900/35">
                      <div className="text-zinc-500 dark:text-zinc-400">
                        Highest OI range (Put - Call)
                      </div>
                      <div className="mt-0.5 font-semibold tabular-nums text-zinc-900 dark:text-zinc-100">
                        {ironButterflyApplied.maxPutOiStrike != null &&
                        ironButterflyApplied.maxCallOiStrike != null
                          ? `${ironButterflyApplied.maxPutOiStrike.toLocaleString("en-IN")} - ${ironButterflyApplied.maxCallOiStrike.toLocaleString("en-IN")}`
                          : "—"}
                      </div>
                    </div>
                    <div className="bg-zinc-50/70 px-3 py-2 text-xs dark:border-zinc-700/80 dark:bg-zinc-900/35">
                      <div className="text-zinc-500 dark:text-zinc-400">
                        Auto-selected legs
                      </div>
                      <div className="mt-0.5 font-semibold tabular-nums text-zinc-900 dark:text-zinc-100">
                        Buy{" "}
                        {ironButterflyApplied.lowStrike.toLocaleString("en-IN")}{" "}
                        PE · Sell{" "}
                        {ironButterflyApplied.midStrike.toLocaleString("en-IN")}{" "}
                        PE · Sell{" "}
                        {ironButterflyApplied.midStrike.toLocaleString("en-IN")}{" "}
                        CE · Buy{" "}
                        {ironButterflyApplied.highStrike.toLocaleString("en-IN")}{" "}
                        CE
                      </div>
                      <div className="mt-1 text-zinc-500 dark:text-zinc-400">
                        Wing distance {ironButterflyApplied.wingPct}%
                        {spot != null
                          ? ` · spot ${spot.toLocaleString("en-IN")}`
                          : ""}
                        {" · "}model POP{" "}
                        <span className="font-medium tabular-nums text-zinc-700 dark:text-zinc-300">
                          {legs.length ? `${pop.toFixed(1)}%` : "—"}
                        </span>
                      </div>
                    </div>
                  </div>
                  {chainSuccess?.chain_rows?.length ? (
                    <OptionChainTable
                      chainSuccess={chainSuccess}
                      scrollRef={buildOwnChainScrollRef}
                      mode="strategyBuilder"
                      onStrategyBuySell={handleStrategyChainBuySell}
                      isStrategySlotAdded={(strike, right, side) =>
                        buildYourOwnAddedSlots.has(
                          buildYourOwnSlotKey(
                            stockCode,
                            expiryDate,
                            strike,
                            right,
                            side,
                          ),
                        )
                      }
                    />
                  ) : !cq.isFetching &&
                    stockCode.trim() &&
                    expiryDate.trim() &&
                    cq.data?.Status === 200 ? (
                    <p className="text-sm text-zinc-500 dark:text-zinc-400">
                      No option chain rows for this expiry.
                    </p>
                  ) : null}
                </>
              ) : null}
            </div>
          ) : selectedReadymade === "long_call_butterfly" ? (
            <div className="space-y-3">
              {cq.isFetching && !chainSuccess ? (
                <p className="text-sm text-zinc-500 dark:text-zinc-400">
                  Loading option chain…
                </p>
              ) : null}
              {cq.isError ? (
                <div className="app-alert-error text-xs">
                  {cq.error instanceof Error
                    ? cq.error.message
                    : "Chain request failed"}
                </div>
              ) : null}
              {cq.data &&
              cq.data.Status !== 200 &&
              (cq.data.Error || "").trim() ? (
                <div className="app-alert-error text-xs">
                  {String(cq.data.Error).trim()}
                </div>
              ) : null}
              {!butterflyFetched ? (
                <p className="text-sm text-zinc-500 dark:text-zinc-400">
                  {!stockCode.trim() || !expiryDate.trim()
                    ? "Set underlying and expiry in section 1 first."
                    : "Set wing distance in section 3, then click Fetch Trades to view the option chain and proposed legs."}
                </p>
              ) : butterflyApplied ? (
                <>
                  <div className="grid gap-2 md:grid-cols-3">
                    <div className="bg-zinc-50/70 px-3 py-2 text-xs dark:border-zinc-700/80 dark:bg-zinc-900/35">
                      <div className="text-zinc-500 dark:text-zinc-400">ATM IV</div>
                      <div className="mt-0.5 font-semibold tabular-nums text-zinc-900 dark:text-zinc-100">
                        {butterflyApplied.atmIvPct.toFixed(1)}%
                      </div>
                      <div className="mt-1 text-zinc-500 dark:text-zinc-400">
                        1σ range (IV):{" "}
                        <span className="font-medium tabular-nums text-zinc-700 dark:text-zinc-300">
                          {butterflyApplied.ivRangeLow.toFixed(2)} -{" "}
                          {butterflyApplied.ivRangeHigh.toFixed(2)}
                        </span>
                      </div>
                    </div>
                    <div className="bg-zinc-50/70 px-3 py-2 text-xs dark:border-zinc-700/80 dark:bg-zinc-900/35">
                      <div className="text-zinc-500 dark:text-zinc-400">
                        Highest OI range (Put - Call)
                      </div>
                      <div className="mt-0.5 font-semibold tabular-nums text-zinc-900 dark:text-zinc-100">
                        {butterflyApplied.maxPutOiStrike != null &&
                        butterflyApplied.maxCallOiStrike != null
                          ? `${butterflyApplied.maxPutOiStrike.toLocaleString("en-IN")} - ${butterflyApplied.maxCallOiStrike.toLocaleString("en-IN")}`
                          : "—"}
                      </div>
                    </div>
                    <div className="bg-zinc-50/70 px-3 py-2 text-xs dark:border-zinc-700/80 dark:bg-zinc-900/35">
                      <div className="text-zinc-500 dark:text-zinc-400">
                        Auto-selected legs
                      </div>
                      <div className="mt-0.5 font-semibold tabular-nums text-zinc-900 dark:text-zinc-100">
                        Buy{" "}
                        {butterflyApplied.lowStrike.toLocaleString("en-IN")} CE
                        {" · "}Sell 2×{" "}
                        {butterflyApplied.midStrike.toLocaleString("en-IN")} CE
                        {" · "}Buy{" "}
                        {butterflyApplied.highStrike.toLocaleString("en-IN")} CE
                      </div>
                      <div className="mt-1 text-zinc-500 dark:text-zinc-400">
                        Wing distance {butterflyApplied.wingPct}%
                        {spot != null
                          ? ` · spot ${spot.toLocaleString("en-IN")}`
                          : ""}
                        {" · "}model POP{" "}
                        <span className="font-medium tabular-nums text-zinc-700 dark:text-zinc-300">
                          {legs.length ? `${pop.toFixed(1)}%` : "—"}
                        </span>
                      </div>
                    </div>
                  </div>
                  {chainSuccess?.chain_rows?.length ? (
                    <OptionChainTable
                      chainSuccess={chainSuccess}
                      scrollRef={buildOwnChainScrollRef}
                      mode="strategyBuilder"
                      onStrategyBuySell={handleStrategyChainBuySell}
                      isStrategySlotAdded={(strike, right, side) =>
                        buildYourOwnAddedSlots.has(
                          buildYourOwnSlotKey(
                            stockCode,
                            expiryDate,
                            strike,
                            right,
                            side,
                          ),
                        )
                      }
                    />
                  ) : !cq.isFetching &&
                    stockCode.trim() &&
                    expiryDate.trim() &&
                    cq.data?.Status === 200 ? (
                    <p className="text-sm text-zinc-500 dark:text-zinc-400">
                      No option chain rows for this expiry.
                    </p>
                  ) : null}
                </>
              ) : null}
            </div>
          ) : !uncoveredScanResult ? (
            <p className="text-sm text-zinc-500 dark:text-zinc-400">
              {selectedReadymade === "naked-shorts" ||
              selectedReadymade === "covered-shorts"
                ? "Configure parameters in section 3 and run “Fetch Legs” to see candidates."
                : "Proposed legs from readymade strategies will appear here. For Naked or Covered Shorts, run a scan in section 3."}
            </p>
          ) : (
            <div className="space-y-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                  {selectedReadymade === "covered-shorts"
                    ? "Covered short pairs (sell + buy hedge)"
                    : "Uncovered short candidates"}
                </h3>
                <button
                  type="button"
                  className={`${sb.btnSecondary} text-[11px]`}
                  onClick={() => {
                    setUncoveredScanResult(null);
                  }}
                >
                  Clear results
                </button>
              </div>
              {(["CALL", "PUT"] as const).map((kind) => {
                const side =
                  kind === "CALL"
                    ? uncoveredScanResult.ce_options
                    : uncoveredScanResult.pe_options;
                const rows = Array.isArray(side?.Success) ? side.Success : [];
                const err = side?.Error;
                const ok = side?.Status === 200 && rows.length > 0;
                const groupShell =
                  kind === "CALL"
                    ? "rounded-md border border-emerald-200/60 bg-emerald-50/50 p-3 dark:border-emerald-900/35 dark:bg-emerald-950/25"
                    : "rounded-md border border-red-200/60 bg-red-50/45 p-3 dark:border-red-900/35 dark:bg-red-950/20";

                return (
                  <div key={kind} className={groupShell}>
                    <h4
                      className={
                        kind === "CALL"
                          ? "mb-2 border-b border-emerald-200/60 pb-1.5 text-xs font-semibold uppercase tracking-wide text-emerald-900/80 dark:border-emerald-800/40 dark:text-emerald-200/90"
                          : "mb-2 border-b border-red-200/60 pb-1.5 text-xs font-semibold uppercase tracking-wide text-red-900/80 dark:border-red-800/40 dark:text-red-200/90"
                      }
                    >
                      {kind} options
                    </h4>
                    {!ok ? (
                      <p className="text-xs text-zinc-600 dark:text-zinc-400">
                        {err ||
                          (rows.length === 0 && side?.Status === 200
                            ? "No candidates returned."
                            : "No data.")}
                      </p>
                    ) : selectedReadymade === "covered-shorts" ? (
                      <div className="flex flex-col gap-3">
                        {rows.map((opt, idx) => {
                          const cardKey = `${kind}-${String(opt.strike_price)}-${idx}`;
                          const hm = opt.hedge_match as
                            | HedgeMatchPayload
                            | undefined;
                          const pairAdded = isCoveredPairInLegs(
                            opt,
                            legs,
                          );
                          const netPremium = coveredPairNetPremiumLabel(opt);
                          const canAdd = Boolean(hm?.best);
                          return (
                            <div
                              key={cardKey}
                              className="rounded-md border border-zinc-200/80 bg-white/60 p-3 shadow-sm dark:border-zinc-700/80 dark:bg-zinc-950/40"
                            >
                              <div className="flex flex-col gap-3 lg:flex-row lg:items-stretch">
                                <UncoveredScanOptionCard
                                  opt={opt}
                                  legAdded={false}
                                  onAddLeg={() => addUncoveredLeg(opt)}
                                  omitAddButton
                                />
                                <div className="flex flex-col items-center justify-center gap-1 border-y border-dashed border-zinc-300 px-3 py-2 dark:border-zinc-600 lg:min-w-[7rem] lg:border-x lg:border-y-0">
                                  <span className="text-[10px] font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                                    Net premium
                                  </span>
                                  <span
                                    className={`text-sm font-bold tabular-nums ${netPremium.className}`}
                                  >
                                    {netPremium.text}
                                  </span>
                                  <span
                                    className="text-lg text-sky-600 dark:text-sky-400 lg:hidden"
                                    aria-hidden
                                  >
                                    ↓
                                  </span>
                                  <span
                                    className="hidden text-lg text-sky-600 dark:text-sky-400 lg:block"
                                    aria-hidden
                                  >
                                    →
                                  </span>
                                </div>
                                <HedgeSuggestionCard
                                  best={hm?.best ?? null}
                                  error={
                                    hm?.best
                                      ? undefined
                                      : (hm?.Error || "").trim() ||
                                        "No suitable hedge for this short."
                                  }
                                />
                                <div className="flex flex-col justify-end lg:min-w-[9rem]">
                                  <button
                                    type="button"
                                    disabled={pairAdded || !canAdd}
                                    className={
                                      pairAdded || !canAdd
                                        ? "w-full cursor-not-allowed rounded-lg border border-zinc-200 bg-zinc-100 py-2.5 text-sm font-semibold text-zinc-500 dark:border-zinc-700 dark:bg-zinc-800/80 dark:text-zinc-400"
                                        : "w-full rounded-lg border border-sky-600 bg-transparent py-2.5 text-sm font-semibold text-sky-700 transition hover:bg-sky-600 hover:text-white focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-500/50 dark:border-sky-500 dark:text-sky-300 dark:hover:bg-sky-600 dark:hover:text-white"
                                    }
                                    onClick={() => addCoveredPair(opt)}
                                  >
                                    {pairAdded ? "Pair added" : "Add pair"}
                                  </button>
                                </div>
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    ) : (
                      <div className="flex flex-wrap gap-2">
                        {rows.map((opt, idx) => {
                          const cardKey = `${kind}-${String(opt.strike_price)}-${idx}`;
                          return (
                            <UncoveredScanOptionCard
                              key={cardKey}
                              opt={opt}
                              legAdded={addedUncoveredContractKeys.has(
                                uncoveredScanOptionKey(opt),
                              )}
                              onAddLeg={() => addUncoveredLeg(opt)}
                            />
                          );
                        })}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </section>

        <section
          ref={legsSectionRef}
          id="strategy-builder-legs"
          className={`${sb.section} space-y-4`}
        >
          <h2 className={sb.sectionTitle}>5. Legs</h2>
          <div className="space-y-3 md:hidden">
            {legs.length === 0 ? (
              <p className="text-sm text-zinc-500 dark:text-zinc-400">
                No legs yet. Add contracts from the option chain or a readymade
                strategy.
              </p>
            ) : (
              legs.map((l) => {
                const qtyU =
                  l.lots > 0 ? Math.round(l.lots * lotSize) : 0;
                const premTotal = (l.premiumPerUnit ?? 0) * qtyU;
                const legEntry = legMarginCache[l.id];
                const legMarginFresh = legMarginEntryMatches(l, legEntry);
                const marginPerLot =
                  legMarginFresh &&
                  legEntry != null &&
                  legEntry.span != null &&
                  Number.isFinite(legEntry.span) &&
                  l.lots > 0
                    ? legEntry.span / l.lots
                    : null;
                return (
                  <div
                    key={l.id}
                    className="rounded-lg border border-zinc-200/80 bg-white/80 p-3 text-sm text-zinc-700 dark:border-zinc-700/80 dark:bg-zinc-950/40 dark:text-zinc-300"
                  >
                    <div className="flex flex-wrap items-start justify-between gap-2 border-b border-zinc-200/80 pb-2 dark:border-zinc-700/80">
                      <div className="min-w-0 flex-1">
                        <p className="text-xs font-medium text-zinc-900 dark:text-zinc-100">
                          {formatOptionSymbolLabel(
                            stockCode,
                            expiryDate,
                            l.strike,
                            l.right,
                          )}
                        </p>
                        <div className="mt-1.5">
                          <LegPositionChip side={l.side} />
                        </div>
                      </div>
                      <button
                        type="button"
                        className="shrink-0 text-sm font-medium text-red-600 dark:text-red-400"
                        onClick={() =>
                          setLegs((prev) => prev.filter((x) => x.id !== l.id))
                        }
                      >
                        Delete
                      </button>
                    </div>
                    <div className="mt-3 grid grid-cols-2 gap-3">
                      <label className="min-w-0 space-y-1">
                        <span className="text-[11px] font-medium text-zinc-500 dark:text-zinc-400">
                          Quantity
                        </span>
                        <LegQuantityInput
                          legId={l.id}
                          lots={l.lots}
                          lotSize={lotSize}
                          onLotsChange={(newLots) =>
                            setLegs((prev) =>
                              prev.map((x) =>
                                x.id === l.id ? { ...x, lots: newLots } : x,
                              ),
                            )
                          }
                          className={`${sb.tableInput} w-full max-w-full tabular-nums`}
                        />
                      </label>
                      <div className="space-y-1">
                        <span className="text-[11px] font-medium text-zinc-500 dark:text-zinc-400">
                          Lot size
                        </span>
                        <p className="pt-1.5 tabular-nums text-zinc-900 dark:text-zinc-100">
                          {lotSize.toLocaleString("en-IN")}
                        </p>
                      </div>
                      <label className="min-w-0 space-y-1">
                        <span className="text-[11px] font-medium text-zinc-500 dark:text-zinc-400">
                          Price (₹)
                        </span>
                        <input
                          type="number"
                          min={0}
                          step={0.05}
                          className={`${sb.tableInput} w-full max-w-full tabular-nums`}
                          value={
                            l.premiumPerUnit != null
                              ? l.premiumPerUnit
                              : ""
                          }
                          onChange={(e) => {
                            const v = parseFloat(e.target.value);
                            setLegs((prev) =>
                              prev.map((x) =>
                                x.id === l.id
                                  ? {
                                      ...x,
                                      premiumPerUnit: Number.isFinite(v)
                                        ? v
                                        : undefined,
                                    }
                                  : x,
                              ),
                            );
                          }}
                        />
                      </label>
                      <div className="space-y-1">
                        <span className="text-[11px] font-medium text-zinc-500 dark:text-zinc-400">
                          Premium
                        </span>
                        <p className="pt-1.5 tabular-nums text-zinc-900 dark:text-zinc-100">
                          {formatIndianMoneyCompact(premTotal)}
                        </p>
                      </div>
                    </div>
                    <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-zinc-200/80 pt-2 dark:border-zinc-700/80">
                      <div className="min-w-0 flex-1 space-y-0.5">
                        <span className="text-[11px] font-medium text-zinc-500 dark:text-zinc-400">
                          Margin / lot
                        </span>
                        <div className="tabular-nums text-zinc-900 dark:text-zinc-100">
                          {l.lots <= 0 ? (
                            "—"
                          ) : legMarginFetchingId === l.id ? (
                            "…"
                          ) : marginPerLot != null &&
                            Number.isFinite(marginPerLot) ? (
                            formatIndianMoneyCompact(marginPerLot)
                          ) : legMarginFresh && legEntry?.error ? (
                            "—"
                          ) : (
                            <MarginRefreshIconButton
                              label="Fetch margin for this leg"
                              onClick={() => void fetchLegMargin(l)}
                            />
                          )}
                        </div>
                      </div>
                      <div className="min-w-0 flex-1 space-y-0.5 text-end">
                        <span className="text-[11px] font-medium text-zinc-500 dark:text-zinc-400">
                          Margin
                        </span>
                        <div className="tabular-nums text-zinc-900 dark:text-zinc-100">
                          {l.lots <= 0 ? (
                            "—"
                          ) : legMarginFetchingId === l.id ? (
                            "…"
                          ) : legMarginFresh &&
                            legEntry != null &&
                            legEntry.span != null &&
                            Number.isFinite(legEntry.span) ? (
                            formatIndianMoneyCompact(legEntry.span)
                          ) : legMarginFresh && legEntry?.error ? (
                            <span className="text-xs text-red-600 dark:text-red-400">
                              {legEntry.error}
                            </span>
                          ) : (
                            <MarginRefreshIconButton
                              label="Fetch margin for this leg"
                              onClick={() => void fetchLegMargin(l)}
                            />
                          )}
                        </div>
                      </div>
                    </div>
                  </div>
                );
              })
            )}
            {legs.length > 0 ? (
              <div className="rounded-lg border border-zinc-200/80 bg-zinc-50/90 px-3 py-2.5 text-sm dark:border-zinc-700/80 dark:bg-zinc-900/50">
                <div className="text-xs font-semibold uppercase tracking-wide text-zinc-600 dark:text-zinc-300">
                  Totals
                </div>
                <div className="mt-2 flex flex-wrap justify-between gap-2">
                  <span className="text-zinc-500 dark:text-zinc-400">
                    Net premium
                  </span>
                  <span
                    className={`font-semibold tabular-nums ${
                      totalsNetPremium < 0
                        ? "text-red-700 dark:text-red-400"
                        : "text-zinc-900 dark:text-zinc-100"
                    }`}
                  >
                    {formatNetPremiumCompactInr(totalsNetPremium)}
                  </span>
                </div>
                <div className="mt-1 flex flex-wrap justify-between gap-2">
                  <span className="text-zinc-500 dark:text-zinc-400">
                    Total margin
                  </span>
                  <span className="font-semibold tabular-nums text-zinc-900 dark:text-zinc-100">
                    {!totalsMargin.hasPositiveLots ? (
                      "—"
                    ) : totalsMargin.hasMarginFetchInFlight ? (
                      "…"
                    ) : totalsMargin.hasMissingFreshMargin ? (
                      "—"
                    ) : (
                      formatIndianMoneyCompact(totalsMargin.sum)
                    )}
                  </span>
                </div>
              </div>
            ) : null}
          </div>
          <div className="hidden md:block">
            <div className="app-table-wrap">
            <table className="w-full min-w-[56rem] border-collapse text-left text-xs">
              <thead className="app-table-head">
                <tr>
                  <th className="px-2 py-1.5 font-medium">Option</th>
                  <th className="px-2 py-1.5 font-medium">Position</th>
                  <th className="px-2 py-1.5 font-medium">Quantity</th>
                  <th className="px-2 py-1.5 font-medium">Lot Size</th>
                  <th className="px-2 py-1.5 font-medium">Price</th>
                  <th className="px-2 py-1.5 font-medium">Premium</th>
                  <th className="px-2 py-1.5 font-medium">Margin / Lot</th>
                  <th className="px-2 py-1.5 font-medium">Margin</th>
                  <th className="px-2 py-1.5 font-medium" />
                </tr>
              </thead>
              <tbody>
                {legs.map((l) => {
                  const qtyU =
                    l.lots > 0 ? Math.round(l.lots * lotSize) : 0;
                  const premTotal = (l.premiumPerUnit ?? 0) * qtyU;
                  const legEntry = legMarginCache[l.id];
                  const legMarginFresh = legMarginEntryMatches(l, legEntry);
                  const marginPerLot =
                    legMarginFresh &&
                    legEntry != null &&
                    legEntry.span != null &&
                    Number.isFinite(legEntry.span) &&
                    l.lots > 0
                      ? legEntry.span / l.lots
                      : null;
                  return (
                    <tr key={l.id} className="app-table-row">
                      <td className="max-w-[14rem] px-2 py-1.5 text-xs text-zinc-800 dark:text-zinc-200">
                        {formatOptionSymbolLabel(
                          stockCode,
                          expiryDate,
                          l.strike,
                          l.right,
                        )}
                      </td>
                      <td className="px-2 py-1.5">
                        <LegPositionChip side={l.side} />
                      </td>
                      <td className="px-2 py-1.5">
                        <LegQuantityInput
                          legId={l.id}
                          lots={l.lots}
                          lotSize={lotSize}
                          onLotsChange={(newLots) =>
                            setLegs((prev) =>
                              prev.map((x) =>
                                x.id === l.id ? { ...x, lots: newLots } : x,
                              ),
                            )
                          }
                          className={`${sb.tableInput} w-[7.5rem] tabular-nums`}
                        />
                      </td>
                      <td className="px-2 py-1.5 tabular-nums text-zinc-800 dark:text-zinc-200">
                        {lotSize.toLocaleString("en-IN")}
                      </td>
                      <td className="px-2 py-1.5">
                        <input
                          type="number"
                          min={0}
                          step={0.05}
                          className={`${sb.tableInput} w-[6rem] tabular-nums`}
                          value={
                            l.premiumPerUnit != null
                              ? l.premiumPerUnit
                              : ""
                          }
                          onChange={(e) => {
                            const v = parseFloat(e.target.value);
                            setLegs((prev) =>
                              prev.map((x) =>
                                x.id === l.id
                                  ? {
                                      ...x,
                                      premiumPerUnit: Number.isFinite(v)
                                        ? v
                                        : undefined,
                                    }
                                  : x,
                              ),
                            );
                          }}
                        />
                      </td>
                      <td className="px-2 py-1.5 tabular-nums text-zinc-800 dark:text-zinc-200">
                        {formatIndianMoneyCompact(premTotal)}
                      </td>
                      <td className="px-2 py-1.5 tabular-nums text-zinc-800 dark:text-zinc-200">
                        {l.lots <= 0 ? (
                          "—"
                        ) : legMarginFetchingId === l.id ? (
                          "…"
                        ) : marginPerLot != null &&
                          Number.isFinite(marginPerLot) ? (
                          formatIndianMoneyCompact(marginPerLot)
                        ) : legMarginFresh && legEntry?.error ? (
                          "—"
                        ) : (
                          <MarginRefreshIconButton
                            label="Fetch margin for this leg"
                            onClick={() => void fetchLegMargin(l)}
                          />
                        )}
                      </td>
                      <td className="px-2 py-1.5 tabular-nums text-zinc-800 dark:text-zinc-200">
                        {l.lots <= 0 ? (
                          "—"
                        ) : legMarginFetchingId === l.id ? (
                          "…"
                        ) : legMarginFresh &&
                          legEntry != null &&
                          legEntry.span != null &&
                          Number.isFinite(legEntry.span) ? (
                          formatIndianMoneyCompact(legEntry.span)
                        ) : legMarginFresh && legEntry?.error ? (
                          legEntry.error
                        ) : (
                          <MarginRefreshIconButton
                            label="Fetch margin for this leg"
                            onClick={() => void fetchLegMargin(l)}
                          />
                        )}
                      </td>
                      <td className="px-2 py-1.5">
                        <button
                          type="button"
                          className="text-red-600 dark:text-red-400"
                          onClick={() =>
                            setLegs((prev) =>
                              prev.filter((x) => x.id !== l.id),
                            )
                          }
                        >
                          Delete
                        </button>
                      </td>
                    </tr>
                  );
                })}
                {legs.length > 0 ? (
                  <tr className="border-t border-zinc-200/80 bg-zinc-50/80 dark:border-zinc-700/80 dark:bg-zinc-900/40">
                    <td
                      className="px-2 py-2 text-xs font-semibold uppercase tracking-wide text-zinc-600 dark:text-zinc-300"
                      colSpan={5}
                    >
                      Totals
                    </td>
                    <td
                      className={`px-2 py-2 text-xs font-semibold tabular-nums ${
                        totalsNetPremium < 0
                          ? "text-red-700 dark:text-red-400"
                          : "text-zinc-900 dark:text-zinc-100"
                      }`}
                    >
                      {formatNetPremiumCompactInr(totalsNetPremium)}
                    </td>
                    <td className="px-2 py-2 text-xs text-zinc-500 dark:text-zinc-400">
                      —
                    </td>
                    <td className="px-2 py-2 text-xs font-semibold tabular-nums text-zinc-900 dark:text-zinc-100">
                      {!totalsMargin.hasPositiveLots ? (
                        "—"
                      ) : totalsMargin.hasMarginFetchInFlight ? (
                        "…"
                      ) : totalsMargin.hasMissingFreshMargin ? (
                        "—"
                      ) : (
                        formatIndianMoneyCompact(totalsMargin.sum)
                      )}
                    </td>
                    <td className="px-2 py-2" />
                  </tr>
                ) : null}
              </tbody>
            </table>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              disabled={
                !legs.length ||
                legs.some((x) => x.lots <= 0) ||
                !stockCode ||
                !expiryDate
              }
              onClick={() => setExecutePreviewOpen(true)}
              className={sb.btnPrimary}
            >
              Execute Legs
            </button>
          </div>
        </section>

        <section className={`${sb.section} space-y-5`}>
          <div className="flex flex-wrap items-center gap-2">
            <h2 className={sb.sectionTitle}>6. Payoff Simulation</h2>
          </div>
          <div className="sticky top-0 z-10 -mx-0.5 py-1">
            <div
              className={`${sb.stickyBar} flex flex-wrap gap-x-6 gap-y-3 text-xs`}
            >
              <div className="transition-opacity">
                <div className="text-[11px] font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                  Max profit
                </div>
                <div className={`font-semibold tabular-nums ${profitClass}`}>
                  {hasStrategyLegs
                    ? formatIndianMoneyCompact(summary.maxProfit)
                    : "—"}
                </div>
              </div>
              <div className="transition-opacity">
                <div className="text-[11px] font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                  Max loss
                </div>
                <div className={`font-semibold tabular-nums ${lossClass}`}>
                  {hasStrategyLegs
                    ? formatIndianMoneyCompact(summary.maxLoss)
                    : "—"}
                </div>
              </div>
              <div className="transition-opacity">
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
                  {marginQ.isFetching ? (
                    "…"
                  ) : strategyMarginQtyStale ? (
                    <MarginRefreshIconButton
                      label="Refresh margin (SPAN)"
                      onClick={() => void refetchStrategyMargin()}
                    />
                  ) : spanMargin != null && Number.isFinite(spanMargin) ? (
                    formatIndianMoneyCompact(spanMargin)
                  ) : (
                    marginState?.Error ??
                    (marginQ.isError ? "Margin request failed" : "—")
                  )}
                </div>
                {strategyBuilderMarginWarnings.length > 0 ? (
                  <div className="mt-1 app-alert-error text-[11px]">
                    {strategyBuilderMarginWarnings[0]}
                  </div>
                ) : null}
              </div>
              <div>
                <div className="text-[11px] font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                  Spot / IV
                </div>
                <div className="font-medium tabular-nums text-zinc-800 dark:text-zinc-200">
                  {chainReady && spot != null ? (
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
                  onChange={(e) => setShowToday(e.target.checked)}
                />
                <span className="relative h-5 w-9 rounded-full bg-zinc-300 transition-colors duration-200 after:absolute after:left-0.5 after:top-0.5 after:h-4 after:w-4 after:rounded-full after:bg-white after:shadow-sm after:transition-transform after:duration-200 peer-checked:bg-sky-600 peer-checked:after:translate-x-4 peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-sky-500 dark:bg-zinc-700 dark:peer-checked:bg-sky-500" />
                <span>Show today (model)</span>
              </label>
            </div>
            <p className="text-[11px] leading-relaxed text-zinc-500 dark:text-zinc-400">
              Solid green = P&amp;L at expiry; dotted violet = mark-to-model now.
              Amber dashes = breakevens. Max profit, max loss, breakevens, and the
              chart follow your legs and inputs (premiums, IV shock) as you edit.
            </p>
            <div className="transition-opacity">
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
          </div>

          <div className="space-y-4 border-t border-zinc-200 pt-4 dark:border-zinc-800">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
              <div>
                <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                  Greeks &amp; IV shock
                </h3>
                <p className="mt-1 max-w-md text-[11px] leading-relaxed text-zinc-500 dark:text-zinc-400">
                  Portfolio sensitivities use the same model as the payoff chart
                  (Black–Scholes, shocked IV).
                </p>
              </div>
              <div
                className={`${sb.checkboxRow} shrink-0 self-start gap-2 text-xs font-medium leading-snug text-zinc-600 dark:text-zinc-400`}
              >
                <button
                  type="button"
                  role="switch"
                  aria-checked={showGreeks}
                  aria-label="Toggle Show Greeks"
                  onClick={() => setShowGreeks((v) => !v)}
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
            <IvShockSlider value={ivShockPct} onChange={setIvShockPct} />
            {showGreeks && legs.length ? (
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                {(
                  [
                    { key: "delta", label: "Delta", fmt: greeks.delta.toFixed(4) },
                    {
                      key: "gamma",
                      label: "Gamma",
                      fmt: greeks.gamma.toFixed(6),
                    },
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
      </div>

      <OrderExecutionConfirmDialog
        open={executePreviewOpen}
        onClose={() => setExecutePreviewOpen(false)}
        stockCode={stockCode}
        exchangeCode={segmentExchange}
        expiryDisplay={expiryDate}
        legs={strategyExecuteLegs}
        controlledChunk={{
          chunkQty,
          onChunkQtyChange: setChunkQty,
          defaultsQuery: chunkDefaultsQ,
          chunkReady,
        }}
      />

      {nakedPrompt ? (
        <div
          className="fixed inset-0 z-[120] flex items-center justify-center bg-black/50 p-4"
          role="presentation"
          onClick={() => setNakedPrompt(null)}
          onKeyDown={(e) => {
            if (e.key === "Escape") setNakedPrompt(null);
          }}
        >
          <div
            role="dialog"
            aria-modal="true"
            className={`${sb.modalPanel} w-full max-w-md`}
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => e.stopPropagation()}
          >
            <h3 className="text-base font-semibold text-red-700 dark:text-red-400">
              Risk warning — uncovered short
            </h3>
            <p className="text-sm leading-relaxed text-zinc-600 dark:text-zinc-400">
              Short naked options can have large or unlimited loss. Confirm you
              understand margin and risk before adding this leg set.
            </p>
            <div className="flex justify-end gap-2 pt-1">
              <button
                type="button"
                className={sb.btnSecondary}
                onClick={() => setNakedPrompt(null)}
              >
                Cancel
              </button>
              <button
                type="button"
                className={sb.btnDanger}
                onClick={confirmNaked}
              >
                I understand — add
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {showChainJumpNav ? (
        <>
          <button
            type="button"
            className={`${sb.btnSecondary} fixed left-1/2 top-[max(4.75rem,calc(env(safe-area-inset-top)+3.5rem))] z-[45] -translate-x-1/2 whitespace-nowrap !rounded-full px-3.5 py-2 text-xs font-semibold shadow-lg ring-1 ring-zinc-950/10 backdrop-blur-sm dark:ring-white/10`}
            aria-label="Scroll to Parameters (section 3)"
            title="Jump to Parameters"
            onClick={() => scrollToStrategySection(parametersSectionRef)}
          >
            ↑ Parameters
          </button>
          <button
            type="button"
            className={`${sb.btnSecondary} fixed bottom-[max(1rem,calc(env(safe-area-inset-bottom)+0.75rem))] left-1/2 z-[45] -translate-x-1/2 whitespace-nowrap !rounded-full px-3.5 py-2 text-xs font-semibold shadow-lg ring-1 ring-zinc-950/10 backdrop-blur-sm dark:ring-white/10`}
            aria-label="Scroll to Legs (section 5)"
            title="Jump to Legs"
            onClick={() => scrollToStrategySection(legsSectionRef)}
          >
            ↓ Legs
          </button>
        </>
      ) : null}
      </RevokedTradingPageGuard>
    </AppShell>
  );
}
