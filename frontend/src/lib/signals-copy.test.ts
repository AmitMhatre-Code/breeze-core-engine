import { describe, expect, it } from "vitest";

import { backtestSentence, horizonDetail, verdictTone } from "@/lib/signals";
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

  it("handles a series that has never been backtested", () => {
    expect(backtestSentence(null, 1)).toBe("Not backtested yet.");
    expect(horizonDetail(null)).toBeNull();
    expect(verdictTone(null)).toBe("muted");
  });
});
