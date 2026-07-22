import type { OptionRight, OrderSide } from "@/lib/strategy-builder/types";

export function normSide(raw: string): OrderSide | null {
  const t = raw.trim().toLowerCase();
  if (t === "buy") return "Buy";
  if (t === "sell") return "Sell";
  return null;
}

export function normRight(raw: string): OptionRight | null {
  const t = raw.trim().toLowerCase();
  if (t === "put" || t === "p" || t === "pe") return "Put";
  if (t === "call" || t === "c" || t === "ce") return "Call";
  return null;
}
