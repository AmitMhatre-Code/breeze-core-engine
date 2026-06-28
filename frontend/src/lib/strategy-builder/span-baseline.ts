import { apiClient } from "@/lib/api-client";
import type {
  OptionRight,
  SpanBaselineSheet,
  StrategyLeg,
} from "@/lib/strategy-builder/types";

export function contractKey(strike: number, right: OptionRight): string {
  return `${strike}:${right === "Call" ? "CE" : "PE"}`;
}

export async function fetchSpanBaselineSheet(
  exchangeCode: string,
  stockCode: string,
  expiryDate: string,
  signal?: AbortSignal,
): Promise<SpanBaselineSheet> {
  const params = new URLSearchParams({
    exchange_code: exchangeCode,
    stock_code: stockCode.trim(),
    expiry_date: expiryDate.trim(),
  });
  return apiClient.get<SpanBaselineSheet>(
    `/strategy-builder/span-baseline?${params.toString()}`,
    signal,
  );
}

export function computeLegSpanMargin(
  sheet: SpanBaselineSheet | undefined,
  leg: StrategyLeg,
  lotSize: number,
): number | null {
  if (!sheet?.found || leg.lots <= 0 || lotSize <= 0) return null;
  const qty = Math.round(leg.lots * lotSize);
  const entry = sheet.contracts[contractKey(leg.strike, leg.right)];
  if (
    !entry ||
    !Number.isFinite(entry.margin_per_lot) ||
    !Number.isFinite(entry.lot_size) ||
    entry.lot_size <= 0
  ) {
    return null;
  }
  const lots = Math.max(1, Math.ceil(qty / entry.lot_size));
  return entry.margin_per_lot * lots;
}

export function computeNetSpanMargin(
  sheet: SpanBaselineSheet | undefined,
  legs: StrategyLeg[],
  lotSize: number,
): number | null {
  const active = legs.filter((l) => l.lots > 0);
  if (!active.length) return null;
  if (!sheet?.found) return null;
  let sum = 0;
  for (const leg of active) {
    const margin = computeLegSpanMargin(sheet, leg, lotSize);
    if (margin == null || !Number.isFinite(margin)) return null;
    sum += margin;
  }
  return sum;
}

export function buildLegMarginsFromSheet(
  sheet: SpanBaselineSheet | undefined,
  legs: StrategyLeg[],
  lotSize: number,
  loading: boolean,
): Record<string, { lots: number; span: number | null; loading?: boolean }> {
  const map: Record<string, { lots: number; span: number | null; loading?: boolean }> =
    {};
  for (const leg of legs) {
    if (leg.lots <= 0) continue;
    map[leg.id] = {
      lots: leg.lots,
      span: loading ? null : computeLegSpanMargin(sheet, leg, lotSize),
      loading,
    };
  }
  return map;
}
