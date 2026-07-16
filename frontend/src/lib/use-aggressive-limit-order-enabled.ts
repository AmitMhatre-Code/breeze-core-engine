"use client";

import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@/lib/api-client";
import type { HomeDataResponse } from "@/lib/home-data";

/**
 * False while ICICI has no native aggressive-limit order support (backend
 * AGGRESSIVE_LIMIT_ORDER_ENABLED). Reads the same ["home","data"] cache AppShell
 * already populates on load, so this never triggers an extra request.
 */
export function useAggressiveLimitOrderEnabled(): boolean {
  const homeQ = useQuery({
    queryKey: ["home", "data"],
    queryFn: () => apiClient.get<HomeDataResponse>("/home/data"),
  });
  return homeQ.data?.aggressive_limit_order_enabled ?? false;
}
