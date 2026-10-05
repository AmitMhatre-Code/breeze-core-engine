"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
  MarginScope,
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
  marginScope: MarginScope,
  netAgainstPositions: boolean,
  signal?: AbortSignal,
): Promise<number> {
  const res = await apiClient.post<MarginApiResponse, MarginApiRequest>(
    "/strategy-builder/margin",
    { legs, margin_scope: marginScope, net_against_positions: netAgainstPositions },
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
  /** Open option positions in the underlying (any expiry), reported whether or not
   * this call netted against them; null when the server could not load them. */
  openPositionsInUnderlying: number | null;
};

/** Same as fetchRealMargin but also reads the basket-level ELM and portfolio-netting
 * fields from the response. Only call this for the whole-basket request — ELM and
 * positions netting are basket-level only, never per-leg. */
async function fetchRealMarginWithElm(
  legs: MarginApiRequest["legs"],
  spot: number | null,
  marginScope: MarginScope,
  netAgainstPositions: boolean,
  signal?: AbortSignal,
): Promise<{ span: number } & BasketElmInfo & PositionsNettingInfo> {
  const res = await apiClient.post<MarginApiResponse, MarginApiRequest>(
    "/strategy-builder/margin",
    {
      legs,
      margin_scope: marginScope,
      spot: spot ?? undefined,
      net_against_positions: netAgainstPositions,
    },
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
    openPositionsInUnderlying: parseOpenPositionsCount(res),
  };
}

function parseOpenPositionsCount(res: MarginApiResponse): number | null {
  const v = (res.Success as { open_positions_in_underlying?: unknown } | null | undefined)
    ?.open_positions_in_underlying;
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

export type OnDemandMarginData = {
  perLegMargin: Record<string, number>;
  spanMargin: number;
  /** Intra-structure netting benefit (this basket's own legs netted against each
   * other) -- the per-leg figures summed minus the basket's own, both on the same
   * netted-or-not basis (see fetchRealBasketMargins). Unrelated
   * to positionsMarginBenefit below; see the parsePositionsNettingFromResponse
   * doc comment for why these must not be conflated. */
  marginBenefit: number;
} & BasketElmInfo &
  PositionsNettingInfo;

/**
 * Real ICICI margin has no per-leg breakdown, so this fans out one call per
 * Sell leg plus one call for the whole basket, then derives the benefit
 * client-side. `netAgainstPositions` applies to every call alike. Buy legs are never sent — their standalone margin is
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
    /** Defaults to "app" (the server's default too). */
    marginScope?: MarginScope;
    /** Defaults to true (the server's default too). */
    netAgainstPositions?: boolean;
  },
  signal?: AbortSignal,
): Promise<OnDemandMarginData> {
  const { legs, spot, marginScope = "app", netAgainstPositions = true, ...ctx } = params;
  const activeLegs = legs.filter((l) => l.lots > 0);
  const sellLegs = activeLegs.filter((l) => l.side === "Sell");

  const [perLegPairs, basket] = await Promise.all([
    Promise.all(
      sellLegs.map(async (leg): Promise<readonly [string, number]> => {
        const margin = await fetchRealMargin(
          [buildMarginLegPayload(leg, ctx)],
          marginScope,
          netAgainstPositions,
          signal,
        );
        return [leg.id, margin] as const;
      }),
    ),
    fetchRealMarginWithElm(
      activeLegs.map((l) => buildMarginLegPayload(l, ctx)),
      spot,
      marginScope,
      netAgainstPositions,
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

  const sumLegs = Object.values(perLegMargin).reduce((a, b) => a + b, 0);
  // Intra-structure benefit compares like with like. The per-leg calls go through
  // the same endpoint as the basket call, so when the server nets against open
  // positions each SELL leg's figure is its own increment over those positions --
  // and the basket's comparable figure is basket.span (its increment), not
  // standaloneSpan. Without netting, span === standaloneSpan and nothing changes.
  const marginBenefit = Math.max(0, sumLegs - basket.span);

  return {
    perLegMargin,
    spanMargin: basket.span,
    marginBenefit,
    standaloneSpan: basket.standaloneSpan,
    positionsMarginBenefit: basket.positionsMarginBenefit,
    nettedAgainstPositions: basket.nettedAgainstPositions,
    nettedPositionCount: basket.nettedPositionCount,
    nettingUnavailableReason: basket.nettingUnavailableReason,
    openPositionsInUnderlying: basket.openPositionsInUnderlying,
    elmRequirement: basket.elmRequirement,
    elmIsIndex: basket.elmIsIndex,
    elmApproximate: basket.elmApproximate,
  };
}

/**
 * Whole-basket margin only — one ICICI call, no per-leg fan-out. For sizing
 * probes, where only the basket total (and its ELM) matters.
 */
export async function fetchBasketMarginOnly(
  params: {
    legs: StrategyLeg[];
    stockCode: string;
    exchangeCode: string;
    expiryDate: string;
    lotSize: number;
    spot: number | null;
    marginScope: MarginScope;
    /** Defaults to true (the server's default too). */
    netAgainstPositions?: boolean;
  },
  signal?: AbortSignal,
): Promise<{ span: number; elmRequirement: number | null }> {
  const { legs, spot, marginScope, netAgainstPositions = true, ...ctx } = params;
  const basket = await fetchRealMarginWithElm(
    legs.filter((l) => l.lots > 0).map((l) => buildMarginLegPayload(l, ctx)),
    spot,
    marginScope,
    netAgainstPositions,
    signal,
  );
  return { span: basket.span, elmRequirement: basket.elmRequirement };
}

function formatMutationError(err: unknown): string {
  if (err instanceof Error) return err.message;
  return "Failed to calculate margins";
}

type CalculateVars = { key: string; legs: StrategyLeg[]; net: boolean };

/**
 * On-demand (not auto-fetching) real ICICI margin for a legs table. Numbers
 * stay `null` — rendered as "—" by the panels — until `calculate()` is
 * called, and revert to `null` as soon as the legs no longer match the key
 * the last successful calculation was for (any edit except price). Flipping
 * `netAgainstPositions` while figures are showing recalculates them at once.
 */
export function useOnDemandBasketMargin(params: {
  legs: StrategyLeg[];
  lotSize: number;
  stockCode: string;
  exchangeCode: string;
  expiryDate: string;
  spot: number | null;
  /** Which "use the SPAN file" setting governs this page's margins. */
  marginScope: MarginScope;
  /** Net against open positions in the underlying. Defaults to true. */
  netAgainstPositions?: boolean;
}) {
  const {
    legs,
    lotSize,
    stockCode,
    exchangeCode,
    expiryDate,
    spot,
    marginScope,
    netAgainstPositions = true,
  } = params;
  const [lastResult, setLastResult] = useState<
    (OnDemandMarginData & { forKey: string; net: boolean }) | null
  >(null);
  // The open-position count belongs to the underlying, not the legs, so it outlives a
  // leg edit -- the toggle that depends on it must not vanish while figures recalculate.
  const underlyingKey = `${stockCode.trim()}|${exchangeCode}`;
  const [positionsCount, setPositionsCount] = useState<{
    underlyingKey: string;
    count: number;
  } | null>(null);
  const netRef = useRef(netAgainstPositions);
  netRef.current = netAgainstPositions;

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
        marginScope,
        netAgainstPositions: vars.net,
      }),
    onSuccess: (data, vars) => {
      setLastResult({ forKey: vars.key, net: vars.net, ...data });
      if (data.openPositionsInUnderlying != null) {
        setPositionsCount({ underlyingKey, count: data.openPositionsInUnderlying });
      }
    },
  });

  const calculate = useCallback(() => {
    mutation.mutate({ key: currentKey, legs, net: netAgainstPositions });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentKey, legs, netAgainstPositions]);

  // Flipping the netting toggle recalculates only when figures for these exact legs
  // are on screen; otherwise the next Calculate picks the new setting up.
  const prevNetRef = useRef(netAgainstPositions);
  useEffect(() => {
    if (prevNetRef.current === netAgainstPositions) return;
    prevNetRef.current = netAgainstPositions;
    if (lastResult != null && lastResult.forKey === currentKey && !mutation.isPending) {
      mutation.mutate({ key: currentKey, legs, net: netAgainstPositions });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [netAgainstPositions]);

  /**
   * Calculate for an explicit legs array rather than the hook's current prop.
   * Used right after a margin-scale applies new lots, so the confirm-recalc runs
   * against the scaled legs without waiting for the render that carries them in.
   */
  const calculateFor = useCallback((legsArg: StrategyLeg[]) => {
    mutation.mutate({
      key: computeMarginsCalcKey(legsArg),
      legs: legsArg,
      net: netRef.current,
    });
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
        // Seeded figures (propose-trades cards) are always computed netted server-side.
        net: true,
        perLegMargin: data.perLegMargin ?? {},
        spanMargin: data.spanMargin,
        marginBenefit: data.marginBenefit ?? 0,
        standaloneSpan: data.standaloneSpan ?? data.spanMargin,
        positionsMarginBenefit: data.positionsMarginBenefit ?? null,
        nettedAgainstPositions: data.nettedAgainstPositions ?? false,
        nettedPositionCount: data.nettedPositionCount ?? 0,
        nettingUnavailableReason: data.nettingUnavailableReason ?? null,
        openPositionsInUnderlying: data.openPositionsInUnderlying ?? null,
        elmRequirement: data.elmRequirement ?? null,
        elmIsIndex: data.elmIsIndex ?? false,
        elmApproximate: data.elmApproximate ?? false,
      });
    },
    [],
  );

  const isFresh =
    lastResult != null &&
    lastResult.forKey === currentKey &&
    lastResult.net === netAgainstPositions;

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
      openPositionsInUnderlying:
        positionsCount != null && positionsCount.underlyingKey === underlyingKey
          ? positionsCount.count
          : null,
      elmRequirement: isFresh ? lastResult!.elmRequirement : null,
      elmIsIndex: isFresh ? lastResult!.elmIsIndex : false,
      elmApproximate: isFresh ? lastResult!.elmApproximate : false,
    }),
    [activeLegs.length, mutation.isPending, isFresh, lastResult, positionsCount, underlyingKey],
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
