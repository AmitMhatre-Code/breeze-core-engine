import { describe, expect, it } from "vitest";

import { backtestSentence, horizonDetail, measuredFromFiringPrice, verdictTone, volatilityDetail } from "@/lib/signals";
import type { HorizonScore, SeriesBacktestSummary, SignalDirection } from "@/lib/signals";

function score(over: Partial<HorizonScore> = {}): HorizonScore {
  return {
    calls: 1326, days: 107, right: 439, wrong: 612, hit_rate: 0.4177,
    mean_move_bps: -0.36, net_bps: -1.16, t: -10.17, stands_out: false, verdict: "loses",
    ...over,
  };
}

function summary(over: Partial<SeriesBacktestSummary> = {}): SeriesBacktestSummary {
  const follow = score();
  const fade = score({ net_bps: 0.59, t: 2.58, stands_out: true, verdict: "pays" });
  return {
    sessions_replayed: 117, calls: 1326, bullish_calls: 609, bearish_calls: 708,
    right: 439, wrong: 612, hit_rate: 0.4177, verdict: "worse",
    sides: {} as SeriesBacktestSummary["sides"],
    breakeven_bps: 0.8, rough_pnl_rupees: -114_726, mean_move_bps: -0.34,
    withdrawn_early: 0, directional_share: 0.035,
    horizons: {
      // At its own minute following loses badly; the money is fifteen minutes out, faded.
      "1": { follow, fade: score({ net_bps: -0.43, t: -3.8, verdict: "loses" }) },
      "15": { follow, fade },
    } as SeriesBacktestSummary["horizons"],
    best: { ...fade, horizon_minutes: 15, direction: "fade" as SignalDirection },
    tradeable: true,
    breakeven: { bps: 0.8, charges_bps: 0.6, spread_bps: 0.2, lots: 1, lot_size: 65, spread_source: "observed", premium: 90.3 },
    ...over,
  };
}

describe("what the page says about a backtested series", () => {
  it("reports a reliably wrong signal as something to trade against, not as a failure", () => {
    const text = backtestSentence(summary(), 1);
    expect(text).toContain("against it");
    expect(text).toContain("+0.59 bps");
    // The old copy called this "worse than the market's own trend", which reads as a dud.
    expect(text).not.toContain("worse");
  });

  it("says when the money was in holding longer than the signal's own window", () => {
    expect(backtestSentence(summary(), 1)).toContain("held 15 min rather than 1");
  });

  it("does not say so when the best horizon is the signal's own window", () => {
    const s = summary();
    const text = backtestSentence({ ...s, best: { ...s.best!, horizon_minutes: 15 } }, 15);
    expect(text).toContain("over the 15 min it stands");
  });

  it("marks a positive result that is inside the noise as possibly luck", () => {
    const s = summary();
    const best = { ...s.best!, net_bps: 0.2, t: 0.6, stands_out: false, verdict: "unclear" as const };
    const text = backtestSentence({ ...s, best, tradeable: false }, 1);
    expect(text).toContain("inside the normal day-to-day swings");
    expect(verdictTone(best)).toBe("muted");
  });

  it("says plainly when neither direction paid", () => {
    const s = summary();
    const best = { ...s.best!, net_bps: -0.4, t: -1.1, stands_out: false, verdict: "loses" as const };
    const text = backtestSentence({ ...s, best, tradeable: false }, 1);
    expect(text).toContain("either with the signal or against it");
    expect(verdictTone(best)).toBe("down");
  });

  it("refuses to draw a conclusion from too few sessions", () => {
    const s = summary();
    const best = { ...s.best!, days: 4, verdict: "not_enough_days" as const, stands_out: false };
    expect(backtestSentence({ ...s, best, tradeable: false }, 1)).toContain("too few sessions");
  });

  it("shows both directions and where the cost bar came from", () => {
    const text = horizonDetail(summary());
    expect(text).toContain("with -1.16 bps");
    expect(text).toContain("against +0.59 bps");
    expect(text).toContain("1 lot");
    expect(text).toContain("0.60 charges, 0.20 spread");
  });

  it("shows what the best cell read from the firing price, beside the next-trade figure", () => {
    const s = summary();
    const fadeAtClose = score({ net_bps: 1.14, t: 4.1, stands_out: true, verdict: "pays" });
    const text = horizonDetail({
      ...s,
      entry_basis: "next_trade",
      at_signal_close: { horizons: { "15": { follow: score(), fade: fadeAtClose } }, best: null },
    });
    expect(text).toContain("against +0.59 bps");
    expect(text).toContain("From the price that fired the call, which no one can trade at, it read +1.14 bps.");
  });

  it("flags a run that measured from the firing price, and only one with calls", () => {
    expect(measuredFromFiringPrice(summary())).toBe(true);
    expect(measuredFromFiringPrice(summary({ entry_basis: "next_trade" }))).toBe(false);
    expect(measuredFromFiringPrice(summary({ calls: 0 }))).toBe(false);
    expect(measuredFromFiringPrice(null)).toBe(false);
    // An older run has nothing to compare against, so its second line is unchanged.
    expect(horizonDetail(summary())).not.toContain("fired the call");
  });

  it("handles a series that has never been backtested", () => {
    expect(backtestSentence(null, 1)).toBe("Not backtested yet.");
    expect(horizonDetail(null)).toBeNull();
    expect(verdictTone(null)).toBe("muted");
  });
});

describe("versions side by side (#72)", () => {
  it("names a Momentum version, but not expansion's only one", async () => {
    const { versionName, reasonText } = await import("@/lib/signals");
    expect(versionName("momentum", 3)).toBe("Momentum v3");
    expect(versionName("expansion", 3)).toBe("Volume expansion");
    expect(reasonText("withdrawn_for_index")).toBe("not run on this index");
    expect(reasonText("move_too_small")).toBe("move too small");
  });

  it("keys availability by mechanism and version", async () => {
    const { signalKey } = await import("@/lib/use-bots");
    expect(signalKey("momentum", 1)).toBe("momentum-v1");
  });
});

describe("the stricter bar and the movement score (2026-10-09)", () => {
  it("names how many ways the best was picked from", () => {
    expect(backtestSentence(summary({ comparisons: 8 }), 1)).toContain("the best of 8 ways tried");
  });

  it("says when a result did not hold up in the last third", () => {
    const s = summary();
    const best = { ...s.best!, verdict: "unclear" as const, stands_out: false, short_of: "holdout" as const };
    const text = backtestSentence({ ...s, best, tradeable: false }, 1);
    expect(text).toContain("did not pay in the last third");
    expect(text).not.toContain("may be luck");
  });

  it("reports a movement forecast whichever way it points", () => {
    const vol = (ratio: number, verdict: "expands" | "calms" | "unclear") => ({
      calls: 300, days: 60, mean_ratio: ratio, t: 4, holdout_ratio: ratio, verdict,
    });
    const text = volatilityDetail(summary({
      volatility: { "5": vol(1.1, "unclear"), "15": vol(1.42, "expands"), "30": vol(1.2, "expands") },
    }));
    expect(text).toContain("over the 15 min after a call the index moved 1.42×");
    expect(text).toContain("bigger moves");
    expect(volatilityDetail(summary({ volatility: { "15": vol(1.05, "unclear") } })))
      .toContain("no clear change");
    expect(volatilityDetail(summary())).toBeNull();
  });
});
