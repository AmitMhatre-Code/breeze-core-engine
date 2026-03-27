import { impliedVolatility } from "@/lib/strategy-builder/blackScholes";
import type { ChainSuccess } from "@/lib/strategy-builder/types";

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
