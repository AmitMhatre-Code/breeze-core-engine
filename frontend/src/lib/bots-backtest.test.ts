import { describe, expect, it } from "vitest";
import {
  describePhase,
  equityCurve,
  exitLabel,
  formatDuration,
  quietVerdict,
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

  describe("running-job progress", () => {
    it("names the setting and the session being replayed", () => {
      expect(describePhase({ phase: "replaying", step: 2, steps: 12, day: "2026-03-09" })).toBe(
        "Replaying setting 2 of 12 · session 2026-03-09",
      );
      expect(describePhase({ phase: "replaying", step: 1, steps: 1, day: null })).toBe(
        "Replaying · loading prices and signal readings",
      );
      expect(describePhase({ phase: undefined })).toBe("Starting");
    });

    it("formats durations the way a person reads them", () => {
      expect(formatDuration(42)).toBe("42s");
      expect(formatDuration(75)).toBe("1m 15s");
      expect(formatDuration(3_700)).toBe("1h 1m");
    });

    it("says nothing about a short silence, and blames the queue only while calling ICICI", () => {
      expect(quietVerdict(179, "replaying")).toBeNull();
      expect(quietVerdict(200, "fetching")).toMatch(/rate-limit cooldowns/);
      expect(quietVerdict(200, "replaying")).toMatch(/still alive/);
    });
  });
});
