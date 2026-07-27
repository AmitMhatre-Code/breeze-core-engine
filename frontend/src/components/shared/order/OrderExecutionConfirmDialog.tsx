"use client";

import type { UseQueryResult } from "@tanstack/react-query";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useMemo, useState } from "react";
import { ChunkSizeOrderField } from "@/components/order/ChunkSizeOrderField";
import { OptionTypeBadge } from "@/components/shared/badges/OptionTypeBadge";
import { OrderSideBadge } from "@/components/shared/badges/OrderSideBadge";
import { AsyncLabelSpan } from "@/components/ui/AsyncLabelSpan";
import { apiClient } from "@/lib/api-client";
import {
  resolveAggressivePrices,
  type AggressiveOrderMode,
  type AggressivePriceResultItem,
} from "@/lib/aggressive-order";
import type { BreakChunkDefaultsResponse } from "@/lib/break-chunk-defaults";
import { formatIndianMoneyCompact } from "@/lib/format-money-in";
import { runBreakOrderChunks } from "@/lib/icici-rate-limit-flow";
import { fetchMarketStatus } from "@/lib/market-status";
import { QuoteSourceBadge } from "@/components/shared/market-data/QuoteSourceBadge";
import { HelpLink } from "@/components/help/HelpLink";
import { createParkedOrders, deleteParkedOrdersMany, patchParkedOrder } from "@/lib/parked-orders";
import { randomUuid } from "@/lib/random-uuid";
import { formatOptionSymbolLabel } from "@/lib/strategy-builder/leg-ui-helpers";
import { sb } from "@/lib/strategy-builder/ui";
import type {
  MarginApiResponse,
  OptionRight,
  OrderSide,
  QuoteMeta,
} from "@/lib/strategy-builder/types";
import { useBreakChunkQty } from "@/lib/use-break-chunk-qty";
import { invalidateTradingShellQueries } from "@/lib/trading-cache";
import { useRateLimitCountdown } from "@/lib/use-rate-limit-countdown";
import { Modal } from "@/components/ui/Modal";

export type ExecutionPreviewLeg = {
  strike: number;
  right: OptionRight;
  side: OrderSide;
  /** Contract units (not lots). */
  quantity: number;
  premiumPerUnit: number;
  aggressiveLimit?: boolean;
  /**
   * Aggressive execution style when aggressiveLimit is set. Defaults to "market" for backward
   * compatibility. "limit_tolerance" legs are repriced from live LTP server-side before placement.
   */
  aggressiveMode?: AggressiveOrderMode;
  /** Tolerance % for "limit_tolerance" mode (ignored otherwise). */
  aggressiveTolerancePct?: number;
};

/** Is this leg an app-side aggressive-limit (LTP × tolerance) rather than a native market order? */
function isToleranceLeg(l: ExecutionPreviewLeg): boolean {
  return Boolean(l.aggressiveLimit) && (l.aggressiveMode ?? "market") === "limit_tolerance";
}

/** Is this leg a native ICICI market order? */
function isMarketLeg(l: ExecutionPreviewLeg): boolean {
  return Boolean(l.aggressiveLimit) && (l.aggressiveMode ?? "market") === "market";
}

export type ControlledChunkProps = {
  chunkQty: string;
  onChunkQtyChange: (v: string) => void;
  defaultsQuery: UseQueryResult<BreakChunkDefaultsResponse, Error>;
  chunkReady: boolean;
};

export type OrderExecutionConfirmDialogProps = {
  open: boolean;
  onClose: () => void;
  stockCode: string;
  exchangeCode: string;
  expiryDisplay: string;
  legs: ExecutionPreviewLeg[];
  /** When re-confirming from parked rows, delete/replace these after successful place. */
  sourceParkedIds?: string[];
  /** Defaults to Options. */
  productType?: string;
  /** Use host page chunk state instead of internal defaults (Strategy Builder). */
  controlledChunk?: ControlledChunkProps;
  quoteMeta?: QuoteMeta | null;
};

