import {
  bsCallDelta,
  bsPutDelta,
  DEFAULT_Q,
  DEFAULT_R,
  impliedVolatility,
} from "@/lib/strategy-builder/blackScholes";
import {
  buildSigmaSmiles,
  parseChainNumber,
  sigmaForStrike,
  trustedMid,
  type SigmaSmiles,
} from "@/lib/strategy-builder/chainIv";
import { yearsToExpiryClose } from "@/lib/strategy-builder/expiry";
import { portfolioGreeks, type PortfolioGreeks } from "@/lib/strategy-builder/payoff";
import type {
  ChainRow,
  ChainSuccess,
  OptionRight,
  OrderSide,
  StrategyLeg,
} from "@/lib/strategy-builder/types";

/** Strikes nearest spot whose call and put both have trusted quotes, read for put-call parity. */
const PARITY_STRIKES = 5;
/** A parity forward further than this from spot is treated as a bad read, not a real basis. */
const MAX_FORWARD_BASIS = 0.03;

/** Where a leg's volatility came from, most to least specific. */
export type SigmaSource = "smile" | "ltp" | "atm";

/**
 * Everything the Greeks need from one chain (one underlying, one expiry), built once per
 * chain refresh so every leg on the page is priced off the same spot, time and forward.
 *
 * Index and stock options trade off the future, not spot. The forward comes from put-call
 * parity at the strikes nearest spot and is carried as an implied yield `q`, so
 * S·e^((r−q)T) equals that forward and the spot-based Black-Scholes functions price
 * consistently with it. With no usable parity read, `q` stays at the default.
 */
export type GreeksModel = {
  spot: number;
  /** Years until 15:30 IST on expiry day. */
  T: number;
  r: number;
  q: number;
  forwardSource: "parity" | "default";
  smiles: SigmaSmiles;
  atmSigma: number | null;
  rows: ReadonlyMap<number, ChainRow>;
};

function median(values: number[]): number {
  const s = [...values].sort((a, b) => a - b);
  const mid = Math.floor(s.length / 2);
  return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
}

/** Forward implied by C − P = e^(−rT)(F − K) at the trusted strikes nearest spot, else null. */
export function impliedForward(
  chain: ChainSuccess,
  T: number,
  r: number = DEFAULT_R,
): number | null {
  const spot = chain.spot_price;
  if (spot == null || !(spot > 0) || !(T > 0)) return null;
  const reads: number[] = [];
  const byDistance = [...chain.chain_rows].sort(
    (a, b) => Math.abs(a.strike_price - spot) - Math.abs(b.strike_price - spot),
  );
  for (const row of byDistance) {
    const c = trustedMid(row.call);
    const p = trustedMid(row.put);
    if (c == null || p == null) continue;
    reads.push(row.strike_price + Math.exp(r * T) * (c - p));
    if (reads.length === PARITY_STRIKES) break;
  }
  if (!reads.length) return null;
  const forward = median(reads);
  if (!(forward > 0) || Math.abs(Math.log(forward / spot)) > MAX_FORWARD_BASIS) return null;
  return forward;
}

function cellFor(row: ChainRow | undefined, right: OptionRight): Record<string, unknown> | null {
  if (!row) return null;
  return (right === "Call" ? row.call : row.put) ?? null;
}

function ivFromPrice(
  right: OptionRight,
  price: number,
  spot: number,
  strike: number,
  T: number,
  r: number,
  q: number,
): number | null {
  if (!(price > 0)) return null;
  const iv = impliedVolatility(right === "Call" ? "call" : "put", price, spot, strike, T, r, q);
  return iv != null && iv > 0 ? iv : null;
}

function atmSigma(
  chain: ChainSuccess,
  rows: ReadonlyMap<number, ChainRow>,
  spot: number,
  T: number,
  r: number,
  q: number,
): number | null {
  const atm = chain.atm_strike;
  if (atm == null) return null;
  const row = rows.get(atm);
  const ivs: number[] = [];
  for (const right of ["Call", "Put"] as const) {
    const cell = cellFor(row, right);
    const price = trustedMid(cell) ?? parseChainNumber(cell?.ltp);
    const iv = ivFromPrice(right, price, spot, atm, T, r, q);
    if (iv != null) ivs.push(iv);
  }
  return ivs.length ? ivs.reduce((a, b) => a + b, 0) / ivs.length : null;
}

