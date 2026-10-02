import { describe, expect, it } from "vitest";
import {
  bsCallDelta,
  bsCallPrice,
  bsPutDelta,
  bsPutPrice,
  DEFAULT_R,
} from "@/lib/strategy-builder/blackScholes";
import { yearsToExpiryClose } from "@/lib/strategy-builder/expiry";
import {
  buildGreeksModel,
  formatOptionDelta,
  formatPositionDelta,
  impliedForward,
  legSigma,
  netDelta,
  optionDelta,
  positionDelta,
  scenarioGreeks,
  strategyLegDeltas,
} from "@/lib/strategy-builder/greeks";
import type { ChainRow, ChainSuccess, StrategyLeg } from "@/lib/strategy-builder/types";

const EXPIRY = "16-Jun-2026";
/** 09:30 IST on 9 Jun 2026 — seven days and six hours before the 15:30 IST close. */
const NOW = Date.UTC(2026, 5, 9, 4, 0);
const YEAR_MS = 365 * 24 * 3600 * 1000;
const SPOT = 24000;
const SIGMA = 0.15;

type CellOpts = { spread?: number; withBook?: boolean; ltpOnly?: boolean };

function cell(price: number, opts: CellOpts = {}): Record<string, unknown> {
  if (opts.ltpOnly) return { ltp: price };
  const half = (opts.spread ?? 0.02) / 2;
  return {
    ltp: price,
    best_bid_price: price * (1 - half),
    best_offer_price: price * (1 + half),
    total_buy_qty: opts.withBook === false ? 0 : 500,
    total_sell_qty: opts.withBook === false ? 0 : 500,
  };
}

/** A chain priced by Black-Scholes at a known forward, so the model must recover it. */
function pricedChain(forward: number, opts: CellOpts = {}): ChainSuccess {
  const T = yearsToExpiryClose(EXPIRY, NOW)!;
  const q = DEFAULT_R - Math.log(forward / SPOT) / T;
  const rows: ChainRow[] = [];
  for (let k = 23000; k <= 25000; k += 100) {
    rows.push({
      strike_price: k,
      call: cell(bsCallPrice(SPOT, k, T, SIGMA, DEFAULT_R, q), opts),
      put: cell(bsPutPrice(SPOT, k, T, SIGMA, DEFAULT_R, q), opts),
    });
  }
  return {
    chain_rows: rows,
    spot_price: SPOT,
    atm_strike: 24000,
    expiry_display: EXPIRY,
    stock_code: "NIFTY",
    exchange_code: "NFO",
  };
}

function leg(id: string, right: "Call" | "Put", side: "Buy" | "Sell", strike: number, lots: number): StrategyLeg {
  return { id, right, side, strike, lots };
}

describe("yearsToExpiryClose", () => {
  it("counts to 15:30 IST on expiry day, not midnight", () => {
    expect(yearsToExpiryClose(EXPIRY, NOW)! * YEAR_MS).toBeCloseTo(
      (7 * 24 + 6) * 3600 * 1000,
      -2,
    );
  });

  it("is exactly one day at 15:30 IST the day before expiry", () => {
    expect(yearsToExpiryClose(EXPIRY, Date.UTC(2026, 5, 15, 10, 0))).toBeCloseTo(1 / 365, 10);
  });

  it("is hours, not a whole day, on expiry morning", () => {
    // 09:15 IST: 6h15m to the close.
    const t = yearsToExpiryClose(EXPIRY, Date.UTC(2026, 5, 16, 3, 45))!;
    expect(t * 365 * 24).toBeCloseTo(6.25, 6);
  });

  it("floors at one minute after the close and rejects bad dates", () => {
    const t = yearsToExpiryClose(EXPIRY, Date.UTC(2026, 5, 16, 11, 0))!;
    expect(t * YEAR_MS).toBeCloseTo(60_000, -1);
    expect(yearsToExpiryClose("2026-06-16", NOW)).toBeNull();
    expect(yearsToExpiryClose("16-Foo-2026", NOW)).toBeNull();
  });
});

describe("impliedForward", () => {
  it("recovers the forward the chain was priced at", () => {
    const T = yearsToExpiryClose(EXPIRY, NOW)!;
    expect(impliedForward(pricedChain(24060), T)).toBeCloseTo(24060, 0);
    expect(impliedForward(pricedChain(23980), T)).toBeCloseTo(23980, 0);
  });

  it("ignores a parity read implausibly far from spot", () => {
    const T = yearsToExpiryClose(EXPIRY, NOW)!;
    expect(impliedForward(pricedChain(SPOT * 1.05), T)).toBeNull();
  });

  it("needs trusted two-sided quotes", () => {
    const T = yearsToExpiryClose(EXPIRY, NOW)!;
    expect(impliedForward(pricedChain(24060, { spread: 0.3 }), T)).toBeNull();
    expect(impliedForward(pricedChain(24060, { withBook: false }), T)).toBeNull();
  });
});

