import { describe, expect, it } from "vitest";
import { QueryClient } from "@tanstack/react-query";
import {
  hydrateDashboardQueryCache,
  type DashboardBootstrapResponse,
} from "@/lib/dashboard-bootstrap";

describe("hydrateDashboardQueryCache", () => {
  it("populates react-query keys used by dashboard and AppShell", () => {
    const queryClient = new QueryClient();
    const bootstrap: DashboardBootstrapResponse = {
      home: {
        customer: { Status: 200, Success: { idirect_user_name: "Test" } },
        margin: { Status: 200, Success: { actual_margin_avl: 1000 } },
      },
      portfolio: { Status: 200, Success: { positions: [] } },
      vix: {
        current_vix: 14,
        nifty_spot: 24000,
        vix_trend_pct: 0,
        nifty_spot_trend_pct: 1,
        vix_30d: [],
      },
      days_pnl: {
        unrealized_day_pnl: 0,
        realized_day_pnl: 0,
        total_day_pnl: 0,
        as_of: "2026-01-01T00:00:00+05:30",
        market_session_state: "closed_non_trading_day",
        positions_priced: 0,
        positions_missing_reference: 0,
      },
    };

    hydrateDashboardQueryCache(queryClient, bootstrap);

    expect(queryClient.getQueryData(["home", "data"])).toEqual(bootstrap.home);
    expect(queryClient.getQueryData(["portfolio", "positions"])).toEqual(
      bootstrap.portfolio,
    );
    expect(queryClient.getQueryData(["dashboard", "vix"])).toEqual(bootstrap.vix);
    expect(queryClient.getQueryData(["dashboard", "days-pnl"])).toEqual(bootstrap.days_pnl);
    expect(queryClient.getQueryData(["dashboard", "vix-options"])).toBeUndefined();
  });
});
