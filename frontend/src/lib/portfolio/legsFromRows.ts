import type { PortfolioPositionRecord } from "@/lib/portfolio";
import { normRight, normSide } from "@/lib/portfolio/legNormalize";
import type { ChainSuccess, StrategyLeg } from "@/lib/strategy-builder/types";

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

export function chainLotSize(chain: ChainSuccess): number {
  const row = chain.chain_rows[0];
  if (!row) return 1;
  const ls =
    parseNum(row.call?.lot_size) ?? parseNum(row.put?.lot_size) ?? NaN;
  return Number.isFinite(ls) && ls > 0 ? Math.round(ls) : 1;
}

export function rowsToStrategyLegs(
  rows: PortfolioPositionRecord[],
  lotSize: number,
): StrategyLeg[] {
  const ls = lotSize > 0 ? lotSize : 1;
  const legs: StrategyLeg[] = [];
  let i = 0;
  for (const row of rows) {
    const side = normSide(String(row.action ?? ""));
    const right = normRight(String(row.right ?? ""));
    const strike = parseNum(row.strike_price);
    const qtyRaw = parseNum(row.quantity);
    if (!side || !right || strike == null || strike <= 0 || qtyRaw == null) {
      continue;
    }
    const absQty = Math.abs(qtyRaw);
    if (absQty <= 0) continue;
    const lots = absQty / ls;
    const prem = parseNum(row.average_price) ?? parseNum(row.ltp) ?? 0;
    legs.push({
      id: `port-${i++}`,
      right,
      side,
      strike,
      lots,
      premiumPerUnit: prem,
    });
  }
  return legs;
}
