"use client";

import { sb } from "@/lib/strategy-builder/ui";
import type { QuoteMeta } from "@/lib/strategy-builder/types";

export type ChainBuildPhase = "preparing" | "live" | "bhavcopy" | "refreshing";

function messageForPhase(phase: ChainBuildPhase): string {
  switch (phase) {
    case "live":
      return "Subscribing to live quotes and building option chain…";
    case "bhavcopy":
      return "Loading option chain from Bhavcopy (session close prices)…";
    case "refreshing":
      return "Refreshing live prices…";
    default:
      return "Building option chain…";
  }
}

export function inferChainBuildPhase(args: {
  quoteMeta?: QuoteMeta | null;
  isInitialLoad: boolean;
  marketLikelyOpen?: boolean;
}): ChainBuildPhase {
  if (!args.isInitialLoad && args.quoteMeta?.quote_source === "websocket") {
    return "refreshing";
  }
  if (args.quoteMeta?.quote_source === "bhavcopy") {
    return "bhavcopy";
  }
  if (args.marketLikelyOpen !== false) {
    return "live";
  }
  return "bhavcopy";
}

export function ChainBuildStatus({
  visible,
  phase,
  elapsedMs,
}: {
  visible: boolean;
  phase: ChainBuildPhase;
  elapsedMs?: number;
}) {
  if (!visible) return null;

  const message = messageForPhase(phase);
  const slowHint =
    elapsedMs != null && elapsedMs >= 3000
      ? " Large chains can take a few seconds."
      : "";

  return (
    <div
      className={`${sb.section} space-y-2 border-sky-500/20 bg-sky-500/5 py-3`}
      role="status"
      aria-live="polite"
      aria-busy="true"
    >
      <p className="text-sm font-medium text-zinc-800 dark:text-zinc-200">
        {message}
        {slowHint}
      </p>
      <div
        className="h-1.5 overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-800"
        aria-hidden
      >
        <div className="h-full w-1/3 animate-pulse rounded-full bg-sky-600 dark:bg-sky-500" />
      </div>
    </div>
  );
}
