import type {
  BasketLegMarginEntry,
  StrategyLeg,
} from "@/lib/strategy-builder/types";
import type { MarginApiResponse } from "@/lib/strategy-builder/types";
import { formatIndianMoneyCompact } from "@/lib/format-money-in";

export function parseNum(v: unknown): number {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string") {
    const n = parseFloat(v.replace(/,/g, ""));
    return Number.isFinite(n) ? n : NaN;
  }
  return NaN;
}

export function legsQtySignature(legs: StrategyLeg[]): string {
  return JSON.stringify(legs.map((l) => [l.id, l.lots]));
}

export function snapQuantityToLotMultiple(qty: number, lotSize: number): number {
  if (!Number.isFinite(qty) || lotSize <= 0) return Math.max(lotSize, 1);
  const lots = Math.max(1, Math.round(qty / lotSize));
  return lots * lotSize;
}

export function parseSpanMarginFromResponse(
  m: MarginApiResponse | undefined,
): number | null {
  if (m?.Status !== 200 || !m.Success) return null;
  const v = parseNum(
    (m.Success as { span_margin_required?: unknown }).span_margin_required,
  );
  return Number.isFinite(v) ? v : null;
}

export function parseElmFromResponse(
  m: MarginApiResponse | undefined,
): { elmRequirement: number | null; elmIsIndex: boolean; elmApproximate: boolean } {
  const empty = { elmRequirement: null, elmIsIndex: false, elmApproximate: false };
  if (m?.Status !== 200 || !m.Success) return empty;
  const success = m.Success as {
    elm_requirement?: unknown;
    elm_is_index?: unknown;
    elm_approximate?: unknown;
  };
  const v = parseNum(success.elm_requirement);
  return {
    elmRequirement: Number.isFinite(v) ? v : null,
    elmIsIndex: success.elm_is_index === true,
    elmApproximate: success.elm_approximate === true,
  };
}

/**
 * Canonical leg label used across Portfolio, Orders, Basket Order and Strategy
 * Builder: "STOCK.DD-Mon-YYYY.STRIKE" (e.g. "BSESEN.09-Jul-2026.82000"). Put/Call
 * is intentionally omitted — render it separately (e.g. via a type badge) since a
 * straddle/strangle can otherwise produce two identical labels for the same strike.
 */
export function formatOptionSymbolLabel(
  stock: string,
  expiryDisplay: string,
  strike: number,
): string {
  const strikeSeg = Number.isFinite(strike) ? String(Math.round(strike)) : "";
  return [stock.trim(), expiryDisplay.trim(), strikeSeg]
    .filter(Boolean)
    .join(".");
}

export function formatNetPremiumCompactInr(v: number): string {
  if (!Number.isFinite(v)) return "—";
  return formatIndianMoneyCompact(v);
}

export function formatBuySellRatio(
  raw: number | string | null | undefined,
): string {
  if (raw == null || raw === "NA") return "—";
  const n = typeof raw === "number" ? raw : parseFloat(String(raw));
  return Number.isFinite(n) ? n.toFixed(1) : "—";
}

/** Signed premium display: Sell is a credit ("+", green); Buy is a debit ("-", red). */
export function formatSignedLegPremium(
  premTotal: number | null,
  side: "Buy" | "Sell",
): { text: string; toneClass: string } {
  if (premTotal == null) return { text: "—", toneClass: "text-foreground" };
  const sign = side === "Sell" ? "+" : "-";
  const toneClass = side === "Sell" ? "text-up" : "text-down";
  return { text: `${sign}${formatIndianMoneyCompact(premTotal)}`, toneClass };
}

export function formatLegMargin(
  leg: StrategyLeg,
  entry: BasketLegMarginEntry | undefined,
  spanBaselineLoading: boolean,
): string {
  if (leg.lots <= 0) return "—";
  if (spanBaselineLoading || entry?.loading) return "…";
  if (entry?.span != null && Number.isFinite(entry.span)) {
    return formatIndianMoneyCompact(entry.span);
  }
  return "—";
}
