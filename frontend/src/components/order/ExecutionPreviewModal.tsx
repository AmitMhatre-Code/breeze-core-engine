"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useMemo } from "react";
import { apiClient } from "@/lib/api-client";
import { formatIndianMoneyCompact } from "@/lib/format-money-in";
import { sb } from "@/lib/strategy-builder/ui";
import type {
  ExecuteApiResponse,
  MarginApiResponse,
  OptionRight,
  OrderSide,
} from "@/lib/strategy-builder/types";

export type ExecutionPreviewLeg = {
  strike: number;
  right: OptionRight;
  side: OrderSide;
  /** Contract units (not lots). */
  quantity: number;
  premiumPerUnit: number;
};

type ExecutionPreviewModalProps = {
  open: boolean;
  onClose: () => void;
  stockCode: string;
  exchangeCode: string;
  expiryDisplay: string;
  legs: ExecutionPreviewLeg[];
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

export function ExecutionPreviewModal({
  open,
  onClose,
  stockCode,
  exchangeCode,
  expiryDisplay,
  legs,
}: ExecutionPreviewModalProps) {
  const router = useRouter();
  const queryClient = useQueryClient();

  const marginLegKey = useMemo(
    () =>
      JSON.stringify(
        legs.map((l) => [
          l.strike,
          l.right,
          l.side,
          l.quantity,
          l.premiumPerUnit,
        ]),
      ),
    [legs],
  );

  const marginQ = useQuery({
    queryKey: [
      "execution-preview",
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
          product_type: "Options",
          right: l.right,
          strike_price: String(l.strike),
          quantity: String(Math.round(l.quantity)),
          price: String(l.premiumPerUnit),
          action: l.side,
        })),
      }),
    enabled:
      open &&
      legs.length > 0 &&
      Boolean(stockCode.trim() && expiryDisplay.trim()),
    staleTime: 5000,
  });

  const marginState = marginQ.data;
  const spanMargin = parseSpanMarginFromResponse(marginState);

  const execMut = useMutation({
    mutationFn: async () => {
      const legsPayload = legs.map((l) => ({
        stock_code: stockCode,
        exchange_code: exchangeCode,
        expiry_date: expiryDisplay,
        product_type: "Options",
        right: l.right,
        strike_price: String(l.strike),
        quantity: String(Math.round(l.quantity)),
        price: String(l.premiumPerUnit),
        action: l.side,
        idempotency_key:
          typeof crypto !== "undefined" && crypto.randomUUID
            ? crypto.randomUUID()
            : undefined,
      }));
      return apiClient.post<ExecuteApiResponse>("/strategy-builder/execute", {
        legs: legsPayload,
      });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["book"] });
      void queryClient.invalidateQueries({ queryKey: ["portfolio", "positions"] });
      onClose();
      router.push("/orders");
    },
  });

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-[120] flex items-center justify-center bg-black/50 p-4"
      role="presentation"
      onClick={() => {
        if (!execMut.isPending) onClose();
      }}
      onKeyDown={(e) => {
        if (e.key === "Escape" && !execMut.isPending) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="execution-preview-title"
        className={`${sb.modalPanel} !w-max max-w-[min(96vw,42rem)] mx-auto`}
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <h3
              id="execution-preview-title"
              className="text-base font-semibold text-zinc-900 dark:text-zinc-50"
            >
              Confirm execution
            </h3>
            <p className="mt-1 text-sm leading-relaxed text-zinc-600 dark:text-zinc-400">
              The following legs will be sent as orders. Total margin is computed
              for the full strategy (single SPAN calculation).
            </p>
          </div>
          <button
            type="button"
            className="-m-1 shrink-0 rounded-lg p-1.5 text-xl leading-none text-zinc-500 transition hover:bg-zinc-100 hover:text-zinc-800 disabled:opacity-40 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
            onClick={() => {
              if (!execMut.isPending) onClose();
            }}
            disabled={execMut.isPending}
            aria-label="Close"
          >
            ×
          </button>
        </div>
        <ul className="max-h-64 divide-y divide-zinc-200/90 overflow-x-auto overflow-y-auto rounded-xl border border-zinc-200/80 dark:divide-zinc-700/90 dark:border-zinc-700/80">
          {legs.map((l, idx) => {
            const q = Math.round(l.quantity);
            const linePrem = l.premiumPerUnit * q;
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
                    @ ₹
                    {l.premiumPerUnit.toLocaleString("en-IN", {
                      maximumFractionDigits: 2,
                    })}
                  </span>
                  <span className="shrink-0 whitespace-nowrap">
                    Premium {formatIndianMoneyCompact(linePrem)}
                  </span>
                </div>
              </li>
            );
          })}
        </ul>
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
          <p className="text-xs text-red-600 dark:text-red-400">
            {execMut.error instanceof Error
              ? execMut.error.message
              : "Execution failed"}
          </p>
        ) : null}
        <div className="flex justify-end pt-1">
          <button
            type="button"
            className={`${sb.btnPrimary} min-w-[10rem]`}
            disabled={
              execMut.isPending ||
              !stockCode ||
              !expiryDisplay ||
              !legs.length ||
              legs.some((x) => x.quantity <= 0)
            }
            onClick={() => execMut.mutate()}
          >
            {execMut.isPending ? "Placing…" : "Confirm"}
          </button>
        </div>
      </div>
    </div>
  );
}
