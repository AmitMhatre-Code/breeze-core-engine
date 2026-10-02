import { describe, expect, it } from "vitest";
import type { PortfolioPositionRecord } from "@/lib/portfolio";
import { portfolioRowDeltas } from "@/lib/portfolio/positionDeltas";
import { bsCallPrice, bsPutPrice, DEFAULT_R } from "@/lib/strategy-builder/blackScholes";
import { yearsToExpiryClose } from "@/lib/strategy-builder/expiry";
import { buildGreeksModel, netDelta, optionDelta } from "@/lib/strategy-builder/greeks";
import type { ChainRow, ChainSuccess } from "@/lib/strategy-builder/types";

const EXPIRY = "16-Jun-2026";
const NOW = Date.UTC(2026, 5, 9, 4, 0);
const SPOT = 24000;

function chain(): ChainSuccess {
  const T = yearsToExpiryClose(EXPIRY, NOW)!;
  const rows: ChainRow[] = [];
  for (let k = 23000; k <= 25000; k += 100) {
    const c = bsCallPrice(SPOT, k, T, 0.14, DEFAULT_R, 0);
    const p = bsPutPrice(SPOT, k, T, 0.14, DEFAULT_R, 0);
    const cell = (x: number) => ({
      ltp: x,
      best_bid_price: x * 0.99,
      best_offer_price: x * 1.01,
      total_buy_qty: 300,
      total_sell_qty: 300,
      lot_size: 75,
    });
    rows.push({ strike_price: k, call: cell(c), put: cell(p) });
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

function row(over: Partial<PortfolioPositionRecord>): PortfolioPositionRecord {
  return {
    stock_code: "NIFTY",
    exchange_code: "NFO",
    expiry_date: EXPIRY,
    right: "Call",
    action: "Sell",
    strike_price: "24300",
    quantity: "75",
    ...over,
  } as PortfolioPositionRecord;
}

describe("portfolioRowDeltas", () => {
  const model = buildGreeksModel(chain(), EXPIRY, NOW)!;

  it("reads ICICI quantity as units and signs by action", () => {
    const [short, long] = portfolioRowDeltas(model, [
      row({ action: "Sell", quantity: "150" }),
      row({ action: "Buy", right: "Put", strike_price: "23700", quantity: "75" }),
    ]);
    const callPer = optionDelta(model, "Call", 24300)!.perUnit;
    const putPer = optionDelta(model, "Put", 23700)!.perUnit;
    expect(short!.position).toBeCloseTo(-150 * callPer, 9);
    expect(long!.position).toBeCloseTo(75 * putPer, 9);
  });

  it("accepts ICICI's CE/PE spellings and string strikes with commas", () => {
    const [d] = portfolioRowDeltas(model, [row({ right: "CE", strike_price: "24,300" })]);
    expect(d!.perUnit).toBeCloseTo(optionDelta(model, "Call", 24300)!.perUnit, 9);
  });

  it("skips squared-off legs and nulls unpriceable ones", () => {
    const deltas = portfolioRowDeltas(model, [
      row({ quantity: "0" }),
      row({ quantity: "75" }),
      row({ quantity: "75", right: "" }),
    ]);
    expect(deltas[0]).toBeUndefined();
    expect(deltas[1]).not.toBeNull();
    expect(deltas[2]).toBeNull();
    expect(netDelta(deltas)).toBeNull();
    expect(netDelta(deltas.slice(0, 2))).toBeCloseTo(deltas[1]!.position, 9);
  });

  it("returns null for open legs while the chain hasn't loaded", () => {
    expect(portfolioRowDeltas(null, [row({})])).toEqual([null]);
  });

  it("nets a short strangle close to flat", () => {
    const deltas = portfolioRowDeltas(model, [
      row({ right: "Call", strike_price: "24400" }),
      row({ right: "Put", strike_price: "23600" }),
    ]);
    const net = netDelta(deltas)!;
    // Each leg alone is several units; the pair largely offsets.
    expect(Math.abs(deltas[0]!.position)).toBeGreaterThan(5);
    expect(Math.abs(net)).toBeLessThan(Math.abs(deltas[0]!.position));
  });
});