function parseNum(v: unknown): number {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string") {
    const n = parseFloat(v.replace(/,/g, ""));
    return Number.isFinite(n) ? n : NaN;
  }
  return NaN;
}

function parseSpanMarginFromResponse(
  m: MarginApiResponse | undefined,
): number | null {
  if (m?.Status !== 200 || !m.Success) return null;
  const v = parseNum(
    (m.Success as { span_margin_required?: unknown }).span_margin_required,
  );
  return Number.isFinite(v) ? v : null;
}

export function OrderExecutionConfirmDialog({
  open,
  onClose,
  stockCode,
  exchangeCode,
  expiryDisplay,
  legs,
  sourceParkedIds = [],
  productType = "Options",
  controlledChunk,
  quoteMeta = null,
}: OrderExecutionConfirmDialogProps) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const { wait } = useRateLimitCountdown();

  const [progress, setProgress] = useState<{
    legIndex: number;
    totalLegs: number;
    chunkIndex: number;
    totalChunks: number;
    placedQty: number;
    totalQty: number;
  } | null>(null);

  const internalChunk = useBreakChunkQty({
    stockCode,
    exchangeCode,
    expiryDisplay,
    enabled: open && !controlledChunk,
  });

  const chunkQty = controlledChunk?.chunkQty ?? internalChunk.chunkQty;
  const setChunkQty = controlledChunk?.onChunkQtyChange ?? internalChunk.setChunkQty;
  const chunkDefaultsQ =
    controlledChunk?.defaultsQuery ?? internalChunk.defaultsQuery;
  const chunkReady = controlledChunk?.chunkReady ?? internalChunk.chunkReady;

  // Legs that need server-side repricing from live LTP (limit_tolerance mode). Native-market and
  // plain-limit legs keep their price as-is.
  const toleranceLegs = useMemo(
    () =>
      legs
        .map((l, i) => ({ l, i }))
        .filter(({ l }) => isToleranceLeg(l)),
    [legs],
  );
  const toleranceTolerancePct = toleranceLegs[0]?.l.aggressiveTolerancePct ?? 0;
  const toleranceKey = useMemo(
    () =>
      JSON.stringify(
        toleranceLegs.map(({ l, i }) => [
          i,
          l.strike,
          l.right,
          l.side,
          l.aggressiveTolerancePct ?? 0,
        ]),
      ),
    [toleranceLegs],
  );

  const resolvedPricesQueryKey = useMemo(
    () => [
      "order-execution-confirm",
      "aggressive-price",
      stockCode,
      exchangeCode,
      expiryDisplay,
      toleranceKey,
    ],
    [stockCode, exchangeCode, expiryDisplay, toleranceKey],
  );

  // Shared by the on-open estimate query and the re-resolve at Confirm — both must price the
  // exact same legs/tolerance.
  const runResolve = useCallback(
    () =>
      resolveAggressivePrices(
        toleranceLegs.map(({ l, i }) => ({
          ref: String(i),
          stock_code: stockCode,
          exchange_code: exchangeCode,
          expiry_date: expiryDisplay,
          right: l.right,
          strike_price: String(l.strike),
          action: l.side,
        })),
        toleranceTolerancePct,
      ),
    [toleranceLegs, stockCode, exchangeCode, expiryDisplay, toleranceTolerancePct],
  );

  const resolvedPricesQ = useQuery({
    queryKey: resolvedPricesQueryKey,
    queryFn: runResolve,
    enabled:
      open &&
      toleranceLegs.length > 0 &&
      stockCode.trim().length > 0 &&
      expiryDisplay.trim().length > 0,
    staleTime: 4000,
  });

  type PricedLeg = {
    leg: ExecutionPreviewLeg;
    /** Price to send to the order API; null while a tolerance leg is still resolving. */
    placementPrice: string | null;
    /** Whether the order is placed as a native market order (aggressive_limit=true). */
    placementAggressive: boolean;
    /** Per-unit price to display/compute premium from; null when unknown (market / unresolved). */
    displayPrice: number | null;
    resolveError: string | null;
    resolving: boolean;
    isMarket: boolean;
    isTolerance: boolean;
  };

  const pricedLegs: PricedLeg[] = useMemo(() => {
    const map = resolvedPricesQ.data;
    return legs.map((l, i) => {
      if (isToleranceLeg(l)) {
        const r: AggressivePriceResultItem | undefined = map?.get(String(i));
        const price = r?.price != null ? Number(r.price) : null;
        return {
          leg: l,
          placementPrice:
            price != null && Number.isFinite(price) ? String(price) : null,
          placementAggressive: false,
          displayPrice: price != null && Number.isFinite(price) ? price : null,
          resolveError: r?.error ?? null,
          resolving: map == null,
          isMarket: false,
          isTolerance: true,
        };
      }
      if (isMarketLeg(l)) {
        return {
          leg: l,
          placementPrice: "0",
          placementAggressive: true,
          displayPrice: null,
          resolveError: null,
          resolving: false,
          isMarket: true,
          isTolerance: false,
        };
      }
      return {
        leg: l,
        placementPrice: String(l.premiumPerUnit),
        placementAggressive: false,
        displayPrice: l.premiumPerUnit,
        resolveError: null,
        resolving: false,
        isMarket: false,
        isTolerance: false,
      };
    });
  }, [legs, resolvedPricesQ.data]);

  const toleranceResolving =
    toleranceLegs.length > 0 && resolvedPricesQ.isPending;
  const toleranceError = pricedLegs.find((p) => p.resolveError)?.resolveError ?? null;
  const toleranceReady =
    toleranceLegs.length === 0 ||
    (resolvedPricesQ.data != null && !toleranceError);

  const marginLegKey = useMemo(
    () =>
      JSON.stringify(
        pricedLegs.map((p) => [
          p.leg.strike,
          p.leg.right,
          p.leg.side,
          p.leg.quantity,
          p.placementAggressive ? "0" : (p.placementPrice ?? ""),
        ]),
      ),
    [pricedLegs],
  );

  const marginQ = useQuery({
    queryKey: [
      "order-execution-confirm",
      "margin",
      stockCode,
      exchangeCode,
      expiryDisplay,
      marginLegKey,
    ],
    queryFn: () =>
      apiClient.post<MarginApiResponse>("/strategy-builder/margin", {
        legs: pricedLegs.map((p) => ({
          stock_code: stockCode,
          exchange_code: exchangeCode,
          expiry_date: expiryDisplay,
          product_type: productType,
          right: p.leg.right,
          strike_price: String(p.leg.strike),
          quantity: String(Math.round(p.leg.quantity)),
          price: p.placementAggressive ? "0" : (p.placementPrice ?? "0"),
          action: p.leg.side,
        })),
      }),
    enabled:
      open &&
      legs.length > 0 &&
      stockCode.trim().length > 0 &&
      expiryDisplay.trim().length > 0 &&
      toleranceReady,
    staleTime: 5000,
  });

  const marketStatusQ = useQuery({
    queryKey: ["settings", "market-status"],
    queryFn: fetchMarketStatus,
    enabled: open,
    staleTime: 30_000,
    refetchInterval: 60_000,
  });

  const marketStatus = marketStatusQ.data;
  const marketClosed = marketStatus != null && !marketStatus.is_open;
  const executeAllowed = marketStatus?.is_open === true;

  const marginState = marginQ.data;
  const spanMargin = parseSpanMarginFromResponse(marginState);

  const netPremium = useMemo(() => {
    // Unknown while any leg lacks a concrete price (native market, or tolerance still resolving).
    if (pricedLegs.some((p) => p.displayPrice == null)) return null;
    return pricedLegs.reduce((sum, p) => {
      const linePrem = (p.displayPrice ?? 0) * Math.round(p.leg.quantity);
      return sum + (p.leg.side === "Buy" ? -linePrem : linePrem);
    }, 0);
  }, [pricedLegs]);

  const execMut = useMutation({
    mutationFn: async () => {
      setProgress(null);
      // Re-resolve tolerance legs from fresh LTP at click time so the placed price reflects the
      // market now, not the estimate shown when the dialog opened. The result also updates the
      // cache so the preview and margin reflect what was actually sent.
      let freshMap = resolvedPricesQ.data;
      if (toleranceLegs.length > 0) {
        freshMap = await runResolve();
        queryClient.setQueryData(resolvedPricesQueryKey, freshMap);
      }

      // Final placement price + flag per leg (index-aligned with `legs`). Tolerance legs use the
      // freshly resolved price; a missing/errored price aborts before anything is sent.
      const placements = legs.map((l, i) => {
        const p = pricedLegs[i]!;
        if (p.isTolerance) {
          const r = freshMap?.get(String(i));
          const price = r?.price != null ? Number(r.price) : null;
          if (r?.error || price == null || !Number.isFinite(price)) {
            throw new Error(
              r?.error ??
                "Could not derive an aggressive limit price from live prices. Try again.",
            );
          }
          return { leg: l, price: String(price), aggressive: false };
        }
        return {
          leg: l,
          price: p.placementAggressive ? "0" : (p.placementPrice ?? "0"),
          aggressive: p.placementAggressive,
        };
      });

      const batchGroupId =
        sourceParkedIds.length === 0 && legs.length > 1 ? randomUuid() : undefined;
      const deferParkedFinalize = legs.length > 1;
      let anyParked = false;
      let parkedReason: string | undefined;
      let placedAny = false;

      for (let legIndex = 0; legIndex < placements.length; legIndex++) {
        const pl = placements[legIndex]!;
        const l = pl.leg;
        const qn = Math.round(l.quantity);
        if (qn <= 0) continue;
        setProgress({
          legIndex,
          totalLegs: placements.length,
          chunkIndex: 0,
          totalChunks: 1,
          placedQty: 0,
          totalQty: qn,
        });
        const out = await runBreakOrderChunks({
          product_type: productType,
          stock_code: stockCode,
          exchange_code: exchangeCode,
          expiry_date: expiryDisplay,
          right: l.right,
          strike_price: String(l.strike),
          total_qty: String(qn),
          price: pl.price,
          action: l.side,
          onRateLimitWait: wait,
          chunk_qty: chunkQty.trim() || undefined,
          aggressive_limit: pl.aggressive,
          from_parked_execution: sourceParkedIds.length > 0,
          batch_group_id: batchGroupId,
          defer_parked_finalize: deferParkedFinalize,
          onChunkPlaced: (info) =>
            setProgress({
              legIndex,
              totalLegs: placements.length,
              chunkIndex: info.chunkIndex,
              totalChunks: info.totalChunks,
              placedQty: info.placedQuantity,
              totalQty: info.totalQty,
            }),
        });
        if (!out.ok) {
          throw new Error(out.terminalError ?? "Order leg failed");
        }
        if (out.parked) {
          anyParked = true;
          parkedReason = out.marketClosedReason ?? parkedReason;
        } else {
          placedAny = true;
        }
      }

      if (anyParked && deferParkedFinalize) {
        const pl0 = placements[0];
        await apiClient.post<{ redirect: string }>("/order/break-finalize", {
          stock_code: stockCode,
          expiry_date: expiryDisplay,
          right: pl0?.leg.right ?? "Call",
          strike_price: String(pl0?.leg.strike ?? 0),
          product_type: productType,
          exchange_code: exchangeCode,
          price: pl0?.price ?? "0",
          action: pl0?.leg.side ?? "Buy",
          parked_only: true,
          market_closed_reason: parkedReason,
        });
      }

      if (placedAny && sourceParkedIds.length > 0) {
        await deleteParkedOrdersMany(sourceParkedIds);
      }
    },
    onSuccess: () => {
      invalidateTradingShellQueries(queryClient);
      void queryClient.invalidateQueries({ queryKey: ["parked-orders"] });
      void queryClient.invalidateQueries({ queryKey: ["orders", "list"] });
      onClose();
      router.push("/orders");
    },
  });

  const parkMut = useMutation({
    mutationFn: async () => {
      const chunk = chunkQty.trim();
      const pid = sourceParkedIds[0];
      if (
        legs.length === 1 &&
        pid != null &&
        sourceParkedIds.length === 1
      ) {
        const l0 = legs[0]!;
        await patchParkedOrder(pid, {
          quantity: String(Math.round(l0.quantity)),
          price: String(l0.premiumPerUnit),
          chunk_qty: chunk ? chunk : null,
        });
        return;
      }
      const batchId = randomUuid();
      const items = legs.map((l) => ({
        product_type: productType as "Options",
        stock_code: stockCode,
        exchange_code: exchangeCode,
        expiry_date: expiryDisplay,
        right: l.right,
        strike_price: String(l.strike),
        quantity: String(Math.round(l.quantity)),
        price: String(l.premiumPerUnit),
        action: l.side,
        chunk_qty: chunk || undefined,
        batch_group_id: batchId,
      }));
      await createParkedOrders({
        items,
        replace_ids:
          sourceParkedIds.length > 0 ? [...sourceParkedIds] : undefined,
      });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["parked-orders"] });
      onClose();
      router.push("/orders");
    },
  });

  const pending = execMut.isPending || parkMut.isPending;

  const progressFraction =
    progress != null
      ? Math.min(
          1,
          (progress.legIndex +
            (progress.totalChunks > 0
              ? (progress.chunkIndex + 1) / progress.totalChunks
              : 0)) /
            Math.max(1, progress.totalLegs),
        )
      : 0;

  const progressLabel = progress
    ? [
        progress.totalLegs > 1
          ? `Leg ${progress.legIndex + 1} of ${progress.totalLegs}`
          : null,
        progress.totalChunks > 1
          ? `chunk ${progress.chunkIndex + 1} of ${progress.totalChunks}`
          : null,
        `${progress.placedQty.toLocaleString("en-IN")} / ${progress.totalQty.toLocaleString(
          "en-IN",
        )} qty`,
      ]
        .filter(Boolean)
        .join(" · ")
    : null;

  return (
    <Modal
      open={open}
      onClose={onClose}
      pending={pending}
      titleId="order-exec-confirm-title"
      zIndexClass="z-[120]"
      panelClassName={`${sb.modalPanel} !max-w-[min(96vw,44rem)] mx-auto`}
    >
        <div className="flex items-start justify-between gap-3">
          <h3
            id="order-exec-confirm-title"
            className="min-w-0 flex-1 app-text-title"
          >
            Confirm execution
          </h3>
          <div className="flex shrink-0 items-center gap-2">
            {quoteMeta ? <QuoteSourceBadge meta={quoteMeta} variant="compact" /> : null}
            <button
              type="button"
              className="-m-1 size-9 shrink-0 rounded-lg text-xl leading-none text-muted transition hover:bg-border-soft hover:text-foreground disabled:cursor-not-allowed disabled:text-faint disabled:hover:bg-transparent"
              onClick={() => {
                if (!pending) onClose();
              }}
              disabled={pending}
              aria-label="Close"
            >
              ×
            </button>
          </div>
        </div>
        <p className="mt-1 text-sm leading-relaxed text-muted">
          The following legs will be sent as orders. Total margin is computed
          for the full basket (single SPAN calculation).
        </p>

        <ul className="mt-3 max-h-64 divide-y divide-border-soft overflow-x-auto overflow-y-auto">
          {pricedLegs.map((p, idx) => {
            const l = p.leg;
            const q = Math.round(l.quantity);
            const linePrem =
              p.displayPrice == null ? null : p.displayPrice * q;
            const label = formatOptionSymbolLabel(
              stockCode,
              expiryDisplay,
              l.strike,
            );
            const legStatus =
              progress == null
                ? null
                : idx < progress.legIndex
                  ? "done"
                  : idx === progress.legIndex
                    ? execMut.isError
                      ? "failed"
                      : "active"
                    : "pending";
            return (
              <li key={`${l.strike}-${l.right}-${idx}`} className="px-1 py-2.5">
                <div className="flex w-max min-w-full flex-nowrap items-center gap-3 font-mono text-sm font-normal tabular-nums text-foreground">
                  {legStatus ? (
                    <span
                      className="inline-flex size-4 shrink-0 items-center justify-center"
                      aria-hidden
                      title={
                        legStatus === "done"
                          ? "Placed"
                          : legStatus === "active"
                            ? "Placing…"
                            : legStatus === "failed"
                              ? "Failed"
                              : "Pending"
                      }
                    >
                      {legStatus === "done" ? (
                        <span className="text-up">✓</span>
                      ) : legStatus === "active" ? (
                        <span className="size-2 animate-pulse rounded-full bg-accent-strong" />
                      ) : legStatus === "failed" ? (
                        <span className="text-down">×</span>
                      ) : (
                        <span className="size-1.5 rounded-full bg-border" />
                      )}
                    </span>
                  ) : null}
                  <span
                    className="inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap"
                    title={label}
                  >
                    {label}
                    <OptionTypeBadge right={l.right} />
                  </span>
                  <OrderSideBadge side={l.side} />
                  <span className="shrink-0 whitespace-nowrap text-muted">
                    Qty {q <= 0 ? "—" : q.toLocaleString("en-IN")}
                  </span>
                  <span className="shrink-0 whitespace-nowrap text-muted">
                    {p.isMarket ? (
                      <span className="rounded-full bg-amber-tint px-2 py-0.5 font-sans text-xs font-medium text-amber-on-tint">
                        Market
                      </span>
                    ) : p.isTolerance ? (
                      <span className="inline-flex items-center gap-1.5">
                        <span className="rounded-full bg-amber-tint px-2 py-0.5 font-sans text-xs font-medium text-amber-on-tint">
                          Aggressive limit
                        </span>
                        {p.resolveError ? (
                          <span className="font-sans text-xs text-down">
                            no live price
                          </span>
                        ) : p.displayPrice == null ? (
                          <span className="text-muted">@ …</span>
                        ) : (
                          <>
                            @ ₹
                            {p.displayPrice.toLocaleString("en-IN", {
                              maximumFractionDigits: 2,
                            })}
                          </>
                        )}
                      </span>
                    ) : (
                      <>
                        @ ₹
                        {l.premiumPerUnit.toLocaleString("en-IN", {
                          maximumFractionDigits: 2,
                        })}
                      </>
                    )}
                  </span>
                  <span className="shrink-0 whitespace-nowrap text-muted">
                    Premium{" "}
                    {linePrem == null
                      ? "—"
                      : formatIndianMoneyCompact(linePrem)}
                  </span>
                </div>
              </li>
            );
          })}
        </ul>

        <div className="mt-3 space-y-1.5 rounded-lg bg-panel2 px-3 py-2 text-sm">
          <div>
            <span className="font-semibold text-foreground">
              Net premium:{" "}
            </span>
            <span
              className={`font-mono tabular-nums ${
                netPremium == null
                  ? "text-foreground"
                  : netPremium < 0
                    ? "text-down"
                    : "text-up"
              }`}
            >
              {netPremium == null
                ? "—"
                : `${netPremium < 0 ? "−" : "+"}${formatIndianMoneyCompact(
                    Math.abs(netPremium),
                  )}`}
            </span>
          </div>
          <div>
            <span className="font-semibold text-foreground">
              Total margin required (SPAN):{" "}
            </span>
            <span className="font-mono tabular-nums text-foreground">
              {marginQ.isFetching ? (
                "…"
              ) : spanMargin != null && Number.isFinite(spanMargin) ? (
                formatIndianMoneyCompact(spanMargin)
              ) : (
                marginState?.Error ??
                (marginQ.isError ? "Margin request failed" : "—")
              )}
            </span>
          </div>
        </div>

        <ChunkSizeOrderField
          id="order-exec-confirm-chunk-qty"
          className="mt-3"
          chunkQty={chunkQty}
          onChunkQtyChange={setChunkQty}
          defaultsQuery={chunkDefaultsQ}
          disabled={pending}
        />

        {execMut.isError ? (
          <p className="app-alert-error mt-2 text-xs">
            {execMut.error instanceof Error
              ? execMut.error.message
              : "Execution failed"}
          </p>
        ) : null}
        {parkMut.isError ? (
          <p className="app-alert-error mt-2 text-xs">
            {parkMut.error instanceof Error
              ? parkMut.error.message
              : "Could not park execution"}
          </p>
        ) : null}
        {toleranceError ? (
          <p className="app-alert-error mt-2 text-xs">
            Couldn&apos;t derive an aggressive limit price from live prices:{" "}
            {toleranceError} Retry, switch to a fixed price, or park the order.
          </p>
        ) : null}

        {marketClosed ? (
          <div
            role="status"
            className="mt-3 rounded-md border border-amber-accent/40 bg-amber-tint px-3 py-2.5 text-sm text-amber-on-tint"
          >
            The market is currently closed ({marketStatus.closed_reason}). Orders
            can only be{" "}
            <span className="font-semibold">parked for execution</span>. Go to
            the{" "}
            <Link
              href="/orders"
              className="font-medium underline underline-offset-2 text-amber-accent hover:brightness-110"
            >
              Orders
            </Link>{" "}
            page and execute your parked orders once the market opens.{" "}
            <HelpLink topicId="parked-orders" className="text-sm text-amber-accent">
              Help
            </HelpLink>
          </div>
        ) : null}

        {execMut.isPending && progressLabel ? (
          <div className="mt-3 space-y-1.5" role="status" aria-live="polite">
            <div className="h-1.5 w-full overflow-hidden rounded-full bg-border-soft">
              <div
                className="h-full rounded-full bg-accent-strong transition-[width] duration-300"
                style={{ width: `${Math.round(progressFraction * 100)}%` }}
              />
            </div>
            <p className="font-mono text-xs tabular-nums text-muted">
              Placing order — {progressLabel}. Don&apos;t close this window.
            </p>
          </div>
        ) : null}

        <div className="grid grid-cols-1 gap-2 pt-3 sm:grid-cols-2 sm:gap-3">
          <button
            type="button"
            className="app-btn-secondary h-10 min-h-10 w-full sm:h-11 sm:min-h-11"
            disabled={parkMut.isPending || execMut.isPending || !legs.length}
            aria-busy={parkMut.isPending}
            onClick={() => parkMut.mutate()}
          >
            <AsyncLabelSpan
              busy={parkMut.isPending}
              idleLabel="Park execution"
              busyLabel="Parking…"
            />
          </button>
          <button
            type="button"
            className={`${sb.btnPrimary} h-10 min-h-10 w-full sm:h-11 sm:min-h-11`}
            disabled={
              execMut.isPending ||
              parkMut.isPending ||
              !stockCode.trim() ||
              !expiryDisplay.trim() ||
              !legs.length ||
              !chunkReady ||
              !executeAllowed ||
              !toleranceReady ||
              toleranceResolving ||
              legs.some((x) => x.quantity <= 0)
            }
            title={
              marketClosed
                ? "Market is closed — park the order and execute from Orders when the market opens."
                : toleranceError ?? undefined
            }
            aria-busy={execMut.isPending}
            onClick={() => execMut.mutate()}
          >
            <AsyncLabelSpan
              busy={execMut.isPending || toleranceResolving}
              idleLabel="Confirm"
              busyLabel={
                toleranceResolving
                  ? "Pricing…"
                  : progressLabel
                    ? `Placing… ${Math.round(progressFraction * 100)}%`
                    : "Placing…"
              }
            />
          </button>
        </div>
    </Modal>
  );
}
