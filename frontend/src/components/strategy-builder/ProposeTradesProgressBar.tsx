"use client";

import { sb } from "@/lib/strategy-builder/ui";

export function ProposeTradesProgressBar({
  message,
  progressPct,
  progressCurrent,
  progressTotal,
}: {
  message: string;
  progressPct: number;
  progressCurrent: number;
  progressTotal: number;
}) {
  const pct = Math.min(100, Math.max(0, progressPct));

  return (
    <div
      className={`${sb.section} mt-4 space-y-2`}
      role="status"
      aria-live="polite"
      aria-busy="true"
    >
      <div className="flex items-center justify-between gap-3 text-xs">
        <p className="font-medium text-zinc-800 dark:text-zinc-200">{message}</p>
        <p className="shrink-0 tabular-nums text-zinc-500 dark:text-zinc-400">
          {progressCurrent}/{progressTotal} · {pct}%
        </p>
      </div>
      <div
        className="h-2 overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-800"
        aria-hidden
      >
        <div
          className="h-full rounded-full bg-sky-600 transition-[width] duration-300 ease-out dark:bg-sky-500"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}
