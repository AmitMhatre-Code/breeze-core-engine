import { describe, expect, it } from "vitest";
import {
  describePhase,
  formatShare,
  formatTimeLeft,
  progressCount,
  progressShare,
  equityCurve,
  exitLabel,
  formatDuration,
  quietVerdict,
  inr,
  markedGroups,
  maxDrawdown,
  rowMarks,
  summaryTotals,
  tradeTotals,
  type BacktestTrade,
  type ComparedRow,
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

    it("counts the session while replaying, and the phase's own unit otherwise", () => {
      const replay = { phase: "replaying" as const, session: 14, sessions: 21, done: 300, total: 567, unit: "sessions" };
      expect(progressCount(replay)).toBe("Session 14 of 21");
      expect(progressShare(replay)).toBeCloseTo(300 / 567);
      expect(progressCount({ phase: "fetching", done: 1200, total: 1580, unit: "NSE sessions" })).toBe(
        "1,200 of 1,580 NSE sessions",
      );
      expect(describePhase({ phase: "replaying", unit: "series" })).toBe("Replaying every signal series");
      expect(describePhase({ phase: "fetching", unit: "NSE sessions" })).toBe("Downloading NSE daily prices");
      expect(describePhase({ phase: "fetching", unit: "option windows" })).toBe("Fetching missing history from ICICI");
    });

    it("draws no share at all while the size of the work is unknown", () => {
      expect(progressShare({ phase: "sizing" })).toBeNull();
      expect(progressShare({ phase: "fetching", done: 3, total: null })).toBeNull();
      expect(progressCount({ phase: "recording" })).toBeNull();
      expect(formatShare(0.004)).toBe("<1%");
      expect(formatShare(0)).toBe("0%");
      expect(formatShare(0.419)).toBe("41%");
    });

    it("quotes time left coarsely, as the estimate it is", () => {
      expect(formatTimeLeft(45)).toBe("under a minute left");
      expect(formatTimeLeft(200)).toBe("about 4 min left");
      expect(formatTimeLeft(3_600)).toBe("about 1h left");
      expect(formatTimeLeft(5_400)).toBe("about 1h 30m left");
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

  describe("comparison marks", () => {
    const row = (id: string, net: number | null, is_saved = false): ComparedRow => ({
      id,
      is_saved,
      trades: 1,
      win_rate_pct: null,
      net_pnl: net,
      friction: null,
      max_drawdown: null,
    });

    it("marks your settings, the best and the worst by net P&L", () => {
      const marks = rowMarks([row("a", 10, true), row("b", 50), row("c", -30), row("d", 0)]);
      expect(Object.fromEntries(marks)).toEqual({ a: ["saved"], b: ["best"], c: ["worst"] });
    });

    it("shows a row that is both yours and the best once, titled with both", () => {
      const groups = markedGroups([row("w", -5), row("a", 90, true), row("b", 10)]);
      expect(groups.map((g) => [g.row.id, g.marks])).toEqual([
        ["a", ["saved", "best"]],
        ["w", ["worst"]],
      ]);
    });

    it("marks no best or worst with nothing to compare, and no worst when every row made the same", () => {
      expect(Object.fromEntries(rowMarks([row("a", 10, true)]))).toEqual({ a: ["saved"] });
      expect(Object.fromEntries(rowMarks([row("a", 0, true), row("b", 0)]))).toEqual({ a: ["saved", "best"] });
    });
  });
});
