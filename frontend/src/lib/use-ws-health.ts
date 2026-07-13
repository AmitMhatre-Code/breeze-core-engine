"use client";

import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@/lib/api-client";

export type WsHealthScripDetail = {
  exchange_code: string;
  stock_code: string;
  expiry_display: string | null;
  subscribed_tokens: number;
  last_tick_age_seconds: number | null;
  stale: boolean;
  warming_up: boolean;
};

export type WsHealthResponse = {
  status: "green" | "red" | "gray";
  reason: string;
  poll_interval_seconds: number;
  market_open: boolean;
  prefetch_done: boolean;
  detail: Record<string, WsHealthScripDetail>;
};

const DEFAULT_POLL_MS = 2000;

/** Combined NIFTY+SENSEX system-chain WS health for the navbar status dot.
 * Polls on the same cadence as the backend's WS flush interval (returned in
 * the payload as `poll_interval_seconds`, live-adjustable in Settings ->
 * Advanced) so the dot never lags further behind reality than the data itself. */
export function useWsHealth() {
  return useQuery({
    queryKey: ["dashboard", "ws-health"],
    queryFn: () => apiClient.get<WsHealthResponse>("/dashboard/ws-health"),
    refetchInterval: (query) => {
      const seconds = query.state.data?.poll_interval_seconds;
      return seconds && seconds > 0 ? seconds * 1000 : DEFAULT_POLL_MS;
    },
    staleTime: 0,
  });
}
