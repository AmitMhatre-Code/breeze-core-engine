import { describe, expect, it } from "vitest";
import { describeFeed } from "@/lib/scalper-audit";

const feed = (over: Record<string, unknown> = {}) => ({
  feed: {
    warm: false,
    stale: false,
    stale_seconds: 1,
    subscribed: true,
    contract: "24-Sep-2026",
    ticks_seen: 0,
    candles: 0,
    candles_required: 20,
    last_error: null,
    ...over,
  },
});

describe("describeFeed", () => {
  it("names an unsubscribed feed outright rather than calling it warm-up", () => {
    // The whole point: this state and the one below share the `not_warm` reason code, but
    // one resolves itself in twenty minutes and the other never does.
    const summary = describeFeed(feed({ subscribed: false }));
    expect(summary).toEqual({
      text: "Futures feed not subscribed — no ticks are reaching the signal.",
      tone: "bad",
    });
  });

  it("reads as progress while the indicators fill", () => {
    const summary = describeFeed(feed({ candles: 6, ticks_seen: 812 }));
    expect(summary?.tone).toBe("warn");
    expect(summary?.text).toBe("Warming up · 6 of 20 candles · 812 ticks");
  });

  it("reports a live feed with its contract", () => {
    const summary = describeFeed(feed({ warm: true, ticks_seen: 41234, candles: 44 }));
    expect(summary?.tone).toBe("ok");
    expect(summary?.text).toBe("Futures feed live · 41,234 ticks · 24-Sep-2026");
  });

  it("surfaces a subscribe error verbatim, since that is the actionable part", () => {
    const summary = describeFeed(
      feed({ last_error: "No live broker session; futures feed not subscribed." }),
    );
    expect(summary).toEqual({
      text: "Futures feed: No live broker session; futures feed not subscribed.",
      tone: "bad",
    });
  });

  it("calls out a quiet feed with its age", () => {
    const summary = describeFeed(feed({ stale: true, stale_seconds: 42.4, warm: true }));
    expect(summary).toEqual({
      text: "Futures feed quiet for 42s — entries are frozen.",
      tone: "bad",
    });
  });

  it("returns nothing for runs that carry no feed detail", () => {
    // Every non-scalper bot, and any scalper run recorded before the audit detail existed.
    expect(describeFeed(null)).toBeNull();
    expect(describeFeed({})).toBeNull();
    expect(describeFeed({ gates: { trading_allowed: true } })).toBeNull();
  });
});
