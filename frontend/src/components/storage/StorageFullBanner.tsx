"use client";

import Link from "next/link";

import { useStorageStatus } from "@/lib/settings/storage";

/**
 * Persistent (non-dismissable) banner while the data volume is at or past its threshold
 * (Settings -> Storage, docs/design-decisions.md #44). Sits under the license banner rather than
 * replacing it: both are deployment-wide, and a lapsed licence and a full disk are independent.
 * Amber, like the licence *warning* states: the app still trades; backtests that write data wait.
 */
export function StorageFullBanner() {
  const { data } = useStorageStatus();
  if (!data?.over_threshold || data.used_pct == null) return null;

  return (
    <div
      role="status"
      className="border-b border-amber-500/60 bg-amber-500/10 py-2 text-center text-sm text-amber-950 ps-[max(1rem,env(safe-area-inset-left))] pe-[max(1rem,env(safe-area-inset-right))] dark:text-amber-100"
    >
      <div className="flex flex-wrap items-center justify-center gap-x-2 gap-y-1">
        <span>
          Storage is {Math.round(data.used_pct)}% full (threshold {data.threshold_pct}%). Free up space
          to keep the app running smoothly; backtests that download or write data are paused until then.
        </span>
        <Link
          href="/settings?tab=storage"
          className="font-medium underline underline-offset-2 text-amber-900 hover:text-amber-950 dark:text-amber-50 dark:hover:text-white"
        >
          Free up space
        </Link>
      </div>
    </div>
  );
}
