/** Matches GET /dashboard/bootstrap orchestrated payload. */

import { apiClient } from "@/lib/api-client";
import type { HomeDataResponse } from "@/lib/home-data";

export type Vix30Point = { date: string; value: number };

export type DashboardVixCore = {
  current_vix: number | null;
  nifty_spot: number | null;
  vix_trend_pct: number | null;
  nifty_spot_trend_pct: number | null;
  vix_30d: Vix30Point[];
  error?: string | null;
};

export type DashboardVixOptions = {
  nifty_spot: number | null;
  next_expiry: string | null;
  atm_iv: number | null;
  expected_range: [number, number] | null;
  expected_move_pct: number | null;
  put_call_ratio: number | null;
  strike_highest_call_oi: number | null;
  strike_highest_put_oi: number | null;
  error?: string | null;
};

export type PortfolioPositionRow = {
  current_profit?: number | null;
  pnl?: number | null;
  span_margin_required?: number | string | null;
};

export type PortfolioApiResponse = {
  Status: number;
  Error?: string;
  Success?: {
    positions?: PortfolioPositionRow[];
  };
};

export type DashboardBootstrapResponse = {
  home: HomeDataResponse;
  portfolio: PortfolioApiResponse;
  vix: DashboardVixCore;
};

export type DashboardVixHistoryResponse = {
  vix_30d: Vix30Point[];
};

export function fetchDashboardBootstrap(): Promise<DashboardBootstrapResponse> {
  return apiClient.get<DashboardBootstrapResponse>("/dashboard/bootstrap");
}

export function fetchDashboardVixOptions(): Promise<DashboardVixOptions> {
  return apiClient.get<DashboardVixOptions>("/dashboard/vix/options");
}

export function fetchDashboardVixHistory(): Promise<DashboardVixHistoryResponse> {
  return apiClient.get<DashboardVixHistoryResponse>("/dashboard/vix/history");
}

export function hydrateDashboardQueryCache(
  queryClient: {
    setQueryData: (key: readonly unknown[], data: unknown) => void;
  },
  data: DashboardBootstrapResponse,
): void {
  queryClient.setQueryData(["home", "data"], data.home);
  queryClient.setQueryData(["portfolio", "positions"], data.portfolio);
  queryClient.setQueryData(["dashboard", "vix"], data.vix);
}
