"use client";

import { useCallback, useMemo, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { apiClient } from "@/lib/api-client";
import {
  parseElmFromResponse,
  parsePositionsNettingFromResponse,
  parseSpanMarginFromResponse,
} from "@/lib/strategy-builder/leg-ui-helpers";
import type {
  BasketElmInfo,
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

/** Netting fields for the whole-basket margin call -- see
 * docs/strategy-builder-portfolio-margin-plan.md (D1-D10). `standaloneSpan`
 * falls back to `span` itself when the server did not net (no open positions,
 * or netting unavailable) so `span === standaloneSpan` exactly reproduces
 * pre-netting behaviour for every downstream computation. */
export type PositionsNettingInfo = {
  standaloneSpan: number;
  positionsMarginBenefit: number | null;
  nettedAgainstPositions: boolean;
  nettedPositionCount: number;
  nettingUnavailableReason: string | null;
};

/** Same as fetchRealMargin but also reads the basket-level ELM and portfolio-netting
 * fields from the response. Only call this for the whole-basket request — ELM and
 * positions netting are basket-level only, never per-leg. */
async function fetchRealMarginWithElm(
  legs: MarginApiRequest["legs"],
  spot: number | null,
  signal?: AbortSignal,
): Promise<{ span: number } & BasketElmInfo & PositionsNettingInfo> {
  const res = await apiClient.post<MarginApiResponse, MarginApiRequest>(
    "/strategy-builder/margin",
    { legs, margin_source: "breeze_api", spot: spot ?? undefined },
    { signal },
  );
  const span = parseSpanMarginFromResponse(res);
  if (span == null) {
    throw new Error(res.Error || "ICICI did not return a margin figure");
  }
  const netting = parsePositionsNettingFromResponse(res);
  return {
    span,
    ...parseElmFromResponse(res),
    standaloneSpan: netting.standaloneSpan ?? span,
    positionsMarginBenefit: netting.positionsMarginBenefit,
    nettedAgainstPositions: netting.nettedAgainstPositions,
    nettedPositionCount: netting.nettedPositionCount,
    nettingUnavailableReason: netting.nettingUnavailableReason,
  };
}

export type OnDemandMarginData = {
  perLegMargin: Record<string, number>;
  spanMargin: number;
  /** Intra-structure netting benefit (this basket's own legs netted against each
   * other) -- sumStandalone minus the basket's OWN standalone margin. Unrelated
   * to positionsMarginBenefit below; see the parsePositionsNettingFromResponse
   * doc comment for why these must not be conflated. */
  marginBenefit: number;
} & BasketElmInfo &
  PositionsNettingInfo;

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
    spot: number | null;
  },
  signal?: AbortSignal,
): Promise<OnDemandMarginData> {
  const { legs, spot, ...ctx } = params;
  const activeLegs = legs.filter((l) => l.lots > 0);
  const sellLegs = activeLegs.filter((l) => l.side === "Sell");

  const [perLegPairs, basket] = await Promise.all([
    Promise.all(
      sellLegs.map(async (leg): Promise<readonly [string, number]> => {
        const margin = await fetchRealMargin(
          [buildMarginLegPayload(leg, ctx)],
          signal,
        );
        return [leg.id, margin] as const;
      }),
    ),
    fetchRealMarginWithElm(
      activeLegs.map((l) => buildMarginLegPayload(l, ctx)),
      spot,
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
  // Intra-structure benefit compares like with like: sum of standalone SELL legs
  // vs the basket's OWN standalone margin -- NOT basket.span, which becomes the
  // incremental (netted-against-positions) figure once netting applies and would
  // silently produce a meaningless number here (see PositionsNettingInfo doc).
  const marginBenefit = Math.max(0, sumStandalone - basket.standaloneSpan);

  return {
    perLegMargin,
    spanMargin: basket.span,
    marginBenefit,
    standaloneSpan: basket.standaloneSpan,
    positionsMarginBenefit: basket.positionsMarginBenefit,
    nettedAgainstPositions: basket.nettedAgainstPositions,
    nettedPositionCount: basket.nettedPositionCount,
    nettingUnavailableReason: basket.nettingUnavailableReason,
    elmRequirement: basket.elmRequirement,
    elmIsIndex: basket.elmIsIndex,
    elmApproximate: basket.elmApproximate,
  };
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
  spot: number | null;
}) {
  const { legs, lotSize, stockCode, exchangeCode, expiryDate, spot } = params;
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
        spot,
      }),
    onSuccess: (data, vars) => {
      setLastResult({ forKey: vars.key, ...data });
    },
  });

  const calculate = useCallback(() => {
    mutation.mutate({ key: currentKey, legs });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentKey, legs]);

  /**
   * Calculate for an explicit legs array rather than the hook's current prop.
   * Used right after a margin-scale applies new lots, so the confirm-recalc runs
   * against the scaled legs without waiting for the render that carries them in.
   */
  const calculateFor = useCallback((legsArg: StrategyLeg[]) => {
    mutation.mutate({ key: computeMarginsCalcKey(legsArg), legs: legsArg });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /** Seeds the calc state from data the caller already has (e.g. a selected
   * propose-trades card's own margin fields) without a round-trip call.
   * `standaloneSpan` defaults to `spanMargin` -- the same "no netting known"
   * convention `fetchRealMarginWithElm` uses -- so a caller that hasn't been
   * updated for netting yet (Phase 3) still produces a consistent state. */
  const prefillMargin = useCallback(
    (data: Partial<OnDemandMarginData> & { spanMargin: number }, forKey: string) => {
      setLastResult({
        forKey,
        perLegMargin: data.perLegMargin ?? {},
        spanMargin: data.spanMargin,
        marginBenefit: data.marginBenefit ?? 0,
        standaloneSpan: data.standaloneSpan ?? data.spanMargin,
        positionsMarginBenefit: data.positionsMarginBenefit ?? null,
        nettedAgainstPositions: data.nettedAgainstPositions ?? false,
        nettedPositionCount: data.nettedPositionCount ?? 0,
        nettingUnavailableReason: data.nettingUnavailableReason ?? null,
        elmRequirement: data.elmRequirement ?? null,
        elmIsIndex: data.elmIsIndex ?? false,
        elmApproximate: data.elmApproximate ?? false,
      });
    },
    [],
  );

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
      standaloneSpan: isFresh ? lastResult!.standaloneSpan : null,
      positionsMarginBenefit: isFresh ? lastResult!.positionsMarginBenefit : null,
      nettedAgainstPositions: isFresh ? lastResult!.nettedAgainstPositions : false,
      nettedPositionCount: isFresh ? lastResult!.nettedPositionCount : 0,
      nettingUnavailableReason: isFresh ? lastResult!.nettingUnavailableReason : null,
      elmRequirement: isFresh ? lastResult!.elmRequirement : null,
      elmIsIndex: isFresh ? lastResult!.elmIsIndex : false,
      elmApproximate: isFresh ? lastResult!.elmApproximate : false,
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
    calculateFor,
    calculateDisabled: !canCalculate || mutation.isPending,
    prefillMargin,
  };
}
