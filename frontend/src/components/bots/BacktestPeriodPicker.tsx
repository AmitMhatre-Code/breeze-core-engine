"use client";

import { DatePicker } from "@/components/ui/DatePicker";
import { BACKTEST_PERIODS, type BacktestPeriod } from "@/lib/bots-backtest";

/** A backtest dialog's one question (#36): the period, with dates only for a custom range. */
export function BacktestPeriodPicker({
  period,
  onPeriod,
  from,
  onFrom,
  to,
  onTo,
  disabled,
}: {
  period: BacktestPeriod;
  onPeriod: (p: BacktestPeriod) => void;
  from: string;
  onFrom: (v: string) => void;
  to: string;
  onTo: (v: string) => void;
  disabled?: boolean;
}) {
  return (
    <fieldset className="mt-4 space-y-1.5" disabled={disabled}>
      <legend className="text-xs text-muted">Period</legend>
      <div className="grid grid-cols-2 gap-1.5">
        {BACKTEST_PERIODS.map((p) => (
          <label
            key={p.value}
            className={[
              "flex cursor-pointer items-center gap-2 rounded-lg border px-3 py-2 text-xs transition",
              period === p.value ? "border-accent text-accent" : "border-border text-foreground hover:border-accent/60",
            ].join(" ")}
          >
            <input
              type="radio"
              name="backtest-period"
              value={p.value}
              checked={period === p.value}
              onChange={() => onPeriod(p.value)}
              className="accent-[var(--accent)]"
            />
            {p.label}
          </label>
        ))}
      </div>
      {period === "custom" ? (
        <div className="flex flex-wrap gap-3 pt-1">
          <label className="space-y-1 text-xs text-muted">
            <span>From</span>
            <DatePicker value={from} onChange={onFrom} />
          </label>
          <label className="space-y-1 text-xs text-muted">
            <span>To</span>
            <DatePicker value={to} onChange={onTo} />
          </label>
        </div>
      ) : null}
    </fieldset>
  );
}
