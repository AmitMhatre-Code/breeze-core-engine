/** Matches GET /dashboard/day-pnl (lazy). Mark-to-market Day's P&L from positions +
 * today's trade book: total = realized (intraday round-trips / squareoffs) + unrealized
 * (open MTM since previous close / entry). Gross of brokerage & taxes. */

import { apiClient } from "@/lib/api-client";

export type DayPnlResponse = {
  total_day_pnl: number | null;
  realized_day_pnl: number | null;
  unrealized_day_pnl: number | null;
  is_gross: boolean;
  as_of: string;
  market_session_state: "open" | "post_close" | "pre_open" | "closed_non_trading_day";
  contracts_priced: number;
  contracts_missing_prev_close: number;
  trades_source_ok: boolean;
  degraded: boolean;
  error?: string | null;
};

export function fetchDashboardDayPnl(): Promise<DayPnlResponse> {
  return apiClient.get<DayPnlResponse>("/dashboard/day-pnl");
}
