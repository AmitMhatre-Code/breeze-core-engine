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

  it("says a scalper on volume expansion won't trade through a futures rollover", () => {
    // 2026-09-29: every pass read `excluded_session` while the card said "Futures feed live".
    const standdown = {
      reason: "futures_rollover",
      series: "nifty:expansion:1m",
      label: "Volume expansion 1m",
      role: "signal",
      futures_expiry: "29-Sep-2026",
    };
    const live = { ...feed({ warm: true, ticks_seen: 41234 }), signal_standdown: standdown };
    expect(describeFeed(live)).toEqual({
      text:
        "Won't trade today — set to Volume expansion 1m, and open interest changes near " +
        "futures expiry (29-Sep-2026) are unreliable.",
      tone: "warn",
    });
    // Warming up is moot on a day the signal cannot read either.
    expect(describeFeed({ ...feed({ candles: 3 }), signal_standdown: standdown })?.text).toMatch(
      /^Won't trade today/,
    );
  });

  it("names the Iron Fly's entry filter rather than a signal it trades", () => {
    const summary = describeFeed({
      ...feed({ warm: true }),
      signal_standdown: { label: "Volume expansion 15m", role: "entry filter", futures_expiry: "29-Sep-2026" },
    });
    expect(summary?.text).toContain("its entry filter is Volume expansion 15m");
  });

  it("still reports a broken feed on a rollover day, since it will still be broken tomorrow", () => {
    const summary = describeFeed({
      ...feed({ subscribed: false }),
      signal_standdown: { label: "Volume expansion 1m", role: "signal" },
    });
    expect(summary?.tone).toBe("bad");
    expect(summary?.text).toMatch(/not subscribed/);
  });

  it("ignores an empty standdown", () => {
    const summary = describeFeed({ ...feed({ warm: true, ticks_seen: 5 }), signal_standdown: null });
    expect(summary?.text).toBe("Futures feed live · 5 ticks · 24-Sep-2026");
  });

  it("returns nothing for runs that carry no feed detail", () => {
    // Every non-scalper bot, and any scalper run recorded before the audit detail existed.
    expect(describeFeed(null)).toBeNull();
    expect(describeFeed({})).toBeNull();
    expect(describeFeed({ gates: { trading_allowed: true } })).toBeNull();
  });
});
