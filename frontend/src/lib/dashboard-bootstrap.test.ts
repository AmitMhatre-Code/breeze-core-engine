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
      vix_options: {
        nifty_spot: 24000,
        next_expiry: "16-Jun-2026",
        atm_iv: 12,
        expected_range: [23000, 25000],
        expected_move_pct: 2,
        put_call_ratio: 1,
        strike_highest_call_oi: 24100,
        strike_highest_put_oi: 23900,
      },
    };

    hydrateDashboardQueryCache(queryClient, bootstrap);

    expect(queryClient.getQueryData(["home", "data"])).toEqual(bootstrap.home);
    expect(queryClient.getQueryData(["portfolio", "positions"])).toEqual(
      bootstrap.portfolio,
    );
    expect(queryClient.getQueryData(["dashboard", "vix"])).toEqual(bootstrap.vix);
    expect(queryClient.getQueryData(["dashboard", "vix-options"])).toEqual(
      bootstrap.vix_options,
    );
  });
});
