"use client";

import { useQuery } from "@tanstack/react-query";
import { fetchDashboardLive } from "@/lib/dashboard-live";
import { fetchMarketStatus } from "@/lib/market-status";
import { usePnlRecomputeRefetchMs } from "@/lib/portfolio/usePnlRecomputeRefetchMs";

/** Slow heartbeat once the calendar says the session is over. Keeps watching for
 * ticks that are still arriving, without polling at the live cadence for nothing.
 * Costs no ICICI quota: `/dashboard/live` is a backend process-state read. */
export const CLOSED_HEARTBEAT_MS = 30_000;

/**
 * Polls `/dashboard/live` at the user's P&L recompute cadence (Settings >
 * Advanced > P&L Engine, default 2s) — the same clock the backend reprices on —
 * so the Open P&L and Day's P&L tiles follow the WS feeds. Each poll is a
 * process-state read on the backend, never an ICICI call.
 *
 * The cadence follows the *feed*, not the clock: it stays live while ticks are
 * arriving even if the configured close has passed, and drops to a slow heartbeat
 * only once the calendar says closed AND the ticks have actually stopped. A
 * calendar that is behind the exchange (a session extended past the configured
 * close) therefore costs nothing but the heartbeat's latency, instead of freezing
 * the tiles while prices are still moving.
 */
export function useDashboardLive(enabled: boolean) {
  const intervalMs = usePnlRecomputeRefetchMs();
  const marketStatus = useQuery({
    queryKey: ["settings", "market-status"],
    queryFn: fetchMarketStatus,
    staleTime: 30_000,
    refetchInterval: 30_000,
  });
  const marketOpen = marketStatus.data?.is_open ?? true;

  return useQuery({
    queryKey: ["dashboard", "live"],
    queryFn: fetchDashboardLive,
    enabled,
    refetchInterval: (query) => {
      if (!enabled) return false;
      if (marketOpen) return intervalMs;
      // Ticks still flowing after the configured close -> the session is really
      // still open, so keep repricing at the live cadence.
      const ticking = query.state.data ? query.state.data.tick_stale === false : false;
      return ticking ? intervalMs : CLOSED_HEARTBEAT_MS;
    },
    staleTime: 0,
  });
}
