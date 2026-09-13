import { describe, expect, it } from "vitest";
import {
  equityCurve,
  exitLabel,
  inr,
  maxDrawdown,
  summaryTotals,
  tradeTotals,
  type BacktestTrade,
} from "@/lib/bots-backtest";

const trade = (exited_at: string, net: number): BacktestTrade => ({
  exited_at,
  net_pnl: net,
  gross_pnl: net + 10,
  friction: 10,
});

describe("bots-backtest", () => {
  it("builds the equity curve in exit order, whatever order the trades arrive in", () => {
    const points = equityCurve([trade("2026-03-03 10:00:00", -50), trade("2026-03-02 10:00:00", 100)]);
    expect(points.map((p) => p.cumulative)).toEqual([100, 50]);
  });

  it("dates Bot 2 trades by their expiry day", () => {
    const points = equityCurve([{ day: "2026-03-10", net_pnl: 5, gross_pnl: 6, friction: 1 }]);
    expect(points[0].at).toBe("2026-03-10");
  });

  it("measures drawdown from the running peak", () => {
    const points = equityCurve([
      trade("2026-03-02", 100),
      trade("2026-03-03", -150),
      trade("2026-03-04", 20),
    ]);
    expect(maxDrawdown(points)).toBe(150);
  });

  it("totals every bot's trades the same way", () => {
    const totals = tradeTotals([trade("a", 100), trade("b", -40)]);
    expect(totals).toEqual({ trades: 2, net: 60, gross: 80, friction: 20, winRate: 50 });
  });

  it("reads a scalper summary directly and adds up Bot 2's per-strategy summary", () => {
    expect(summaryTotals({ cycles: 4, net_pnl: 120 })).toEqual({ trades: 4, net: 120 });
    expect(
      summaryTotals({
        by_strategy: { "NIFTY naked_pe": { trades: 2, net_pnl: 30 }, "NIFTY naked_ce": { trades: 1, net_pnl: -10 } },
      }),
    ).toEqual({ trades: 3, net: 20 });
    expect(summaryTotals(null)).toEqual({ trades: null, net: null });
  });

  it("labels exits in plain words and formats rupees with a true minus", () => {
    expect(exitLabel("credit_decay_target")).toBe("Decay target");
    expect(exitLabel("something_new")).toBe("something new");
    expect(inr(-1234.5)).toBe("−₹1,235");
    expect(inr(null)).toBe("—");
  });
});
