"use client";

import type { UseQueryResult } from "@tanstack/react-query";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useMemo } from "react";
import { ChunkSizeOrderField } from "@/components/order/ChunkSizeOrderField";
import { AsyncLabelSpan } from "@/components/ui/AsyncLabelSpan";
import { apiClient } from "@/lib/api-client";
import type { BreakChunkDefaultsResponse } from "@/lib/break-chunk-defaults";
import { formatIndianMoneyCompact } from "@/lib/format-money-in";
import { runBreakOrderChunks } from "@/lib/icici-rate-limit-flow";
import { createParkedOrders, deleteParkedOrdersMany, patchParkedOrder } from "@/lib/parked-orders";
import { randomUuid } from "@/lib/random-uuid";
import { sb } from "@/lib/strategy-builder/ui";
import type {
  MarginApiResponse,
  OptionRight,
  OrderSide,
} from "@/lib/strategy-builder/types";
import { useBreakChunkQty } from "@/lib/use-break-chunk-qty";
import { invalidateTradingShellQueries } from "@/lib/trading-cache";
import { useRateLimitCountdown } from "@/lib/use-rate-limit-countdown";

export type ExecutionPreviewLeg = {
  strike: number;
  right: OptionRight;
  side: OrderSide;
  /** Contract units (not lots). */
  quantity: number;
  premiumPerUnit: number;
  aggressiveLimit?: boolean;
};

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

function formatOptionSymbolLabel(
  stock: string,
  expiryDisplay: string,
  strike: number,
  right: OptionRight,
): string {
  const sym = right === "Call" ? "CE" : "PE";
  const expShort = (() => {
    const p = expiryDisplay.trim().split("-");
    if (
      p.length >= 2 &&
      /^\d{1,2}$/.test(p[0]!) &&
      /^[A-Za-z]{3}/.test(p[1]!)
    ) {
      const day = p[0]!.padStart(2, "0");
      const mon =
        p[1]!.slice(0, 1).toUpperCase() + p[1]!.slice(1, 3).toLowerCase();
      return `${day}-${mon}`;
    }
    return expiryDisplay.trim() || "—";
  })();
  const k = Number.isFinite(strike) ? Math.round(strike).toString() : "—";
  return `${stock || "—"}-${expShort}-${k}-${sym}`;
}

