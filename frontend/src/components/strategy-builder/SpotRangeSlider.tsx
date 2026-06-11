"use client";

import { useCallback, useMemo, useState } from "react";
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

function parsePositiveNum(v: string): number | null {
  const n = parseFloat(v.replace(/,/g, ""));
  return Number.isFinite(n) && n > 0 ? n : null;
}

function formatPctLabel(pct: number): string {
  const sign = pct >= 0 ? "+" : "−";
  return `${sign}${Math.abs(pct).toFixed(1)}%`;
}

function SpotRangeHandle({
  variant,
  pctPos,
  committedAbs,
  spot,
  isEditing,
  draft,
  onDraftChange,
  onStartEdit,
  onCommit,
  onCancel,
}: {
  variant: "lower" | "upper";
  pctPos: number;
  committedAbs: number;
  spot: number;
  isEditing: boolean;
  draft: string;
  onDraftChange: (v: string) => void;
  onStartEdit: () => void;
  onCommit: () => void;
  onCancel: () => void;
}) {
  const z = variant === "upper" ? "z-[45]" : "z-[40]";
  const pctLabel = formatPctLabel(pctFromAbs(spot, committedAbs));
  const displayValue = isEditing
    ? draft
    : committedAbs.toLocaleString("en-IN", { maximumFractionDigits: 0 });

  return (
    <div
      className={`pointer-events-none absolute top-1/2 ${z} -translate-x-1/2 -translate-y-1/2`}
      style={{
        left: `clamp(1.25rem, ${pctPos}%, calc(100% - 1.25rem))`,
      }}
    >
      <div className="pointer-events-auto flex flex-col items-center gap-1">
        <div className="flex h-8 min-w-[3rem] items-center justify-center rounded-full border-[3px] border-blue-600 bg-white px-1.5 shadow-sm shadow-blue-500/20 ring-2 ring-blue-500/30 transition-[box-shadow] duration-150 ease-out focus-within:ring-4 focus-within:ring-blue-500/35 dark:border-blue-500 dark:bg-zinc-900 dark:shadow-blue-500/15 dark:ring-blue-400/25 dark:focus-within:ring-blue-400/40">
          <input
            type="text"
            inputMode="numeric"
            aria-label={
              variant === "lower" ? "Lower strike bound" : "Upper strike bound"
            }
            value={displayValue}
            onFocus={onStartEdit}
            onChange={(e) => onDraftChange(e.target.value)}
            onBlur={onCommit}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                onCommit();
              } else if (e.key === "Escape") {
                e.preventDefault();
                onCancel();
                (e.target as HTMLInputElement).blur();
              }
            }}
            className="w-full min-w-0 border-0 bg-transparent p-0 text-center text-[10px] font-bold tabular-nums leading-none text-zinc-900 outline-none dark:text-zinc-50"
          />
        </div>
        <span className="text-[10px] font-medium tabular-nums text-zinc-500 dark:text-zinc-400">
          {pctLabel}
        </span>
      </div>
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
  rangeLower: string;
  rangeUpper: string;
  onRangeLowerChange: (v: string) => void;
  onRangeUpperChange: (v: string) => void;
}) {
  const [editingLower, setEditingLower] = useState(false);
  const [draftLower, setDraftLower] = useState("");
  const [editingUpper, setEditingUpper] = useState(false);
  const [draftUpper, setDraftUpper] = useState("");

  const absMin = useMemo(() => absFromPct(spot, PCT_MIN), [spot]);
  const absMax = useMemo(() => absFromPct(spot, PCT_MAX), [spot]);

  const committedLower = parsePositiveNum(rangeLower) ?? absFromPct(spot, -10);
  const committedUpper = parsePositiveNum(rangeUpper) ?? absFromPct(spot, 10);

  const lowerPct = useMemo(() => {
    const p = clampPct(pctFromAbs(spot, committedLower));
    const upperP = clampPct(pctFromAbs(spot, committedUpper));
    return Math.min(p, upperP - 1);
  }, [spot, committedLower, committedUpper]);

  const upperPct = useMemo(() => {
    const p = clampPct(pctFromAbs(spot, committedUpper));
    const lowerP = clampPct(pctFromAbs(spot, committedLower));
    return Math.max(p, lowerP + 1);
  }, [spot, committedLower, committedUpper]);

  const minPctPos = ((lowerPct - PCT_MIN) / SPAN) * 100;
  const maxPctPos = ((upperPct - PCT_MIN) / SPAN) * 100;
  const spotPctPos = ((0 - PCT_MIN) / SPAN) * 100;

  const thumbInteractiveCls =
    "[&::-webkit-slider-thumb]:pointer-events-auto [&::-moz-range-thumb]:pointer-events-auto";

  const commitLower = useCallback(() => {
    const n = parseFloat(draftLower.replace(/,/g, ""));
    if (Number.isFinite(n) && n > 0) {
      const clamped = Math.min(
        Math.max(Math.round(n), absMin),
        committedUpper - 1,
      );
      onRangeLowerChange(String(clamped));
    }
    setEditingLower(false);
  }, [draftLower, absMin, committedUpper, onRangeLowerChange]);

  const commitUpper = useCallback(() => {
    const n = parseFloat(draftUpper.replace(/,/g, ""));
    if (Number.isFinite(n) && n > 0) {
      const clamped = Math.max(
        Math.min(Math.round(n), absMax),
        committedLower + 1,
      );
      onRangeUpperChange(String(clamped));
    }
    setEditingUpper(false);
  }, [draftUpper, absMax, committedLower, onRangeUpperChange]);

  const applyLowerPct = (pct: number) => {
    if (editingLower) setEditingLower(false);
    const clamped = Math.min(pct, upperPct - 1);
    onRangeLowerChange(String(absFromPct(spot, clamped)));
  };

  const applyUpperPct = (pct: number) => {
    if (editingUpper) setEditingUpper(false);
    const clamped = Math.max(pct, lowerPct + 1);
    onRangeUpperChange(String(absFromPct(spot, clamped)));
  };

  return (
    <div className="min-w-0 space-y-3 sm:col-span-2 lg:col-span-3">
      <span className={sb.fieldLabel}>Strike range (from spot price)</span>
      <div className="relative flex h-14 w-full shrink-0 items-center">
        <div className="relative h-10 w-full overflow-visible">
          <div className="pointer-events-none absolute top-1/2 h-2 w-full -translate-y-1/2 rounded-full bg-zinc-200/80 dark:bg-zinc-700/60" />
          <div
            className="pointer-events-none absolute top-1/2 h-2 -translate-y-1/2 rounded-full bg-linear-to-r from-blue-600 to-blue-500 shadow-sm shadow-blue-500/25 dark:from-blue-500 dark:to-blue-400 dark:shadow-blue-500/20"
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
            className={`sb-range-slim sb-range-otm absolute inset-0 z-20 w-full min-w-0 bg-transparent ${editingLower ? "pointer-events-none" : `pointer-events-none ${thumbInteractiveCls}`}`}
            aria-label="Range lower bound"
            aria-valuetext={`${committedLower}`}
          />
          <input
            type="range"
            min={PCT_MIN}
            max={PCT_MAX}
            step={1}
            value={upperPct}
            onChange={(e) => applyUpperPct(Number(e.target.value))}
            className={`sb-range-slim sb-range-otm absolute inset-0 z-30 w-full min-w-0 bg-transparent ${editingUpper ? "pointer-events-none" : `pointer-events-none ${thumbInteractiveCls}`}`}
            aria-label="Range upper bound"
            aria-valuetext={`${committedUpper}`}
          />
          <SpotRangeHandle
            variant="lower"
            pctPos={minPctPos}
            committedAbs={committedLower}
            spot={spot}
            isEditing={editingLower}
            draft={draftLower}
            onDraftChange={setDraftLower}
            onStartEdit={() => {
              setEditingLower(true);
              setDraftLower(String(committedLower));
            }}
            onCommit={commitLower}
            onCancel={() => setEditingLower(false)}
          />
          <SpotRangeHandle
            variant="upper"
            pctPos={maxPctPos}
            committedAbs={committedUpper}
            spot={spot}
            isEditing={editingUpper}
            draft={draftUpper}
            onDraftChange={setDraftUpper}
            onStartEdit={() => {
              setEditingUpper(true);
              setDraftUpper(String(committedUpper));
            }}
            onCommit={commitUpper}
            onCancel={() => setEditingUpper(false)}
          />
        </div>
      </div>
      <div className="flex justify-between gap-4 text-[10px] font-medium tabular-nums text-zinc-400 dark:text-zinc-500">
        <span>−20%</span>
        <span className="rounded-full bg-zinc-100/80 px-2 py-0.5 text-zinc-600 backdrop-blur-sm dark:bg-zinc-800/60 dark:text-zinc-300">
          Spot {spot.toLocaleString("en-IN", { maximumFractionDigits: 2 })}
        </span>
        <span>+20%</span>
      </div>
    </div>
  );
}
