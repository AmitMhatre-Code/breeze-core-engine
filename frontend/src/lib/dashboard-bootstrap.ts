/** Matches GET /dashboard/bootstrap orchestrated payload. */

import { apiClient } from "@/lib/api-client";
import type { HomeDataResponse } from "@/lib/home-data";
import type { NettedMargin } from "@/lib/portfolio/groupPositions";

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
    /** Whole-portfolio netted SPAN + ELM (per-underlying netted, summed). */
    portfolio?: NettedMargin;
  };
};

/** Unrealized MTM since previous close (v1: no realized component). See GET /dashboard/bootstrap. */
export type DaysPnlResponse = {
  unrealized_day_pnl: number | null;
  realized_day_pnl: number | null;
  total_day_pnl: number | null;
  as_of: string;
  market_session_state: "open" | "post_close" | "pre_open" | "closed_non_trading_day";
  positions_priced: number;
  positions_missing_reference: number;
  error?: string | null;
};

export type RecentScripEntry = { stock_code: string; trade_count: number };

export type DashboardBootstrapResponse = {
  home: HomeDataResponse;
  portfolio: PortfolioApiResponse;
  vix: DashboardVixCore;
  days_pnl: DaysPnlResponse;
  recent_scrips: RecentScripEntry[];
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
  queryClient.setQueryData(["dashboard", "days-pnl"], data.days_pnl);
  queryClient.setQueryData(["scrips", "recent"], data.recent_scrips ?? []);
}
