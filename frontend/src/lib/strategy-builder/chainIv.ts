import { impliedVolatility } from "@/lib/strategy-builder/blackScholes";
import type { ChainSuccess, StrategyLeg } from "@/lib/strategy-builder/types";

function parseChainNumber(v: unknown): number {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string") {
    const n = parseFloat(v.replace(/,/g, ""));
    return Number.isFinite(n) ? n : NaN;
  }
  return NaN;
}

/** ATM implied vol from chain LTPs (same logic as Strategy Builder payoff panel). */
export function atmSigmaFromChain(chain: ChainSuccess, T: number): number {
  const spot = chain.spot_price;
  const atm = chain.atm_strike;
  if (spot == null || atm == null) return 0.22;
  const row = chain.chain_rows.find((r) => r.strike_price === atm);
  if (!row) return 0.22;
  const ivs: number[] = [];
  const c = parseChainNumber(row.call?.ltp);
  const p = parseChainNumber(row.put?.ltp);
  if (c > 0) {
    const iv = impliedVolatility("call", c, spot, atm, T);
    if (iv != null) ivs.push(iv);
  }
  if (p > 0) {
    const iv = impliedVolatility("put", p, spot, atm, T);
    if (iv != null) ivs.push(iv);
  }
  if (!ivs.length) return 0.22;
  return ivs.reduce((a, b) => a + b, 0) / ivs.length;
}

export type SmilePoint = { x: number; iv: number };
/** Sorted ascending by x = ln(strike/spot). */
export type SigmaSmile = SmilePoint[];
export type SigmaSmiles = { call: SigmaSmile; put: SigmaSmile };

export const MAX_TRUSTED_REL_SPREAD = 0.10;

/** Stricter than "has a two-sided market": caps relative bid/ask spread so this strike's own
 * IV inversion is numerically stable enough to serve as a smile anchor. Thin, wide-relative-
 * spread, near-zero-premium quotes (deep OTM, far from spot) can have a real two-sided market
 * and still produce a noisy IV inversion off a single tick. */
function isTrustedQuote(bid: number, ask: number): boolean {
  if (!(bid > 0) || !(ask > 0)) return false;
  const mid = (bid + ask) / 2;
  return mid > 0 && (ask - bid) / mid <= MAX_TRUSTED_REL_SPREAD;
}

/** Per-side IV smile from trust-gated chain quotes, in log-moneyness space. Reuses data
 * already present in `chain_rows` (best_bid_price/best_offer_price/total_buy_qty/
 * total_sell_qty) — no new fetch. */
export function buildSigmaSmile(
  chain: ChainSuccess,
  T: number,
  right: "call" | "put",
): SigmaSmile {
  const spot = chain.spot_price;
  if (spot == null || spot <= 0) return [];
  const points: SmilePoint[] = [];
  for (const row of chain.chain_rows) {
    const leg = right === "call" ? row.call : row.put;
    if (!leg) continue;
    const buyQty = parseChainNumber(leg.total_buy_qty);
    const sellQty = parseChainNumber(leg.total_sell_qty);
    if (!(buyQty > 0) || !(sellQty > 0)) continue;
    const bid = parseChainNumber(leg.best_bid_price);
    const ask = parseChainNumber(leg.best_offer_price);
    if (!isTrustedQuote(bid, ask)) continue;
    const iv = impliedVolatility(right, (bid + ask) / 2, spot, row.strike_price, T);
    if (iv == null || iv <= 0) continue;
    points.push({ x: Math.log(row.strike_price / spot), iv });
  }
  return points.sort((a, b) => a.x - b.x);
}

export function buildSigmaSmiles(chain: ChainSuccess, T: number): SigmaSmiles {
  return { call: buildSigmaSmile(chain, T, "call"), put: buildSigmaSmile(chain, T, "put") };
}

/** Linear interpolation in log-moneyness space; flat-clamp beyond the outermost anchor;
 * falls back to `fallback` (ATM iv, else 0.2/0.22) when the side has fewer than 2 anchors. */
export function sigmaForStrike(
  smile: SigmaSmile,
  strike: number,
  spot: number,
  fallback: number,
): number {
  if (smile.length < 2 || !(spot > 0) || !(strike > 0)) return fallback;
  const x = Math.log(strike / spot);
  if (x <= smile[0].x) return smile[0].iv;
  const last = smile[smile.length - 1];
  if (x >= last.x) return last.iv;
  for (let i = 1; i < smile.length; i++) {
    const lo = smile[i - 1];
    const hi = smile[i];
    if (x >= lo.x && x <= hi.x) {
      if (hi.x === lo.x) return lo.iv;
      const t = (x - lo.x) / (hi.x - lo.x);
      return lo.iv + t * (hi.iv - lo.iv);
    }
  }
  return fallback; // unreachable given the bounds checks above
}

export function sigmaForLeg(
  smiles: SigmaSmiles | null,
  leg: StrategyLeg,
  spot: number,
  fallback: number,
): number {
  if (!smiles || !(spot > 0)) return fallback;
  const curve = leg.right === "Call" ? smiles.call : smiles.put;
  return sigmaForStrike(curve, leg.strike, spot, fallback);
}

/** Notional-weighted (quantity × |premium|, falling back to quantity-only if premium is
 * unknown/zero) blend of each leg's own interpolated sigma into ONE sigma — needed only
 * because the Monte Carlo PoP shares one simulated terminal price across all legs per sample,
 * so a multi-strike basket can't give each leg its own diffusion path. Degenerates correctly
 * to that leg's own `sigmaForLeg` for a single-leg list. */
export function blendedSigmaForLegs(
  smiles: SigmaSmiles | null,
  legs: StrategyLeg[],
  spot: number,
  lotSize: number,
  fallback: number,
): number {
  if (!legs.length) return fallback;
  let weightedSum = 0;
  let totalWeight = 0;
  for (const leg of legs) {
    const units = Math.max(0, leg.lots) * Math.max(0, lotSize);
    const premium = leg.premiumPerUnit ?? 0;
    let weight = units * Math.abs(premium);
    if (!(weight > 0)) weight = units;
    if (!(weight > 0)) continue;
    weightedSum += weight * sigmaForLeg(smiles, leg, spot, fallback);
    totalWeight += weight;
  }
  return totalWeight > 0 ? weightedSum / totalWeight : fallback;
}
