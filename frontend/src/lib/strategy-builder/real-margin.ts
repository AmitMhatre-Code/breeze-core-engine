"use client";

import { useCallback, useMemo, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { apiClient } from "@/lib/api-client";
import { parseSpanMarginFromResponse } from "@/lib/strategy-builder/leg-ui-helpers";
import type {
  BasketLegMarginEntry,
  MarginApiRequest,
  MarginApiResponse,
  StrategyLeg,
} from "@/lib/strategy-builder/types";

type MarginLegContext = {
  stockCode: string;
  exchangeCode: string;
  expiryDate: string;
  lotSize: number;
};

/** Same signature as the exchange-baseline invalidation key this replaces: strike/type/position/quantity, not price. */
export function computeMarginsCalcKey(legs: StrategyLeg[]): string {
  return JSON.stringify(
    legs
      .filter((l) => l.lots > 0)
      .map((l) => [l.id, l.strike, l.right, l.side, l.lots]),
  );
}

function buildMarginLegPayload(
  leg: StrategyLeg,
  ctx: MarginLegContext,
): MarginApiRequest["legs"][number] {
  return {
    stock_code: ctx.stockCode.trim(),
    exchange_code: ctx.exchangeCode,
    expiry_date: ctx.expiryDate.trim(),
    product_type: "Options",
    right: leg.right,
    strike_price: String(leg.strike),
    quantity: String(Math.round(leg.lots * ctx.lotSize)),
    price: leg.aggressiveLimit ? "0" : String(leg.premiumPerUnit ?? 0),
    action: leg.side,
  };
}

async function fetchRealMargin(
  legs: MarginApiRequest["legs"],
  signal?: AbortSignal,
): Promise<number> {
  const res = await apiClient.post<MarginApiResponse, MarginApiRequest>(
    "/strategy-builder/margin",
    { legs, margin_source: "breeze_api" },
    { signal },
  );
  const v = parseSpanMarginFromResponse(res);
  if (v == null) {
    throw new Error(res.Error || "ICICI did not return a margin figure");
  }
  return v;
}

export type OnDemandMarginData = {
  perLegMargin: Record<string, number>;
  spanMargin: number;
  marginBenefit: number;
};

/**
 * Real ICICI margin has no per-leg breakdown, so this fans out one call per
 * Sell leg (standalone) plus one call for the whole basket, then derives the
 * benefit client-side. Buy legs are never sent — their standalone margin is
 * always 0.
 */
export async function fetchRealBasketMargins(
  params: {
    legs: StrategyLeg[];
    stockCode: string;
    exchangeCode: string;
    expiryDate: string;
    lotSize: number;
  },
  signal?: AbortSignal,
): Promise<OnDemandMarginData> {
  const { legs, ...ctx } = params;
  const activeLegs = legs.filter((l) => l.lots > 0);
  const sellLegs = activeLegs.filter((l) => l.side === "Sell");

  const [perLegPairs, spanMargin] = await Promise.all([
    Promise.all(
      sellLegs.map(async (leg): Promise<readonly [string, number]> => {
        const margin = await fetchRealMargin(
          [buildMarginLegPayload(leg, ctx)],
          signal,
        );
        return [leg.id, margin] as const;
      }),
    ),
    fetchRealMargin(
      activeLegs.map((l) => buildMarginLegPayload(l, ctx)),
      signal,
    ),
  ]);

  const perLegMargin: Record<string, number> = {};
  for (const l of activeLegs) {
    if (l.side === "Buy") perLegMargin[l.id] = 0;
  }
  for (const [id, margin] of perLegPairs) {
    perLegMargin[id] = margin;
  }

  const sumStandalone = Object.values(perLegMargin).reduce((a, b) => a + b, 0);
  const marginBenefit = Math.max(0, sumStandalone - spanMargin);

  return { perLegMargin, spanMargin, marginBenefit };
}

function formatMutationError(err: unknown): string {
  if (err instanceof Error) return err.message;
  return "Failed to calculate margins";
}

type CalculateVars = { key: string; legs: StrategyLeg[] };

/**
 * On-demand (not auto-fetching) real ICICI margin for a legs table. Numbers
 * stay `null` — rendered as "—" by the panels — until `calculate()` is
 * called, and revert to `null` as soon as the legs no longer match the key
 * the last successful calculation was for (any edit except price).
 */
export function useOnDemandBasketMargin(params: {
  legs: StrategyLeg[];
  lotSize: number;
  stockCode: string;
  exchangeCode: string;
  expiryDate: string;
}) {
  const { legs, lotSize, stockCode, exchangeCode, expiryDate } = params;
  const [lastResult, setLastResult] = useState<
    (OnDemandMarginData & { forKey: string }) | null
  >(null);

  const currentKey = useMemo(() => computeMarginsCalcKey(legs), [legs]);
  const activeLegs = useMemo(() => legs.filter((l) => l.lots > 0), [legs]);

  const mutation = useMutation({
    mutationFn: (vars: CalculateVars) =>
      fetchRealBasketMargins({
        legs: vars.legs,
        stockCode,
        exchangeCode,
        expiryDate,
        lotSize,
      }),
    onSuccess: (data, vars) => {
      setLastResult({ forKey: vars.key, ...data });
    },
  });

  const calculate = useCallback(() => {
    mutation.mutate({ key: currentKey, legs });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentKey, legs]);

  const prefillSpanMargin = useCallback((spanMargin: number, forKey: string) => {
    setLastResult({ forKey, perLegMargin: {}, spanMargin, marginBenefit: 0 });
  }, []);

  const isFresh = lastResult != null && lastResult.forKey === currentKey;

  const legMargins = useMemo(() => {
    const map: Record<string, BasketLegMarginEntry> = {};
    for (const leg of legs) {
      const span =
        leg.lots > 0 && isFresh ? (lastResult!.perLegMargin[leg.id] ?? null) : null;
      map[leg.id] = { lots: leg.lots, span, loading: mutation.isPending };
    }
    return map;
  }, [legs, isFresh, lastResult, mutation.isPending]);

  const totalsMargin = useMemo(
    () => ({
      hasPositiveLots: activeLegs.length > 0,
      isFetching: mutation.isPending,
      netMargin: isFresh ? lastResult!.spanMargin : null,
      marginBenefit:
        isFresh && Object.keys(lastResult!.perLegMargin).length > 0
          ? lastResult!.marginBenefit
          : null,
    }),
    [activeLegs.length, mutation.isPending, isFresh, lastResult],
  );

  const canCalculate =
    activeLegs.length > 0 &&
    stockCode.trim().length > 0 &&
    expiryDate.trim().length > 0;

  return {
    legMargins,
    totalsMargin,
    error: mutation.isError ? formatMutationError(mutation.error) : null,
    isCalculating: mutation.isPending,
    calculate,
    calculateDisabled: !canCalculate || mutation.isPending,
    prefillSpanMargin,
  };
}