describe("buildGreeksModel", () => {
  it("prices deltas at the parity forward and the chain's own IV", () => {
    const model = buildGreeksModel(pricedChain(24060), EXPIRY, NOW)!;
    expect(model.forwardSource).toBe("parity");
    const { T, q } = model;
    expect(SPOT * Math.exp((DEFAULT_R - q) * T)).toBeCloseTo(24060, 0);
    const call = optionDelta(model, "Call", 24200)!;
    const put = optionDelta(model, "Put", 23800)!;
    expect(call.sigmaSource).toBe("smile");
    expect(call.sigma).toBeCloseTo(SIGMA, 3);
    expect(call.perUnit).toBeCloseTo(bsCallDelta(SPOT, 24200, T, SIGMA, DEFAULT_R, q), 3);
    expect(put.perUnit).toBeCloseTo(bsPutDelta(SPOT, 23800, T, SIGMA, DEFAULT_R, q), 3);
    expect(call.perUnit).toBeGreaterThan(0);
    expect(put.perUnit).toBeLessThan(0);
  });

  it("falls back to the default carry when parity can't be read", () => {
    const model = buildGreeksModel(pricedChain(24060, { ltpOnly: true }), EXPIRY, NOW)!;
    expect(model.forwardSource).toBe("default");
    expect(model.q).toBe(0);
  });

  it("returns null without spot or a valid expiry", () => {
    expect(buildGreeksModel({ ...pricedChain(24000), spot_price: null }, EXPIRY, NOW)).toBeNull();
    expect(buildGreeksModel(pricedChain(24000), "bad", NOW)).toBeNull();
    expect(buildGreeksModel(null, EXPIRY, NOW)).toBeNull();
  });
});

describe("legSigma", () => {
  it("uses the strike's own LTP when no smile can be built", () => {
    const model = buildGreeksModel(pricedChain(24000, { ltpOnly: true }), EXPIRY, NOW)!;
    const s = legSigma(model, "Call", 24300)!;
    expect(s.source).toBe("ltp");
    // Priced at forward 24000, read at the default carry: close to, not exactly, SIGMA.
    expect(s.sigma).toBeCloseTo(SIGMA, 1);
  });

  it("uses ATM when the strike has no price, and gives up with nothing at all", () => {
    const chain = pricedChain(24000, { ltpOnly: true });
    chain.chain_rows = chain.chain_rows.map((r) =>
      r.strike_price === 24000 ? r : { ...r, call: { ltp: 0 }, put: { ltp: 0 } },
    );
    const model = buildGreeksModel(chain, EXPIRY, NOW)!;
    expect(legSigma(model, "Call", 24300)!.source).toBe("atm");

    const empty = buildGreeksModel(
      { ...chain, chain_rows: chain.chain_rows.map((r) => ({ ...r, call: null, put: null })) },
      EXPIRY,
      NOW,
    )!;
    expect(legSigma(empty, "Call", 24300)).toBeNull();
    expect(optionDelta(empty, "Call", 24300)).toBeNull();
  });
});

describe("position and net delta", () => {
  const model = buildGreeksModel(pricedChain(24060), EXPIRY, NOW)!;
  const LOT = 75;

  it("signs by side and scales by units", () => {
    const per = optionDelta(model, "Call", 24200)!.perUnit;
    const short = positionDelta(model, { right: "Call", side: "Sell", strike: 24200, units: 150 })!;
    expect(short.position).toBeCloseTo(-150 * per, 9);
    const long = positionDelta(model, { right: "Call", side: "Buy", strike: 24200, units: 150 })!;
    expect(long.position).toBeCloseTo(150 * per, 9);
  });

  it("sums legs exactly and matches the scenario total with no scenario", () => {
    const legs = [
      leg("a", "Call", "Sell", 24300, 2),
      leg("b", "Call", "Buy", 24600, 2),
      leg("c", "Put", "Sell", 23700, 2),
      leg("d", "Put", "Buy", 23400, 2),
    ];
    const deltas = strategyLegDeltas(model, legs, LOT);
    const net = netDelta(Object.values(deltas))!;
    const manual = legs.reduce((s, l) => s + deltas[l.id]!.position, 0);
    expect(net).toBeCloseTo(manual, 9);
    expect(scenarioGreeks(model, legs, LOT)!.delta).toBeCloseTo(net, 6);
  });

  it("leaves zero-lot legs out instead of blanking the total", () => {
    const legs = [leg("a", "Call", "Sell", 24300, 1), leg("z", "Put", "Sell", 23700, 0)];
    const deltas = strategyLegDeltas(model, legs, LOT);
    expect("z" in deltas).toBe(false);
    expect(netDelta(Object.values(deltas))).toBeCloseTo(deltas.a!.position, 9);
  });

  it("refuses a partial total", () => {
    expect(netDelta([null])).toBeNull();
    expect(netDelta([])).toBeNull();
    const d = positionDelta(model, { right: "Put", side: "Buy", strike: 23800, units: 75 });
    expect(netDelta([d, null])).toBeNull();
  });

  it("applies the IV shock and DTE override only to the scenario read-out", () => {
    const legs = [leg("a", "Call", "Sell", 24300, 1)];
    const base = scenarioGreeks(model, legs, LOT)!;
    const shocked = scenarioGreeks(model, legs, LOT, { ivShockPct: 20 })!;
    expect(shocked.delta).not.toBeCloseTo(base.delta, 3);
    const later = scenarioGreeks(model, legs, LOT, { tYears: 1 / 365 })!;
    expect(later.delta).not.toBeCloseTo(base.delta, 3);
  });
});

describe("formatting", () => {
  it("formats option delta with an explicit sign", () => {
    expect(formatOptionDelta(0.3249)).toBe("+0.32");
    expect(formatOptionDelta(-0.455)).toBe("−0.46");
    expect(formatOptionDelta(0.001)).toBe("0.00");
    expect(formatOptionDelta(null)).toBe("—");
  });

  it("formats position delta in units", () => {
    expect(formatPositionDelta(24.56)).toBe("+24.6");
    expect(formatPositionDelta(-1230.4)).toBe("−1,230");
    expect(formatPositionDelta(0.01)).toBe("0");
    expect(formatPositionDelta(undefined)).toBe("—");
  });
});
