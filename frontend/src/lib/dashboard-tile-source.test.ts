import { describe, expect, it } from "vitest";
import { resolveDayPnl, resolveOpenPnl } from "@/lib/dashboard-tile-source";
import type { DayPnlResponse } from "@/lib/dashboard-day-pnl";

function dayPnl(total: number | null): DayPnlResponse {
  return {
    total_day_pnl: total,
    realized_day_pnl: 0,
    unrealized_day_pnl: total,
    is_gross: true,
    as_of: "2026-09-08T16:21:00",
    market_session_state: "post_close",
    contracts_priced: total == null ? 0 : 4,
    contracts_missing_prev_close: total == null ? 4 : 0,
    trades_source_ok: true,
    degraded: total == null,
  };
}

describe("resolveOpenPnl", () => {
  it("prefers the live value when the engine priced every leg", () => {
    expect(resolveOpenPnl(22580, 100)).toEqual({ value: 22580, isSnapshot: false });
  });

  it("falls back to the snapshot when the live figure is withheld", () => {
    expect(resolveOpenPnl(null, 22580)).toEqual({ value: 22580, isSnapshot: true });
  });

  it("keeps a genuine live zero rather than treating it as absent", () => {
    expect(resolveOpenPnl(0, 22580)).toEqual({ value: 0, isSnapshot: false });
  });

  it("reports nothing when neither source has a value", () => {
    expect(resolveOpenPnl(null, null)).toEqual({ value: null, isSnapshot: false });
  });
});

describe("resolveDayPnl", () => {
  it("prefers a live payload that carries a total", () => {
    const live = dayPnl(34270);
    expect(resolveDayPnl(live, dayPnl(1))).toEqual({ value: live, isSnapshot: false });
  });

  it("falls back when the live payload withheld its total", () => {
    const snapshot = dayPnl(34270);
    expect(resolveDayPnl(dayPnl(null), snapshot)).toEqual({
      value: snapshot,
      isSnapshot: true,
    });
  });

  it("falls back when there is no live payload yet", () => {
    const snapshot = dayPnl(34270);
    expect(resolveDayPnl(null, snapshot)).toEqual({ value: snapshot, isSnapshot: true });
  });

  it("reports nothing when neither source has a payload", () => {
    expect(resolveDayPnl(null, undefined)).toEqual({ value: null, isSnapshot: false });
  });
});
