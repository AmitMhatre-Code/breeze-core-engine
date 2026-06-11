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

function BoundValueInput({
  variant,
  pctPos,
  committedAbs,
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
  isEditing: boolean;
  draft: string;
  onDraftChange: (v: string) => void;
  onStartEdit: () => void;
  onCommit: () => void;
  onCancel: () => void;
}) {
  const z = variant === "upper" ? "z-[45]" : "z-[40]";
  const displayValue = isEditing
    ? draft
    : committedAbs.toLocaleString("en-IN", { maximumFractionDigits: 2 });

  return (
    <div
      className={`pointer-events-none absolute bottom-full mb-2 ${z} -translate-x-1/2`}
      style={{
        left: `clamp(1.25rem, ${pctPos}%, calc(100% - 1.25rem))`,
      }}
    >
      <div className="pointer-events-auto">
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
          className="w-[5.5rem] border-0 border-b-2 border-blue-600 bg-transparent p-0 text-center text-xs font-semibold tabular-nums text-zinc-900 outline-none dark:border-blue-500 dark:text-zinc-50"
        />
      </div>
    </div>
  );
}

function PctLabel({
  variant,
  pctPos,
  label,
}: {
  variant: "lower" | "upper" | "spot";
  pctPos: number;
  label: string;
}) {
  const z =
    variant === "upper" ? "z-[45]" : variant === "lower" ? "z-[40]" : "z-[35]";
  return (
    <div
      className={`pointer-events-none absolute top-full mt-2 ${z} -translate-x-1/2 text-[10px] font-medium tabular-nums text-zinc-500 dark:text-zinc-400`}
      style={{
        left: `clamp(1.25rem, ${pctPos}%, calc(100% - 1.25rem))`,
      }}
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

  const lowerPctLabel = formatPctLabel(pctFromAbs(spot, committedLower));
  const upperPctLabel = formatPctLabel(pctFromAbs(spot, committedUpper));
  const spotFormatted = spot.toLocaleString("en-IN", {
    maximumFractionDigits: 2,
  });

  return (
    <div className="min-w-0 space-y-3 sm:col-span-2 lg:col-span-3">
      <span className={sb.fieldLabel}>Strike range (from spot price)</span>
      <div className="relative pt-8 pb-8">
        <div className="relative h-10 w-full">
          <BoundValueInput
            variant="lower"
            pctPos={minPctPos}
            committedAbs={committedLower}
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
          <div
            className="pointer-events-none absolute bottom-full mb-2 z-[35] -translate-x-1/2 text-xs font-semibold tabular-nums text-zinc-700 dark:text-zinc-300"
            style={{ left: `${spotPctPos}%` }}
          >
            {spotFormatted}
          </div>
          <BoundValueInput
            variant="upper"
            pctPos={maxPctPos}
            committedAbs={committedUpper}
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

          <div className="absolute inset-x-0 top-1/2 -translate-y-1/2">
            <div className="pointer-events-none absolute top-1/2 h-1 w-full -translate-y-1/2 rounded-full bg-zinc-200 dark:bg-zinc-700/85" />
            <div
              className="pointer-events-none absolute top-1/2 h-1.5 -translate-y-1/2 rounded-full bg-blue-600 dark:bg-blue-500"
              style={{
                left: `${minPctPos}%`,
                width: `${Math.max(0, maxPctPos - minPctPos)}%`,
              }}
            />
            <div
              className="pointer-events-none absolute top-1/2 z-[35] size-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-zinc-400 ring-2 ring-white dark:bg-zinc-500 dark:ring-zinc-900"
              style={{ left: `${spotPctPos}%` }}
              aria-hidden
            />
            <input
              type="range"
              min={PCT_MIN}
              max={PCT_MAX}
              step={1}
              value={lowerPct}
              onChange={(e) => applyLowerPct(Number(e.target.value))}
              className={`sb-range-slim absolute inset-0 z-20 w-full min-w-0 bg-transparent ${editingLower ? "pointer-events-none" : `pointer-events-none ${thumbInteractiveCls}`}`}
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
              className={`sb-range-slim absolute inset-0 z-30 w-full min-w-0 bg-transparent ${editingUpper ? "pointer-events-none" : `pointer-events-none ${thumbInteractiveCls}`}`}
              aria-label="Range upper bound"
              aria-valuetext={`${committedUpper}`}
            />
          </div>

          <PctLabel variant="lower" pctPos={minPctPos} label={lowerPctLabel} />
          <PctLabel variant="spot" pctPos={spotPctPos} label="Spot" />
          <PctLabel variant="upper" pctPos={maxPctPos} label={upperPctLabel} />
        </div>
      </div>
    </div>
  );
}
