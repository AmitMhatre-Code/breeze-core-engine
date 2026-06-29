import { apiClient } from "@/lib/api-client";
import { normalizeStrikeKey } from "@/lib/strategy-builder/format-strike";
import type {
  BasketLegMarginEntry,
  OptionRight,
  SpanBaselineSheet,
  SpanPortfolioMarginResponse,
  SpanPortfolioMarginSuccess,
  StrategyLeg,
} from "@/lib/strategy-builder/types";

export function contractKey(strike: number, right: OptionRight): string {
  return `${normalizeStrikeKey(strike)}:${right === "Call" ? "CE" : "PE"}`;
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

export async function fetchSpanPortfolioMargin(
  params: {
    exchange_code: string;
    stock_code: string;
    expiry_date: string;
    legs: {
      strike_price: string;
      right: OptionRight;
      action: StrategyLeg["side"];
      quantity: string;
    }[];
    spot: number;
    iv?: number | null;
  },
  signal?: AbortSignal,
): Promise<SpanPortfolioMarginResponse> {
  return apiClient.post<SpanPortfolioMarginResponse>(
    "/strategy-builder/span-portfolio-margin",
    {
      exchange_code: params.exchange_code,
      stock_code: params.stock_code.trim(),
      expiry_date: params.expiry_date.trim(),
      legs: params.legs,
      spot: params.spot,
      iv: params.iv ?? undefined,
    },
    {signal: signal},
  );
}

export function computeLegSpanMargin(
  sheet: SpanBaselineSheet | undefined,
  leg: StrategyLeg,
  lotSize: number,
): number | null {
  if (!sheet?.found || leg.lots <= 0 || lotSize <= 0) return null;
  if (leg.side === "Buy") return 0;
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

/** Fallback when portfolio API unavailable: sum per-leg standalone (sell only). */
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
): Record<string, BasketLegMarginEntry> {
  const map: Record<string, BasketLegMarginEntry> = {};
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

export function buildLegMarginsFromPortfolio(
  sheet: SpanBaselineSheet | undefined,
  legs: StrategyLeg[],
  lotSize: number,
  portfolio: SpanPortfolioMarginSuccess | null | undefined,
  loading: boolean,
): Record<string, BasketLegMarginEntry> {
  const active = legs.filter((l) => l.lots > 0);
  const map: Record<string, BasketLegMarginEntry> = {};
  for (let idx = 0; idx < active.length; idx++) {
    const leg = active[idx]!;
    let span: number | null = null;
    if (!loading) {
      if (leg.side === "Buy") {
        span = 0;
      } else if (portfolio?.per_leg_standalone) {
        const v = portfolio.per_leg_standalone[String(idx)];
        if (v != null && Number.isFinite(v)) {
          span = v;
        } else {
          span = computeLegSpanMargin(sheet, leg, lotSize);
        }
      } else {
        span = computeLegSpanMargin(sheet, leg, lotSize);
      }
    }
    map[leg.id] = { lots: leg.lots, span, loading };
  }
  for (const leg of legs) {
    if (leg.lots <= 0 && !map[leg.id]) {
      map[leg.id] = { lots: leg.lots, span: null, loading };
    }
  }
  return map;
}

export function portfolioMarginFromResponse(
  res: SpanPortfolioMarginResponse | undefined,
  fallback: number | null,
): number | null {
  const v = res?.Success?.span_margin_required;
  if (v != null && Number.isFinite(v)) return v;
  return fallback;
}