function LegPositionChip({ side }: { side: OrderSide }) {
  const buy = side === "Buy";
  return (
    <span
      className={
        buy
          ? "inline-flex shrink-0 rounded-full border border-emerald-600/80 bg-emerald-600/15 px-2 py-0.5 text-sm font-semibold text-emerald-800 dark:border-emerald-500/70 dark:bg-emerald-500/15 dark:text-emerald-200"
          : "inline-flex shrink-0 rounded-full border border-red-600/80 bg-red-600/15 px-2 py-0.5 text-sm font-semibold text-red-800 dark:border-red-500/70 dark:bg-red-500/15 dark:text-red-200"
      }
    >
      {side}
    </span>
  );
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
}: OrderExecutionConfirmDialogProps) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const { wait } = useRateLimitCountdown();

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

  const marginLegKey = useMemo(
    () =>
      JSON.stringify(
        legs.map((l) => [
          l.strike,
          l.right,
          l.side,
          l.quantity,
          l.premiumPerUnit,
          l.aggressiveLimit ?? false,
        ]),
      ),
    [legs],
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
        legs: legs.map((l) => ({
          stock_code: stockCode,
          exchange_code: exchangeCode,
          expiry_date: expiryDisplay,
          product_type: productType,
          right: l.right,
          strike_price: String(l.strike),
          quantity: String(Math.round(l.quantity)),
          price: l.aggressiveLimit ? "0" : String(l.premiumPerUnit),
          action: l.side,
        })),
      }),
    enabled:
      open &&
      legs.length > 0 &&
      stockCode.trim().length > 0 &&
      expiryDisplay.trim().length > 0,
    staleTime: 5000,
  });

  const marginState = marginQ.data;
  const spanMargin = parseSpanMarginFromResponse(marginState);

  const execMut = useMutation({
    mutationFn: async () => {
      const batchGroupId =
        sourceParkedIds.length === 0 && legs.length > 1 ? randomUuid() : undefined;
      const deferParkedFinalize = legs.length > 1;
      let anyParked = false;
      let parkedReason: string | undefined;
      let placedAny = false;

      for (const l of legs) {
        const qn = Math.round(l.quantity);
        if (qn <= 0) continue;
        const out = await runBreakOrderChunks({
          product_type: productType,
          stock_code: stockCode,
          exchange_code: exchangeCode,
          expiry_date: expiryDisplay,
          right: l.right,
          strike_price: String(l.strike),
          total_qty: String(qn),
          price: l.aggressiveLimit ? "0" : String(l.premiumPerUnit),
          action: l.side,
          onRateLimitWait: wait,
          chunk_qty: chunkQty.trim() || undefined,
          aggressive_limit: l.aggressiveLimit ?? false,
          from_parked_execution: sourceParkedIds.length > 0,
          batch_group_id: batchGroupId,
          defer_parked_finalize: deferParkedFinalize,
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
        await apiClient.post<{ redirect: string }>("/order/break-finalize", {
          stock_code: stockCode,
          expiry_date: expiryDisplay,
          right: legs[0]?.right ?? "Call",
          strike_price: String(legs[0]?.strike ?? 0),
          product_type: productType,
          exchange_code: exchangeCode,
          price: legs[0]?.aggressiveLimit ? "0" : String(legs[0]?.premiumPerUnit ?? 0),
          action: legs[0]?.side ?? "Buy",
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

  if (!open) return null;

  const pending = execMut.isPending || parkMut.isPending;

  return (
    <div
      className="fixed inset-0 z-[120] flex items-center justify-center bg-black/50 p-4 dark:bg-black/60"
      role="presentation"
      onClick={() => {
        if (!pending) onClose();
      }}
      onKeyDown={(e) => {
        if (e.key === "Escape" && !pending) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="order-exec-confirm-title"
        className={`${sb.modalPanel} !w-max max-w-[min(96vw,42rem)] mx-auto`}
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <h3
              id="order-exec-confirm-title"
              className="text-base font-semibold text-zinc-900 dark:text-zinc-50"
            >
              Confirm execution
            </h3>
            <p className="mt-1 text-sm leading-relaxed text-zinc-600 dark:text-zinc-400">
              The following legs will be sent as orders. Total margin is
              computed for the full basket (single SPAN calculation).
            </p>
          </div>
          <button
            type="button"
            className="-m-1 size-9 shrink-0 rounded-lg text-xl leading-none text-zinc-500 transition hover:bg-zinc-100 hover:text-zinc-800 disabled:cursor-not-allowed disabled:text-zinc-300 disabled:hover:bg-transparent dark:hover:bg-zinc-800 dark:hover:text-zinc-200 dark:disabled:text-zinc-600 dark:disabled:hover:bg-transparent"
            onClick={() => {
              if (!pending) onClose();
            }}
            disabled={pending}
            aria-label="Close"
          >
            ×
          </button>
        </div>

        <ul className="max-h-64 divide-y divide-zinc-200/90 overflow-x-auto overflow-y-auto rounded-md border border-zinc-200/80 dark:divide-zinc-700/90 dark:border-zinc-700/80">
          {legs.map((l, idx) => {
            const q = Math.round(l.quantity);
            const linePrem = l.aggressiveLimit ? null : l.premiumPerUnit * q;
            const label = formatOptionSymbolLabel(
              stockCode,
              expiryDisplay,
              l.strike,
              l.right,
            );
            return (
              <li key={`${l.strike}-${l.right}-${idx}`} className="px-3 py-2.5">
                <div className="flex w-max min-w-full flex-nowrap items-center gap-3 text-sm font-normal tabular-nums text-zinc-800 dark:text-zinc-200">
                  <span className="shrink-0 whitespace-nowrap" title={label}>
                    {label}
                  </span>
                  <LegPositionChip side={l.side} />
                  <span className="shrink-0 whitespace-nowrap">
                    Qty {q <= 0 ? "—" : q.toLocaleString("en-IN")}
                  </span>
                  <span className="shrink-0 whitespace-nowrap">
                    {l.aggressiveLimit ? (
                      <span className="rounded-full border border-amber-500/60 bg-amber-500/10 px-2 py-0.5 text-xs font-medium text-amber-800 dark:text-amber-200">
                        Aggressive limit
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
                  <span className="shrink-0 whitespace-nowrap">
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

        <ChunkSizeOrderField
          id="order-exec-confirm-chunk-qty"
          className="mt-3"
          chunkQty={chunkQty}
          onChunkQtyChange={setChunkQty}
          defaultsQuery={chunkDefaultsQ}
          disabled={pending}
        />

        <div className="mt-3 rounded-lg bg-zinc-100/90 px-3 py-2 text-sm dark:bg-zinc-800/80">
          <span className="font-semibold text-zinc-800 dark:text-zinc-100">
            Total margin required (SPAN):{" "}
          </span>
          <span className="tabular-nums text-zinc-900 dark:text-zinc-50">
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

        {execMut.isError ? (
          <p className="mt-2 text-xs text-red-600 dark:text-red-400">
            {execMut.error instanceof Error
              ? execMut.error.message
              : "Execution failed"}
          </p>
        ) : null}
        {parkMut.isError ? (
          <p className="mt-2 text-xs text-red-600 dark:text-red-400">
            {parkMut.error instanceof Error
              ? parkMut.error.message
              : "Could not park execution"}
          </p>
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
              legs.some((x) => x.quantity <= 0)
            }
            aria-busy={execMut.isPending}
            onClick={() => execMut.mutate()}
          >
            <AsyncLabelSpan
              busy={execMut.isPending}
              idleLabel="Confirm"
              busyLabel="Placing…"
            />
          </button>
        </div>
      </div>
    </div>
  );
}
