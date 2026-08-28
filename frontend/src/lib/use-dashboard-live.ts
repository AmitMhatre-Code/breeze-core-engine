"use client";

import { useQuery } from "@tanstack/react-query";
import { fetchDashboardLive } from "@/lib/dashboard-live";
import { fetchMarketStatus } from "@/lib/market-status";
import { usePnlRecomputeRefetchMs } from "@/lib/portfolio/usePnlRecomputeRefetchMs";

/**
 * Polls `/dashboard/live` at the user's P&L recompute cadence (Settings >
 * Advanced > P&L Engine, default 2s) — the same clock the backend reprices on —
 * so the Open P&L and Day's P&L tiles follow the WS feeds. Each poll is a
 * process-state read on the backend, never an ICICI call.
 *
 * Polling stops when the market is closed (the underlying values can't move);
 * the query still runs once on mount to pick up the frozen post-close figure.
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
    refetchInterval: enabled && marketOpen ? intervalMs : false,
    staleTime: 0,
  });
}
