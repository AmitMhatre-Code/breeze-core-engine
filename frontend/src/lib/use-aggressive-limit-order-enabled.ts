"use client";

import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@/lib/api-client";
import type { HomeDataResponse } from "@/lib/home-data";

/** Deployment-wide aggressive-order config, surfaced via /home/data. */
export type AggressiveOrderConfig = {
  /** Master gate (backend AGGRESSIVE_LIMIT_ORDER_ENABLED) — shows the ⚡ controls. */
  enabled: boolean;
  /** Seeds a new user's tolerance% for limit_tolerance mode. */
  defaultTolerancePct: number;
  /** Hard cap on tolerance% (server-clamped regardless of client input). */
  maxTolerancePct: number;
};

export function useAggressiveOrderConfig(): AggressiveOrderConfig {
  const homeQ = useQuery({
    queryKey: ["home", "data"],
    queryFn: () => apiClient.get<HomeDataResponse>("/home/data"),
  });
  return {
    enabled: homeQ.data?.aggressive_limit_order_enabled ?? false,
    defaultTolerancePct: homeQ.data?.aggressive_limit_default_tolerance_pct ?? 5,
    maxTolerancePct: homeQ.data?.aggressive_limit_max_tolerance_pct ?? 25,
  };
}

/**
 * False while the aggressive-order feature is off (backend AGGRESSIVE_LIMIT_ORDER_ENABLED).
 * Reads the same ["home","data"] cache AppShell already populates on load.
 */
export function useAggressiveLimitOrderEnabled(): boolean {
  return useAggressiveOrderConfig().enabled;
}
