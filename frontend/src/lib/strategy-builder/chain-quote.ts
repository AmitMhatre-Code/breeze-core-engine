import { ltpAsOrderPrice } from "@/lib/order-confirm";
import type { ChainRow, OptionRight, StrategyLeg } from "@/lib/strategy-builder/types";

/** LTP from loaded chain rows for strike + right (undefined if missing or zero). */
export function premiumFromChainRow(
  rows: ChainRow[],
  strike: number,
  right: OptionRight,
): number | undefined {
  const row = rows.find((r) => r.strike_price === strike);
  if (!row) return undefined;
  const cell = right === "Call" ? row.call : row.put;
  if (!cell || typeof cell !== "object") return undefined;
  const premStr = ltpAsOrderPrice((cell as Record<string, unknown>).ltp);
  const n = parseFloat(premStr);
  return Number.isFinite(n) && n > 0 ? n : undefined;
}

/** Order-book buy:sell ratio from loaded chain rows for strike + right, if known. */
export function buySellRatioFromChainRow(
  rows: ChainRow[],
  strike: number,
  right: OptionRight,
): number | string | null {
  const row = rows.find((r) => r.strike_price === strike);
  if (!row) return null;
  const cell = right === "Call" ? row.call : row.put;
  if (!cell || typeof cell !== "object") return null;
  const raw = (cell as Record<string, unknown>).buy_sell_ratio;
  return raw == null ? null : (raw as number | string);
}

export function strikesFromChain(rows: ChainRow[]): number[] {
  return rows
    .map((r) => r.strike_price)
    .filter((k) => Number.isFinite(k));
}

export function createBlankLeg(
  atmStrike: number | null,
  strikes: number[],
): StrategyLeg {
  const strike = atmStrike ?? strikes[0] ?? 0;
  return {
    id: `leg-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`,
    right: "Call",
    side: "Buy",
    strike,
    lots: 1,
    premiumPerUnit: undefined,
  };
}
