"use client";

import { sb } from "@/lib/strategy-builder/ui";
import type { AggressiveOrderMode } from "@/lib/aggressive-order";
import { useAggressiveOrderControls } from "@/lib/use-aggressive-order-controls";

/**
 * Page-level aggressive execution-style selector for multi-leg forms (Strategy Builder, Basket).
 * The per-leg ⚡ toggles decide *which* legs are aggressive; this decides *how* those legs execute
 * (native market vs LTP-derived limit) and the tolerance. Only shown when the feature is enabled
 * and at least one leg is marked aggressive.
 */
export function AggressiveModeControl({
  controls,
  visible,
  className = "",
}: {
  controls: ReturnType<typeof useAggressiveOrderControls>;
  visible: boolean;
  className?: string;
}) {
  const { enabled, mode, tolerancePct, maxTolerancePct, setMode, setTolerancePct } =
    controls;
  if (!enabled || !visible) return null;

  return (
    <div
      className={`flex flex-wrap items-center gap-2 text-xs text-muted ${className}`}
    >
      <span className="font-medium text-foreground">Aggressive style:</span>
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
            onClick={() => setMode(m)}
            className={`px-2.5 py-1 font-medium transition ${
              mode === m
                ? "bg-amber-tint text-amber-on-tint"
                : "text-muted hover:bg-border-soft"
            }`}
          >
            {label}
          </button>
        ))}
      </div>
      {mode === "limit_tolerance" ? (
        <label className="inline-flex items-center gap-1.5">
          <span>Tolerance</span>
          <input
            type="number"
            min={0}
            max={maxTolerancePct}
            step={0.5}
            aria-label="Aggressive limit tolerance percent"
            className={`${sb.input} h-8 w-16 tabular-nums`}
            value={String(tolerancePct)}
            onChange={(e) => {
              const v = parseFloat(e.target.value);
              setTolerancePct(
                Number.isFinite(v)
                  ? Math.max(0, Math.min(maxTolerancePct, v))
                  : 0,
              );
            }}
          />
          <span>%</span>
        </label>
      ) : (
        <span>Sent as market orders (may be rejected until ICICI enables it).</span>
      )}
    </div>
  );
}
