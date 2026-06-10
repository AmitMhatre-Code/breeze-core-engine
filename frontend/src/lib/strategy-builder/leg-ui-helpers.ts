import type { StrategyLeg } from "@/lib/strategy-builder/types";
import type { MarginApiResponse } from "@/lib/strategy-builder/types";

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

export function formatOptionSymbolLabel(
  stock: string,
  expiryDisplay: string,
  strike: number,
  right: "Call" | "Put",
): string {
  const abbr = right === "Put" ? "PE" : "CE";
  const strikeSeg = `${strike.toLocaleString("en-IN")} ${abbr}`;
  if (stock && expiryDisplay) {
    return `${stock} ${expiryDisplay} ${strikeSeg}`;
  }
  if (stock) return `${stock} ${strikeSeg}`;
  return strikeSeg;
}

export function formatNetPremiumCompactInr(v: number): string {
  if (!Number.isFinite(v)) return "—";
  const abs = Math.abs(v).toLocaleString("en-IN", { maximumFractionDigits: 0 });
  return v < 0 ? `(₹${abs})` : `₹${abs}`;
}
