import type { ChainRow } from "@/lib/strategy-builder/types";

export function parseNum(v: unknown): number | null {
  if (v == null || v === "") return null;
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string") {
    const t = v.trim().replace(/,/g, "");
    if (!t || t === "*") return null;
    const n = Number(t);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

/** Maps `Call|<strike>` / `Put|<strike>` -> live LTP from a WS-fed chain response. */
export function chainLtpLookup(chainRows: ChainRow[]): Map<string, number> {
  const m = new Map<string, number>();
  for (const row of chainRows) {
    const callLtp = parseNum(row.call?.ltp);
    if (callLtp != null && callLtp > 0) m.set(`Call|${row.strike_price}`, callLtp);
    const putLtp = parseNum(row.put?.ltp);
    if (putLtp != null && putLtp > 0) m.set(`Put|${row.strike_price}`, putLtp);
  }
  return m;
}

/**
 * Live MTM for one leg: SELL -> (avg - ltp) × qty, BUY -> (ltp - avg) × qty.
 * `action` must already be the position's own entry side, not a closing order's
 * (inverted) side. Returns null if any input is missing/unusable.
 */
export function computeLegMtm(
  action: string | null | undefined,
  avgPrice: number | null,
  quantity: number | null,
  liveLtp: number | null,
): number | null {
  const side = String(action ?? "").trim().toUpperCase();
  if (avgPrice == null || quantity == null || liveLtp == null) return null;
  if (side !== "SELL" && side !== "BUY") return null;
  return side === "SELL" ? (avgPrice - liveLtp) * quantity : (liveLtp - avgPrice) * quantity;
}
