"use client";

import { useMemo } from "react";
import { sb } from "@/lib/strategy-builder/ui";

const PCT_MIN = -20;
const PCT_MAX = 20;
const SPAN = PCT_MAX - PCT_MIN;

function absFromPct(spot: number, pct: number): number {
  return Math.round(spot * (1 + pct / 100));
}

function pctFromAbs(spot: number, abs: number): number {
  return ((abs / spot) - 1) * 100;
}

function clampPct(pct: number): number {
  return Math.min(PCT_MAX, Math.max(PCT_MIN, pct));
}

function SpotRangeKnob({
  value,
  pct,
  variant,
}: {
  value: number;
  pct: number;
  variant: "lower" | "upper";
}) {
  const z = variant === "upper" ? "z-[45]" : "z-[40]";
  const label = value.toLocaleString("en-IN", { maximumFractionDigits: 0 });
  return (
    <div
      className={`pointer-events-none absolute top-1/2 ${z} flex h-8 min-w-[2.75rem] -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full border-[3px] border-blue-600 bg-white px-1 text-[10px] font-bold tabular-nums leading-none text-zinc-900 shadow-[0_0_0_1px_rgb(37_99_235/0.12),0_3px_8px_rgb(15_23_42/0.16)] dark:border-blue-500 dark:bg-zinc-900 dark:text-zinc-50 dark:shadow-[0_0_0_1px_rgb(59_130_246/0.2),0_3px_8px_rgb(0_0_0/0.35)]`}
      style={{
        left: `clamp(1rem, ${pct}%, calc(100% - 1rem))`,
      }}
      aria-hidden
    >
      {label}
    </div>
  );
}

export function SpotRangeSlider({
  spot,
  rangeLower,
  rangeUpper,
  onRangeLowerChange,
  onRangeUpperChange,
}: {
  spot: number;
  rangeLower: number;
  rangeUpper: number;
  onRangeLowerChange: (v: number) => void;
  onRangeUpperChange: (v: number) => void;
}) {
  const absMin = useMemo(() => absFromPct(spot, PCT_MIN), [spot]);
  const absMax = useMemo(() => absFromPct(spot, PCT_MAX), [spot]);

  const lowerPct = useMemo(() => {
    const p = clampPct(pctFromAbs(spot, rangeLower));
    const upperP = clampPct(pctFromAbs(spot, rangeUpper));
    return Math.min(p, upperP - 1);
  }, [spot, rangeLower, rangeUpper]);

  const upperPct = useMemo(() => {
    const p = clampPct(pctFromAbs(spot, rangeUpper));
    const lowerP = clampPct(pctFromAbs(spot, rangeLower));
    return Math.max(p, lowerP + 1);
  }, [spot, rangeLower, rangeUpper]);

  const minPctPos = ((lowerPct - PCT_MIN) / SPAN) * 100;
  const maxPctPos = ((upperPct - PCT_MIN) / SPAN) * 100;
  const spotPctPos = ((0 - PCT_MIN) / SPAN) * 100;

  const thumbInteractiveCls =
    "[&::-webkit-slider-thumb]:pointer-events-auto [&::-moz-range-thumb]:pointer-events-auto";

  const applyLowerPct = (pct: number) => {
    const clamped = Math.min(pct, upperPct - 1);
    onRangeLowerChange(absFromPct(spot, clamped));
  };

  const applyUpperPct = (pct: number) => {
    const clamped = Math.max(pct, lowerPct + 1);
    onRangeUpperChange(absFromPct(spot, clamped));
  };

  const applyLowerAbs = (raw: string) => {
    const n = parseFloat(raw.replace(/,/g, ""));
    if (!Number.isFinite(n)) return;
    const clamped = Math.min(Math.max(n, absMin), rangeUpper - 1);
    onRangeLowerChange(Math.round(clamped));
  };

  const applyUpperAbs = (raw: string) => {
    const n = parseFloat(raw.replace(/,/g, ""));
    if (!Number.isFinite(n)) return;
    const clamped = Math.max(Math.min(n, absMax), rangeLower + 1);
    onRangeUpperChange(Math.round(clamped));
  };

  return (
    <div className="min-w-0 space-y-3 sm:col-span-2 lg:col-span-3">
      <span className={sb.fieldLabel}>Strike range (from spot price)</span>
      <div className="relative flex h-10 w-full shrink-0 items-center">
        <div className="relative h-7 w-full overflow-visible">
          <div className="pointer-events-none absolute top-1/2 h-1.5 w-full -translate-y-1/2 rounded-full bg-zinc-200 dark:bg-zinc-700/85" />
          <div
            className="pointer-events-none absolute top-1/2 h-1.5 -translate-y-1/2 rounded-full bg-blue-600 dark:bg-blue-500"
            style={{
              left: `${minPctPos}%`,
              width: `${Math.max(0, maxPctPos - minPctPos)}%`,
            }}
          />
          <div
            className="pointer-events-none absolute top-1/2 z-[35] -translate-x-1/2 -translate-y-1/2"
            style={{ left: `${spotPctPos}%` }}
            aria-hidden
          >
            <div className="size-2.5 rounded-full bg-zinc-500 ring-2 ring-white dark:bg-zinc-400 dark:ring-zinc-900" />
          </div>
          <input
            type="range"
            min={PCT_MIN}
            max={PCT_MAX}
            step={1}
            value={lowerPct}
            onChange={(e) => applyLowerPct(Number(e.target.value))}
            className={`sb-range-slim sb-range-otm pointer-events-none absolute inset-0 z-20 w-full min-w-0 bg-transparent ${thumbInteractiveCls}`}
            aria-label="Range lower bound"
            aria-valuetext={`${rangeLower}`}
          />
          <input
            type="range"
            min={PCT_MIN}
            max={PCT_MAX}
            step={1}
            value={upperPct}
            onChange={(e) => applyUpperPct(Number(e.target.value))}
            className={`sb-range-slim sb-range-otm pointer-events-none absolute inset-0 z-30 w-full min-w-0 bg-transparent ${thumbInteractiveCls}`}
            aria-label="Range upper bound"
            aria-valuetext={`${rangeUpper}`}
          />
          <SpotRangeKnob value={rangeLower} pct={minPctPos} variant="lower" />
          <SpotRangeKnob value={rangeUpper} pct={maxPctPos} variant="upper" />
        </div>
      </div>
      <div className="flex justify-between gap-4 text-[10px] font-medium tabular-nums text-zinc-400 dark:text-zinc-500">
        <span>−20%</span>
        <span className="text-zinc-600 dark:text-zinc-300">
          Spot {spot.toLocaleString("en-IN", { maximumFractionDigits: 2 })}
        </span>
        <span>+20%</span>
      </div>
      <div className="grid grid-cols-2 gap-4">
        <label className="block">
          <span className="mb-1 block text-[10px] font-medium text-zinc-500 dark:text-zinc-400">
            Lower bound
          </span>
          <input
            type="number"
            className={sb.input}
            value={rangeLower}
            min={absMin}
            max={rangeUpper - 1}
            step={1}
            onChange={(e) => applyLowerAbs(e.target.value)}
          />
        </label>
        <label className="block">
          <span className="mb-1 block text-[10px] font-medium text-zinc-500 dark:text-zinc-400">
            Upper bound
          </span>
          <input
            type="number"
            className={sb.input}
            value={rangeUpper}
            min={rangeLower + 1}
            max={absMax}
            step={1}
            onChange={(e) => applyUpperAbs(e.target.value)}
          />
        </label>
      </div>
    </div>
  );
}
