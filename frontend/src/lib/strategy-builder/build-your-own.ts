import { ltpAsOrderPrice } from "@/lib/order-confirm";
import type {
  ChainRow,
  OrderSide,
  OptionRight,
  StrategyLeg,
} from "@/lib/strategy-builder/types";

/** Strike + right + order side — for Build Your Own chain ticks vs duplicate B/S. */
export function buildYourOwnSlotKey(
  stock: string,
  expiry: string,
  strike: number,
  right: OptionRight,
  side: OrderSide,
): string {
  return `${stock.trim().toUpperCase()}|${expiry.trim()}|${strike}|${right}|${side}`;
}

export function buildYourOwnAddedSlots(
  legs: StrategyLeg[],
  stockCode: string,
  expiryDate: string,
): Set<string> {
  const slots = new Set<string>();
  for (const leg of legs) {
    slots.add(
      buildYourOwnSlotKey(stockCode, expiryDate, leg.strike, leg.right, leg.side),
    );
  }
  return slots;
}

export function appendLegFromChainRow(
  legs: StrategyLeg[],
  side: OrderSide,
  row: ChainRow,
  right: OptionRight,
): StrategyLeg[] {
  const apiLeg = right === "Call" ? row.call : row.put;
  if (!apiLeg) return legs;
  const premStr = ltpAsOrderPrice(apiLeg.ltp);
  const premNum = parseFloat(premStr);
  return [
    ...legs,
    {
      id: `leg-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`,
      right,
      side,
      strike: row.strike_price,
      lots: 1,
      premiumPerUnit: Number.isFinite(premNum) ? premNum : undefined,
    },
  ];
}