export function buildGreeksModel(
  chain: ChainSuccess | null | undefined,
  expiryDisplay: string,
  nowMs: number = Date.now(),
): GreeksModel | null {
  if (!chain) return null;
  const spot = chain.spot_price;
  const T = yearsToExpiryClose(expiryDisplay || chain.expiry_display, nowMs);
  if (spot == null || !(spot > 0) || T == null) return null;
  const r = DEFAULT_R;
  const forward = impliedForward(chain, T, r);
  const q = forward != null ? r - Math.log(forward / spot) / T : DEFAULT_Q;
  const rows = new Map(chain.chain_rows.map((row) => [row.strike_price, row]));
  return {
    spot,
    T,
    r,
    q,
    forwardSource: forward != null ? "parity" : "default",
    smiles: buildSigmaSmiles(chain, T, q),
    atmSigma: atmSigma(chain, rows, spot, T, r, q),
    rows,
  };
}

/**
 * Volatility for one strike: the trusted-quote smile (which passes through the strike's own
 * IV when that quote is itself trusted), else the strike's own LTP, else ATM. Null when the
 * chain has none of these — a delta from a guessed volatility is not shown.
 */
export function legSigma(
  model: GreeksModel,
  right: OptionRight,
  strike: number,
): { sigma: number; source: SigmaSource } | null {
  const curve = right === "Call" ? model.smiles.call : model.smiles.put;
  if (curve.length >= 2) {
    return { sigma: sigmaForStrike(curve, strike, model.spot, NaN), source: "smile" };
  }
  const ltp = parseChainNumber(cellFor(model.rows.get(strike), right)?.ltp);
  const own = ivFromPrice(right, ltp, model.spot, strike, model.T, model.r, model.q);
  if (own != null) return { sigma: own, source: "ltp" };
  if (model.atmSigma != null) return { sigma: model.atmSigma, source: "atm" };
  return null;
}

export type OptionDelta = {
  /** Delta of one unit of the contract (calls 0…1, puts −1…0), before side or quantity. */
  perUnit: number;
  sigma: number;
  sigmaSource: SigmaSource;
};

export function optionDelta(
  model: GreeksModel,
  right: OptionRight,
  strike: number,
): OptionDelta | null {
  if (!(strike > 0)) return null;
  const s = legSigma(model, right, strike);
  if (!s) return null;
  const { spot, T, r, q } = model;
  const perUnit =
    right === "Call"
      ? bsCallDelta(spot, strike, T, s.sigma, r, q)
      : bsPutDelta(spot, strike, T, s.sigma, r, q);
  return Number.isFinite(perUnit)
    ? { perUnit, sigma: s.sigma, sigmaSource: s.source }
    : null;
}

export type PositionDelta = OptionDelta & {
  /** Side- and quantity-weighted delta, in units of the underlying. Sums across legs. */
  position: number;
  units: number;
  side: OrderSide;
};

export function positionDelta(
  model: GreeksModel,
  leg: { right: OptionRight; side: OrderSide; strike: number; units: number },
): PositionDelta | null {
  const d = optionDelta(model, leg.right, leg.strike);
  if (!d) return null;
  const units = Math.max(0, leg.units);
  const sign = leg.side === "Buy" ? 1 : -1;
  return { ...d, position: sign * units * d.perUnit, units, side: leg.side };
}

/** Per-leg position deltas for builder legs (lots × lot size), keyed by leg id. Legs with
 * no lots are left out, so they neither show a delta nor hold back the total. */
export function strategyLegDeltas(
  model: GreeksModel | null,
  legs: StrategyLeg[],
  lotSize: number,
): Record<string, PositionDelta | null> {
  const out: Record<string, PositionDelta | null> = {};
  for (const leg of legs) {
    if (!(leg.lots > 0)) continue;
    out[leg.id] = model
      ? positionDelta(model, {
          right: leg.right,
          side: leg.side,
          strike: leg.strike,
          units: leg.lots * lotSize,
        })
      : null;
  }
  return out;
}

/**
 * Sum of position deltas. Exact for legs on one underlying — a derivative of a sum is the
 * sum of derivatives — so the only approximation is inside each leg. Null when any counted
 * leg has no delta, since a partial total would read as the whole position's.
 */
