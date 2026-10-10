"use client";

import {
  formatShare,
  formatTimeLeft,
  progressCount,
  progressShare,
  type BacktestJobProgress,
} from "@/lib/bots-backtest";

/**
 * A running backtest's progress bar, shared by the card's dialog, its Activity row and the
 * Signals page.
 *
 * It fills only for work whose size the job knows -- sessions replayed across every setting,
 * or a batch of windows being fetched -- and pulses otherwise (sizing, loading prices, a fetch
 * whose size is not known up front). A bar that filled on a guess would be the one number on
 * the panel that could not be trusted.
 *
 * `sinceSeconds` is how long ago the job was polled, so the time left counts down between polls.
 */
export function BacktestProgressBar({
  job,
  sinceSeconds = 0,
}: {
  job: BacktestJobProgress;
  sinceSeconds?: number;
}) {
  const share = progressShare(job);
  const count = progressCount(job);
  const eta = job.eta_seconds != null ? formatTimeLeft(job.eta_seconds - sinceSeconds) : null;
  const trailing = [share !== null ? formatShare(share) : null, eta].filter(Boolean).join(" · ");
  const label = [count, trailing].filter(Boolean).join(", ") || "Working";

  return (
    <div className="space-y-1">
      {count || trailing ? (
        <div className="flex flex-wrap items-baseline justify-between gap-x-3 font-mono text-[11px] tabular-nums">
          <span className="text-muted">{count}</span>
          <span className="text-foreground">{trailing}</span>
        </div>
      ) : null}
      <div
        className="relative h-1.5 overflow-hidden rounded-full bg-track"
        role="progressbar"
        aria-label="Backtest progress"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={share !== null ? Math.floor(share * 100) : undefined}
        aria-valuetext={label}
      >
        {share !== null ? (
          <div
            className="h-full rounded-full bg-accent-strong transition-[width] duration-500"
            style={{ width: `${Math.max(share * 100, share > 0 ? 1 : 0)}%` }}
          />
        ) : (
          <div className="h-full w-1/3 rounded-full bg-accent-strong motion-safe:animate-pulse" />
        )}
      </div>
    </div>
  );
}
