/** Matches GET /dashboard/live — WS-fed live values for the Dashboard tiles.
 *
 * Open P&L comes from the portfolio P&L engine's ~2s repricing off the scrip WS
 * feed; Day's P&L from `dashboard_day_pnl_live` (last REST snapshot + order WS
 * feed + scrip WS feed). Both are pure process-state reads — no ICICI call per
 * poll. Any field is `null` until warm; callers fall back to their REST snapshot.
 */

import { apiClient } from "@/lib/api-client";
import type { DayPnlResponse } from "@/lib/dashboard-day-pnl";

export type DashboardLiveOpenPnl = {
  total_pnl: number;
  leg_count: number;
  stream_stale: boolean;
  computed_at: number | null;
};

export type DashboardLiveResponse = {
  open_pnl: DashboardLiveOpenPnl | null;
  day_pnl: DayPnlResponse | null;
  tick_stale: boolean;
};

export function fetchDashboardLive(): Promise<DashboardLiveResponse> {
  return apiClient.get<DashboardLiveResponse>("/dashboard/live");
}