export function netDelta(deltas: ReadonlyArray<PositionDelta | null | undefined>): number | null {
  let total = 0;
  let counted = 0;
  for (const d of deltas) {
    if (d === undefined) continue;
    if (d === null) return null;
    total += d.position;
    counted += 1;
  }
  return counted ? total : null;
}

/**
 * Delta, gamma, vega and theta for the payoff panels' "what if" read-out. With no scenario
 * (no DTE override, no IV shock) its delta equals the sum of the legs table's deltas.
 */
export function scenarioGreeks(
  model: GreeksModel | null,
  legs: StrategyLeg[],
  lotSize: number,
  opts: { tYears?: number | null; ivShockPct?: number } = {},
): PortfolioGreeks | null {
  if (!model) return null;
  const active = legs.filter((l) => l.lots > 0);
  if (!active.length) return null;
  const sigmas = new Map<string, number>();
  for (const leg of active) {
    const s = legSigma(model, leg.right, leg.strike);
    if (!s) return null;
    sigmas.set(leg.id, s.sigma * (1 + (opts.ivShockPct ?? 0) / 100));
  }
  const T = opts.tYears != null && opts.tYears > 0 ? opts.tYears : model.T;
  return portfolioGreeks(
    model.spot,
    active,
    lotSize,
    T,
    (leg) => sigmas.get(leg.id) ?? NaN,
    model.r,
    model.q,
  );
}

/** "+0.32" / "−0.45": one contract's delta. */
export function formatOptionDelta(perUnit: number | null | undefined): string {
  if (perUnit == null || !Number.isFinite(perUnit)) return "—";
  // Round the magnitude so −0.455 and +0.455 both land on 0.46.
  const abs = Math.round(Math.abs(perUnit) * 100) / 100;
  if (abs === 0) return "0.00";
  return `${perUnit > 0 ? "+" : "−"}${abs.toFixed(2)}`;
}

/** "+24.6" / "−1,230": a position's delta in units of the underlying. */
export function formatPositionDelta(units: number | null | undefined): string {
  if (units == null || !Number.isFinite(units)) return "—";
  const abs = Math.abs(units);
  const digits = abs >= 100 ? 0 : 1;
  const rounded = Number(abs.toFixed(digits));
  if (rounded === 0) return "0";
  const text = rounded.toLocaleString("en-IN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
  return `${units > 0 ? "+" : "−"}${text}`;
}

/** Tone for a position delta: long the underlying reads up, short reads down. */
export function positionDeltaToneClass(units: number | null | undefined): string {
  if (units == null || !Number.isFinite(units) || Math.abs(units) < 0.05) return "text-muted";
  return units > 0 ? "text-up" : "text-down";
}

const SIGMA_SOURCE_LABEL: Record<SigmaSource, string> = {
  smile: "chain IV smile",
  ltp: "this strike's LTP",
  atm: "ATM IV (no quote at this strike)",
};

/** Hover text explaining one leg's delta. */
export function positionDeltaTitle(d: PositionDelta | null, lotSize?: number | null): string {
  if (!d) return "Delta unavailable: the chain has no usable price for this strike yet.";
  const lots =
    lotSize != null && lotSize > 0 ? ` (${formatPositionDelta(d.position / lotSize)} lots)` : "";
  return (
    `Option delta ${formatOptionDelta(d.perUnit)} per unit × ${d.units.toLocaleString("en-IN")} ` +
    `${d.side === "Buy" ? "bought" : "sold"} = ${formatPositionDelta(d.position)} units of the underlying${lots}. ` +
    `IV ${(d.sigma * 100).toFixed(1)}% from ${SIGMA_SOURCE_LABEL[d.sigmaSource]}.`
  );
}

/** Hover text for a strategy or group total. */
export function netDeltaTitle(net: number | null, lotSize?: number | null): string {
  if (net == null) return "Net delta unavailable until every leg has a delta.";
  const lots =
    lotSize != null && lotSize > 0 ? ` (${formatPositionDelta(net / lotSize)} lots)` : "";
  return (
    `Net delta ${formatPositionDelta(net)} units${lots}: the position gains about ` +
    `₹${Math.abs(net).toLocaleString("en-IN", { maximumFractionDigits: 0 })} for each 1-point ` +
    `${net >= 0 ? "rise" : "fall"} in the underlying, before gamma.`
  );
}
