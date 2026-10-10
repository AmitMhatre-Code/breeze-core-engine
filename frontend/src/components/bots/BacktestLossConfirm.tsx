"use client";

import { useId } from "react";
import { formatIndianMoneyCompact } from "@/lib/format-money-in";
import { lossToType } from "@/lib/backtest-loss";

/** Shown in front of unattended trading when the backtest of these exact settings lost money:
 *  the loss, and a box to type it into (#76). Warns, never refuses. */
export function BacktestLossConfirm({
  netPnl,
  fromDate,
  toDate,
  value,
  onChange,
  disabled,
}: {
  netPnl: number | null | undefined;
  fromDate?: string | null;
  toDate?: string | null;
  value: string;
  onChange: (next: string) => void;
  disabled?: boolean;
}) {
  const id = useId();
  const loss = lossToType(netPnl);
  if (loss === null) return null;
  return (
    <div className="rounded-lg border border-down/30 bg-down-tint p-3 text-hint text-text">
      <p>
        <strong>These exact settings lost {formatIndianMoneyCompact(Math.abs(netPnl ?? 0))}</strong> after costs
        in their backtest{fromDate && toDate ? ` (${fromDate} to ${toDate})` : ""}. Nothing stops you trading them,
        but a bot that lost money on history has shown no reason to make it live.
      </p>
      <label htmlFor={id} className="mt-2 block text-faint">
        To continue, type the loss in rupees ({loss}):
      </label>
      <input
        id={id}
        className="app-input mt-1 w-40 font-mono"
        inputMode="numeric"
        autoComplete="off"
        disabled={disabled}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  );
}
