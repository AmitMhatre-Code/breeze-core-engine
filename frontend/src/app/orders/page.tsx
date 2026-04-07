// Client component so auth cookies are included with browser fetch.
"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Fragment,
  Suspense,
  useCallback,
  useMemo,
  useState,
  type MouseEvent,
} from "react";
import { useRouter } from "next/navigation";
import { AppShell } from "@/components/layout/AppShell";
import type { ExecutionPreviewLeg } from "@/components/order/OrderExecutionConfirmDialog";
import { OrderBookDatePopover } from "@/components/order/OrderBookDatePopover";
import { useOrderConfirm } from "@/components/order/OrderConfirmProvider";
import { PrefilledOrderCard } from "@/components/order/PrefilledOrderCard";
import { RateLimitPauseOverlay } from "@/components/order/RateLimitPauseOverlay";
import { AsyncLabelSpan } from "@/components/ui/AsyncLabelSpan";
import { apiClient } from "@/lib/api-client";
import { runCancelOrdersWithPacing } from "@/lib/icici-rate-limit-flow";
import {
  deleteParkedOrdersMany,
  fetchParkedOrders,
  parkedOrderToConfirmPayload,
  type ParkedOrderListItem,
} from "@/lib/parked-orders";
import { useRateLimitCountdown } from "@/lib/use-rate-limit-countdown";
import {
  buildPlaceOrderCloneFromBookRow,
  buildPlaceOrderCloneFromParkedRow,
  setPlaceOrderClonePayload,
} from "@/lib/place-order-clone";
import type { OptionRight } from "@/lib/strategy-builder/types";

type BookMessage = { type?: string; message?: string };

type BookOrderRow = {
  order_id?: string;
  option?: string;
  exchange_code?: string;
  action?: string;
  quantity?: number | string;
  open_quantity?: number | string;
  price?: number | string;
  status?: string;
  cancelable?: boolean;
  stock_code?: string;
  expiry_date?: string;
  strike_price?: number | string;
  right?: string;
};

type BookGroup = {
  group: string;
  group_option?: string;
  group_action?: string;
  group_ordered?: number;
  group_cancelled?: number;
  group_expired?: number;
  group_open?: number;
  group_executed?: number;
  group_ltp?: number | string;
  group_orders?: BookOrderRow[];
};

function cancelDetailForOrderKey(
  key: string,
  groups: BookGroup[],
): { option: string; open_quantity: number } {
  for (const g of groups) {
    for (const o of g.group_orders ?? []) {
      const k = `${o.order_id ?? ""}|${o.exchange_code ?? ""}`;
      if (k === key) {
        const raw = o.open_quantity ?? o.quantity ?? 0;
        const open_quantity =
          typeof raw === "number"
            ? raw
            : parseInt(String(raw), 10) || 0;
        return {
          option: String(o.option ?? "").trim(),
          open_quantity,
        };
      }
    }
  }
  return { option: "", open_quantity: 0 };
}

type BookDataResponse = {
  messages: BookMessage[];
  grouped_orders: BookGroup[] | null;
  start: string;
  end: string;
  orders_failed: boolean;
};

function parsePositiveInt(raw: string): number | null {
  const n = parseInt(raw.trim(), 10);
  if (!Number.isFinite(n) || n <= 0) return null;
  return n;
}

const ordersCancelBarClass =
  "flex flex-wrap items-center justify-end gap-3 rounded-md border border-zinc-200/80 bg-zinc-50/90 px-4 py-3 shadow-sm backdrop-blur-sm dark:border-zinc-700/80 dark:bg-zinc-900/70";

const cloneToPlaceBtnClass =
  "inline-flex rounded-md p-1.5 text-zinc-500 transition hover:bg-sky-500/10 hover:text-sky-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-500/40 dark:text-zinc-400 dark:hover:bg-sky-950/40 dark:hover:text-sky-300";

function sidePillClass(action: string | undefined): string {
  const a = String(action ?? "")
    .trim()
    .toLowerCase();
  if (a === "buy" || a.startsWith("buy"))
    return "inline-flex rounded-full bg-emerald-500/[0.12] px-2.5 py-0.5 text-xs font-semibold text-emerald-800 ring-1 ring-emerald-600/15 dark:bg-emerald-400/10 dark:text-emerald-300 dark:ring-emerald-400/20";
  if (a === "sell" || a.startsWith("sell"))
    return "inline-flex rounded-full bg-rose-500/[0.12] px-2.5 py-0.5 text-xs font-semibold text-rose-800 ring-1 ring-rose-600/15 dark:bg-rose-400/10 dark:text-rose-300 dark:ring-rose-400/20";
  return "font-medium text-zinc-800 dark:text-zinc-200";
}

function statusChipClass(status: string | undefined): string {
  const s = String(status ?? "")
    .trim()
    .toLowerCase();
  const base =
    "inline-flex max-w-[11rem] truncate rounded-md px-2 py-0.5 text-xs font-medium ring-1 ";
  if (s.includes("execut"))
    return `${base} bg-emerald-500/10 text-emerald-900 ring-emerald-500/20 dark:text-emerald-200`;
  if (s.includes("cancel"))
    return `${base} bg-zinc-200/90 text-zinc-800 ring-zinc-300/80 dark:bg-zinc-800 dark:text-zinc-300 dark:ring-zinc-600`;
  if (s.includes("partial") || s.includes("open") || s.includes("request"))
    return `${base} bg-sky-500/10 text-sky-900 ring-sky-500/20 dark:text-sky-200`;
  if (s.includes("expir"))
    return `${base} bg-amber-500/10 text-amber-950 ring-amber-500/25 dark:text-amber-200`;
  return `${base} bg-zinc-100 text-zinc-800 ring-zinc-200 dark:bg-zinc-800/80 dark:text-zinc-300 dark:ring-zinc-600`;
}

function CloneOrderGlyph({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
      <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
    </svg>
  );
}

function ExecuteOrderGlyph({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <polygon points="5 3 19 12 5 21 5 3" />
    </svg>
  );
}

const parkedCheckboxClass =
  "h-[1.125rem] w-[1.125rem] cursor-pointer rounded border-zinc-300 text-sky-600 accent-sky-600 focus:ring-sky-500/30 dark:border-zinc-600 dark:bg-zinc-900 dark:accent-sky-500";

const executeParkedBtnClass =
  "inline-flex rounded-md p-1.5 text-sky-600 transition hover:bg-sky-500/10 hover:text-sky-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-500/40 disabled:cursor-not-allowed disabled:opacity-40 dark:text-sky-400 dark:hover:bg-sky-950/40 dark:hover:text-sky-300";

function ChevronGlyph({
  expanded,
  className,
}: {
  expanded?: boolean;
  className?: string;
}) {
  return (
    <svg
      className={[
        "shrink-0 transition-transform duration-200",
        expanded ? "rotate-180" : "",
        className ?? "",
      ]
        .filter(Boolean)
        .join(" ")}
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="m6 9 6 6 6-6" />
    </svg>
  );
}

function messageClass(type: string | undefined): string {
  if (type === "alert-success")
    return "border-emerald-200 bg-emerald-50 text-emerald-900 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-100";
  if (type === "alert-danger")
    return "border-red-200 bg-red-50 text-red-900 dark:border-red-900 dark:bg-red-950/40 dark:text-red-100";
  if (type === "alert-warning")
    return "border-amber-200 bg-amber-50 text-amber-950 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-100";
  return "border-zinc-200 bg-zinc-50 text-zinc-800 dark:border-zinc-700 dark:bg-zinc-900/40 dark:text-zinc-200";
}

/** Map derivatives segment codes to parent exchange labels for display only. */
function formatExchangeDisplay(code: string | undefined): string {
  const raw = String(code ?? "").trim();
  if (!raw) return "";
  const u = raw.toUpperCase();
  if (u === "NFO") return "NSE";
  if (u === "BFO") return "BSE";
  return raw;
}

/** Integer quantities with Indian-style grouping (e.g. 12,34,567). */
function formatQtyIndian(raw: unknown): string {
  if (raw == null || raw === "") return "—";
  const n =
    typeof raw === "number" ? raw : Number(String(raw).trim());
  if (!Number.isFinite(n)) return "—";
  return Math.trunc(n).toLocaleString("en-IN", {
    maximumFractionDigits: 0,
  });
}

function DismissMessageGlyph({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M18 6 6 18M6 6l12 12" />
    </svg>
  );
}

function BookMessages({ messages }: { messages: BookMessage[] }) {
  const [dismissed, setDismissed] = useState<Set<string>>(() => new Set());

  if (!messages.length) return null;
  const hasVisible = messages.some((_, i) => !dismissed.has(String(i)));
  if (!hasVisible) return null;

  const dismissBtnClass =
    "shrink-0 rounded-md p-1 text-zinc-500 opacity-70 transition hover:bg-black/5 hover:opacity-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-500/40 dark:text-zinc-400 dark:hover:bg-white/10";

  return (
    <ul className="space-y-2">
      {messages.map((m, i) => {
        if (dismissed.has(String(i))) return null;
        return (
          <li
            key={i}
            className={`flex items-start gap-2 rounded-lg border px-3 py-2 text-sm ${messageClass(m.type)}`}
          >
            <span className="min-w-0 flex-1 leading-snug">{m.message ?? ""}</span>
            <button
              type="button"
              className={dismissBtnClass}
              aria-label="Dismiss message"
              onClick={() =>
                setDismissed((prev) => new Set(prev).add(String(i)))
              }
            >
              <DismissMessageGlyph />
            </button>
          </li>
        );
      })}
    </ul>
  );
}

function OrdersBody() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const { secondsRemaining, wait } = useRateLimitCountdown();
  const { openOrderConfirm, openExecutionConfirm } = useOrderConfirm();

  const cloneOrderToPlace = useCallback(
    (o: BookOrderRow, e: MouseEvent) => {
      e.preventDefault();
      e.stopPropagation();
      const payload = buildPlaceOrderCloneFromBookRow(o);
      if (!payload) return;
      setPlaceOrderClonePayload(payload);
      router.push("/place-order");
    },
    [router],
  );
  /** When null, backend applies the same default range as legacy book (today → next weekday). */
  const [appliedRange, setAppliedRange] = useState<{
    start: string;
    end: string;
  } | null>(null);
  const [draftStart, setDraftStart] = useState("");
  const [draftEnd, setDraftEnd] = useState("");
  const [selected, setSelected] = useState<Set<string>>(() => new Set());
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [parkedSelected, setParkedSelected] = useState<Set<string>>(
    () => new Set(),
  );
  const [parkedEdits, setParkedEdits] = useState<
    Record<string, { quantity: string; price: string }>
  >({});
  const [parkedError, setParkedError] = useState<string | null>(null);

  const queryString = useMemo(() => {
    if (!appliedRange) return "";
    const p = new URLSearchParams();
    p.set("start", appliedRange.start);
    p.set("end", appliedRange.end);
    return `?${p.toString()}`;
  }, [appliedRange]);

  const bookQuery = useQuery({
    queryKey: [
      "book",
      "data",
      appliedRange?.start ?? "__default__",
      appliedRange?.end ?? "__default__",
    ],
    queryFn: async () =>
      apiClient.get<BookDataResponse>(`/book/data${queryString}`),
    refetchOnWindowFocus: false,
  });

  const data = bookQuery.data;
  const parkedQuery = useQuery({
    queryKey: ["parked-orders"],
    queryFn: fetchParkedOrders,
    refetchOnWindowFocus: false,
  });
  const parkedRows = useMemo(
    () => parkedQuery.data ?? [],
    [parkedQuery.data],
  );
  const inputStart = draftStart || data?.start || "";
  const inputEnd = draftEnd || data?.end || "";

  const cancelMut = useMutation({
    mutationFn: (payload: {
      order_ids: string[];
      cancel_details: { option: string; open_quantity: number }[];
    }) =>
      runCancelOrdersWithPacing({
        orderIds: payload.order_ids,
        cancel_details: payload.cancel_details,
        onRateLimitWait: wait,
      }),
    onSuccess: async () => {
      setSelected(new Set());
      await queryClient.invalidateQueries({ queryKey: ["book"] });
    },
  });

  const parkedDeleteManyMut = useMutation({
    mutationFn: (ids: string[]) => deleteParkedOrdersMany(ids),
    onSuccess: () =>
      queryClient.invalidateQueries({
        queryKey: ["parked-orders"],
      }),
    onError: (e) =>
      setParkedError(
        e instanceof Error ? e.message : "Could not cancel selected parked orders",
      ),
  });

  const toggleOne = useCallback((value: string, checked: boolean) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (checked) next.add(value);
      else next.delete(value);
      return next;
    });
  }, []);

  const toggleGroup = useCallback(
    (group: BookGroup, checked: boolean) => {
      const rows = group.group_orders ?? [];
      setSelected((prev) => {
        const next = new Set(prev);
        for (const o of rows) {
          if (!o.cancelable || !o.order_id) continue;
          const ex = o.exchange_code ?? "";
          const key = `${o.order_id}|${ex}`;
          if (checked) next.add(key);
          else next.delete(key);
        }
        return next;
      });
    },
    [],
  );

  const groups = bookQuery.data?.grouped_orders ?? null;
  const bookMsgs = bookQuery.data?.messages;
  const brokerMessagesKey = useMemo(() => {
    if (!bookMsgs?.length) return "";
    return bookMsgs
      .map((m) => `${m.type ?? ""}\0${m.message ?? ""}`)
      .join("\x1e");
  }, [bookMsgs]);
  const messages = bookMsgs ?? [];

  const groupAllSelected = useCallback(
    (group: BookGroup) => {
      const cancelable = (group.group_orders ?? []).filter(
        (o) => o.cancelable && o.order_id,
      );
      if (!cancelable.length) return false;
      return cancelable.every((o) =>
        selected.has(`${o.order_id}|${o.exchange_code ?? ""}`),
      );
    },
    [selected],
  );

  const groupSomeSelected = useCallback(
    (group: BookGroup) => {
      const cancelable = (group.group_orders ?? []).filter(
        (o) => o.cancelable && o.order_id,
      );
      return cancelable.some((o) =>
        selected.has(`${o.order_id}|${o.exchange_code ?? ""}`),
      );
    },
    [selected],
  );

  const applyDateRange = useCallback(() => {
    const s = (draftStart || data?.start || "").trim();
    const en = (draftEnd || data?.end || "").trim();
    if (!s || !en) return;
    setAppliedRange({ start: s, end: en });
    setDraftStart(s);
    setDraftEnd(en);
  }, [draftStart, draftEnd, data?.start, data?.end]);

  const toggleParkedOne = useCallback((id: string, checked: boolean) => {
    setParkedSelected((prev) => {
      const next = new Set(prev);
      if (checked) next.add(id);
      else next.delete(id);
      return next;
    });
  }, []);

  const allParkedSelected = useMemo(() => {
    if (!parkedRows.length) return false;
    return parkedRows.every((r) => parkedSelected.has(r.id));
  }, [parkedRows, parkedSelected]);

  const someParkedSelected = useMemo(
    () => parkedRows.some((r) => parkedSelected.has(r.id)),
    [parkedRows, parkedSelected],
  );

  const toggleParkedAll = useCallback(
    (checked: boolean) => {
      if (checked) {
        setParkedSelected(new Set(parkedRows.map((r) => r.id)));
      } else {
        setParkedSelected(new Set());
      }
    },
    [parkedRows],
  );

  const cloneParkedToPlace = useCallback(
    (row: ParkedOrderListItem, e: MouseEvent) => {
      e.preventDefault();
      e.stopPropagation();
      const edit = parkedEdits[row.id];
      const payload = buildPlaceOrderCloneFromParkedRow(
        row,
        edit ?? {
          quantity: row.quantity,
          price: row.price,
        },
      );
      if (!payload) return;
      setPlaceOrderClonePayload(payload);
      router.push("/place-order");
    },
    [router, parkedEdits],
  );

  const executeOneParked = useCallback(
    (row: ParkedOrderListItem) => {
      const edit = parkedEdits[row.id];
      const payload = parkedOrderToConfirmPayload(row);
      if (edit) {
        if (parsePositiveInt(edit.quantity) == null) {
          setParkedError("Enter a valid positive quantity before execute.");
          return;
        }
        payload.quantity = edit.quantity;
        payload.price = edit.price;
      }
      setParkedError(null);
      openOrderConfirm(payload, {
        sourceParkedIds: [row.id],
      });
    },
    [openOrderConfirm, parkedEdits],
  );

  const executeSelectedParked = useCallback(() => {
    const selectedRows = parkedRows.filter((r) => parkedSelected.has(r.id));
    if (!selectedRows.length) return;
    if (selectedRows.length === 1) {
      executeOneParked(selectedRows[0]!);
      return;
    }
    const first = selectedRows[0]!;
    const stockCode = first.stock_code;
    const exchangeCode = first.exchange_code || "NFO";
    const expiryDisplay = first.expiry_date;
    const sameContract = selectedRows.every(
      (x) =>
        x.stock_code === stockCode &&
        (x.exchange_code || "NFO") === exchangeCode &&
        x.expiry_date === expiryDisplay,
    );
    if (!sameContract) {
      setParkedError(
        "Bulk execute requires same stock, exchange, and expiry for all selected parked rows.",
      );
      return;
    }
    const legs: ExecutionPreviewLeg[] = selectedRows.map((x) => {
      const edit = parkedEdits[x.id];
      return {
        strike: Number(x.strike_price),
        right: x.right as OptionRight,
        side: x.action,
        quantity: Number(edit?.quantity ?? x.quantity),
        premiumPerUnit: Number(edit?.price ?? x.price),
      };
    });
    if (legs.some((l) => !Number.isFinite(l.strike) || !Number.isFinite(l.quantity))) {
      setParkedError("One or more selected parked rows have invalid quantity or strike.");
      return;
    }
    setParkedError(null);
    openExecutionConfirm({
      stockCode,
      exchangeCode,
      expiryDisplay,
      legs,
      sourceParkedIds: selectedRows.map((x) => x.id),
    });
  }, [
    parkedRows,
    parkedSelected,
    executeOneParked,
    parkedEdits,
    openExecutionConfirm,
  ]);

  return (
    <>
      {secondsRemaining !== null ? (
        <RateLimitPauseOverlay secondsRemaining={secondsRemaining} />
      ) : null}
      <Suspense fallback={null}>
        <PrefilledOrderCard />
      </Suspense>

      <section className="app-card min-w-0 space-y-3 p-4">
        <header className="flex items-center justify-between gap-2">
          <h2 className="text-base font-semibold text-zinc-900 dark:text-zinc-100">
            Parked Execution
          </h2>
          <span className="app-text-muted text-xs">
            Edit qty/price before execute
          </span>
        </header>
        {parkedQuery.isLoading ? (
          <div className="app-card-muted border-dashed p-4 text-sm app-text-muted">
            Loading parked orders...
          </div>
        ) : parkedQuery.isError ? (
          <div className="app-alert-error text-xs">
            {parkedQuery.error instanceof Error
              ? parkedQuery.error.message
              : "Could not load parked orders"}
          </div>
        ) : parkedRows.length === 0 ? (
          <div className="app-card-muted border-dashed p-4 text-sm app-text-muted">
            No parked orders.
          </div>
        ) : (
          <div className="space-y-3">
            {parkedError ? (
              <div className="app-alert-error text-xs">{parkedError}</div>
            ) : null}
            <div className="overflow-x-auto rounded-lg border border-zinc-200/80 dark:border-zinc-700/80">
              <table className="min-w-full text-left text-sm">
                <thead className="bg-zinc-50 dark:bg-zinc-900/60">
                  <tr>
                    <th className="px-3 py-2 text-[11px] font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
                      #
                    </th>
                    <th className="px-3 py-2 text-[11px] font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
                      Contract
                    </th>
                    <th className="px-3 py-2 text-center text-[11px] font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
                      Side
                    </th>
                    <th className="px-3 py-2 text-[11px] font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
                      Quantity
                    </th>
                    <th className="px-3 py-2 text-[11px] font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
                      Price
                    </th>
                    <th className="px-3 py-2 text-right text-[11px] font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
                      <span className="sr-only">Run or clone</span>
                    </th>
                    <th className="px-3 py-2 text-center text-[11px] font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
                      {parkedRows.length ? (
                        <input
                          type="checkbox"
                          className={parkedCheckboxClass}
                          checked={allParkedSelected}
                          ref={(el) => {
                            if (!el) return;
                            el.indeterminate =
                              someParkedSelected && !allParkedSelected;
                          }}
                          onChange={(e) => toggleParkedAll(e.target.checked)}
                          aria-label="Select all parked orders"
                        />
                      ) : null}
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800">
                  {parkedRows.map((row, idx) => {
                    const edit = parkedEdits[row.id] ?? {
                      quantity: row.quantity,
                      price: row.price,
                    };
                    const qtyOk = parsePositiveInt(edit.quantity) != null;
                    return (
                      <tr key={row.id}>
                        <td className="px-3 py-2 align-middle tabular-nums text-zinc-400 dark:text-zinc-500">
                          {idx + 1}
                        </td>
                        <td className="px-3 py-2 align-middle">
                          {row.stock_code} {row.expiry_date} {row.right}{" "}
                          {row.strike_price}
                        </td>
                        <td className="px-3 py-2 align-middle text-center">
                          <span className={sidePillClass(row.action)}>
                            {row.action}
                          </span>
                        </td>
                        <td className="px-3 py-2 align-middle">
                          <input
                            type="number"
                            min={1}
                            className="w-24 rounded-md border border-zinc-300 bg-white px-2 py-1 text-sm dark:border-zinc-700 dark:bg-zinc-950"
                            value={edit.quantity}
                            onChange={(e) =>
                              setParkedEdits((prev) => ({
                                ...prev,
                                [row.id]: {
                                  ...edit,
                                  quantity: e.target.value,
                                },
                              }))
                            }
                          />
                        </td>
                        <td className="px-3 py-2 align-middle">
                          <input
                            type="number"
                            step={0.05}
                            className="w-28 rounded-md border border-zinc-300 bg-white px-2 py-1 text-sm dark:border-zinc-700 dark:bg-zinc-950"
                            value={edit.price}
                            onChange={(e) =>
                              setParkedEdits((prev) => ({
                                ...prev,
                                [row.id]: {
                                  ...edit,
                                  price: e.target.value,
                                },
                              }))
                            }
                          />
                        </td>
                        <td className="px-3 py-2 align-middle">
                          <div className="flex flex-wrap justify-end gap-1">
                            <button
                              type="button"
                              className={executeParkedBtnClass}
                              disabled={!qtyOk}
                              aria-label="Execute parked order"
                              onClick={() => executeOneParked(row)}
                            >
                              <ExecuteOrderGlyph />
                            </button>
                            <button
                              type="button"
                              className={cloneToPlaceBtnClass}
                              aria-label="Clone order to Place Order"
                              onClick={(e) => cloneParkedToPlace(row, e)}
                            >
                              <CloneOrderGlyph />
                            </button>
                          </div>
                        </td>
                        <td className="px-3 py-2 text-center align-middle">
                          <input
                            type="checkbox"
                            className={parkedCheckboxClass}
                            checked={parkedSelected.has(row.id)}
                            onChange={(e) =>
                              toggleParkedOne(row.id, e.target.checked)
                            }
                            aria-label={`Select parked order ${row.stock_code}`}
                          />
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            {parkedSelected.size > 0 || parkedDeleteManyMut.isPending ? (
              <div className={ordersCancelBarClass}>
                <span className="text-sm text-zinc-600 dark:text-zinc-400">
                  {formatQtyIndian(parkedSelected.size)} parked selected
                </span>
                <button
                  type="button"
                  className="app-btn-primary h-10 min-h-10 shrink-0 whitespace-nowrap"
                  disabled={parkedSelected.size === 0}
                  onClick={executeSelectedParked}
                >
                  Execute selected
                </button>
                <button
                  type="button"
                  className={[
                    "app-btn-secondary h-10 min-h-10 shrink-0 whitespace-nowrap",
                    parkedDeleteManyMut.isPending ? "cursor-wait" : "",
                  ].join(" ")}
                  disabled={
                    parkedSelected.size === 0 || parkedDeleteManyMut.isPending
                  }
                  aria-busy={parkedDeleteManyMut.isPending}
                  onClick={() =>
                    parkedDeleteManyMut.mutate(Array.from(parkedSelected))
                  }
                >
                  <AsyncLabelSpan
                    busy={parkedDeleteManyMut.isPending}
                    idleLabel="Cancel selected"
                    busyLabel="Cancelling…"
                    className="font-semibold"
                  />
                </button>
              </div>
            ) : null}
          </div>
        )}
      </section>

      {bookQuery.isLoading ? (
        <div className="app-card mt-6 p-4">Loading order book…</div>
      ) : bookQuery.error ? (
        <div className="app-alert-error mt-6">
          Unable to load order book:{" "}
          {bookQuery.error instanceof Error
            ? bookQuery.error.message
            : "Unknown error"}
        </div>
      ) : (
        <section className="app-card mt-6 min-w-0 space-y-3 p-4">
          <header className="space-y-1">
            <div className="flex items-center justify-between gap-2">
              <h2 className="text-base font-semibold text-zinc-900 dark:text-zinc-100">
                Order Book
              </h2>
              <span className="app-text-muted hidden text-right uppercase tracking-wide sm:block sm:max-w-[14rem]">
                Broker messages
              </span>
            </div>
          </header>

          <BookMessages key={brokerMessagesKey} messages={messages} />

          <div className="orders-date-surface overflow-visible">
            <p className="orders-date-caption">Date range</p>
            <div className="flex flex-col gap-4 sm:flex-row sm:flex-wrap sm:items-center sm:justify-between sm:gap-4">
              <div className="flex min-w-0 flex-1 flex-col gap-3 overflow-visible sm:flex-row sm:items-center sm:gap-3">
                <OrderBookDatePopover
                  id="order-book-start"
                  label="Start date"
                  value={inputStart}
                  onChange={setDraftStart}
                />
                <span className="orders-date-sep sm:px-0.5" aria-hidden>
                  to
                </span>
                <OrderBookDatePopover
                  id="order-book-end"
                  label="End date"
                  value={inputEnd}
                  onChange={setDraftEnd}
                />
              </div>
              <button
                type="button"
                className="orders-fetch-btn"
                onClick={applyDateRange}
                disabled={!(inputStart && inputEnd)}
              >
                Fetch orders
              </button>
            </div>
          </div>

          {cancelMut.isError ? (
            <div className="app-alert-error text-xs">
              {cancelMut.error instanceof Error
                ? cancelMut.error.message
                : "Cancel failed"}
            </div>
          ) : null}

          {!groups || groups.length === 0 ? (
            <div className="app-card-muted border-dashed p-8 text-center text-sm app-text-muted">
              No orders in this date range.
            </div>
          ) : (
            <form
              className="space-y-3"
              onSubmit={(e) => {
                e.preventDefault();
                const ids = Array.from(selected);
                if (!ids.length || !groups) return;
                cancelMut.mutate({
                  order_ids: ids,
                  cancel_details: ids.map((id) =>
                    cancelDetailForOrderKey(id, groups),
                  ),
                });
              }}
            >
              <div className="hidden overflow-hidden rounded-lg border border-zinc-200/90 bg-white shadow-sm ring-1 ring-zinc-950/[0.04] dark:border-zinc-800 dark:bg-zinc-900/40 dark:shadow-none dark:ring-white/[0.06] md:block">
                <div className="overflow-x-auto">
                  <table className="min-w-full text-left text-sm text-zinc-800 dark:text-zinc-200">
                    <thead className="sticky top-0 z-[1] border-b border-zinc-200/90 bg-zinc-50/95 backdrop-blur-md dark:border-zinc-800 dark:bg-zinc-900/95">
                      <tr>
                        <th className="px-4 py-3 text-[11px] font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
                          #
                        </th>
                        <th className="px-4 py-3 text-[11px] font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
                          Group
                        </th>
                        <th className="px-4 py-3 text-center text-[11px] font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
                          Side
                        </th>
                        <th className="px-4 py-3 text-center text-[11px] font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
                          Ordered
                        </th>
                        <th className="px-4 py-3 text-center text-[11px] font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
                          Cancelled
                        </th>
                        <th className="px-4 py-3 text-center text-[11px] font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
                          Expired
                        </th>
                        <th className="px-4 py-3 text-center text-[11px] font-semibold uppercase tracking-wider text-sky-600 dark:text-sky-400">
                          Open
                        </th>
                        <th className="px-4 py-3 text-center text-[11px] font-semibold uppercase tracking-wider text-emerald-600 dark:text-emerald-400">
                          Executed
                        </th>
                        <th className="px-4 py-3 text-right text-[11px] font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
                          <span className="sr-only">Expand group</span>
                        </th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800/90">
                      {groups.map((g, idx) => {
                        const isOpen = !!expanded[g.group];
                        const groupLabel = String(g.group_option ?? g.group ?? "Group");
                        const toggleGroupRow = () =>
                          setExpanded((prev) => ({
                            ...prev,
                            [g.group]: !prev[g.group],
                          }));
                        return (
                          <Fragment key={g.group}>
                            <tr
                              className="cursor-pointer transition-colors hover:bg-zinc-50/90 dark:hover:bg-zinc-800/35"
                              role="button"
                              tabIndex={0}
                              aria-expanded={isOpen}
                              aria-label={
                                isOpen
                                  ? `Collapse orders for ${groupLabel}`
                                  : `Expand orders for ${groupLabel}`
                              }
                              onClick={toggleGroupRow}
                              onKeyDown={(e) => {
                                if (e.key === "Enter" || e.key === " ") {
                                  e.preventDefault();
                                  toggleGroupRow();
                                }
                              }}
                            >
                              <td className="px-4 py-3.5 align-middle tabular-nums text-zinc-400 dark:text-zinc-500">
                                {idx + 1}
                              </td>
                              <td className="px-4 py-3.5 align-middle font-medium text-zinc-900 dark:text-zinc-50">
                                {g.group_option}
                              </td>
                              <td className="px-4 py-3.5 align-middle text-center">
                                <span className={sidePillClass(g.group_action)}>
                                  {g.group_action}
                                </span>
                              </td>
                              <td className="px-4 py-3.5 align-middle text-center tabular-nums text-zinc-700 dark:text-zinc-300">
                                {formatQtyIndian(g.group_ordered)}
                              </td>
                              <td className="px-4 py-3.5 align-middle text-center tabular-nums text-zinc-700 dark:text-zinc-300">
                                {formatQtyIndian(g.group_cancelled)}
                              </td>
                              <td className="px-4 py-3.5 align-middle text-center tabular-nums text-zinc-700 dark:text-zinc-300">
                                {formatQtyIndian(g.group_expired)}
                              </td>
                              <td className="px-4 py-3.5 align-middle text-center tabular-nums font-medium text-sky-700 dark:text-sky-300">
                                {formatQtyIndian(g.group_open)}
                              </td>
                              <td className="px-4 py-3.5 align-middle text-center tabular-nums font-medium text-emerald-700 dark:text-emerald-300">
                                {formatQtyIndian(g.group_executed)}
                              </td>
                              <td className="px-4 py-3.5 align-middle text-right text-zinc-500 dark:text-zinc-400">
                                <ChevronGlyph expanded={isOpen} />
                              </td>
                            </tr>
                            {isOpen ? (
                              <tr className="bg-zinc-50/50 dark:bg-zinc-950/40">
                                <td colSpan={9} className="p-0">
                                  <div className="border-t border-zinc-200/80 p-3 dark:border-zinc-800/80">
                                    <div className="overflow-hidden rounded-md border border-zinc-200/90 bg-white shadow-inner dark:border-zinc-700/90 dark:bg-zinc-950/60">
                                      <table className="min-w-full text-left text-sm">
                                        <thead>
                                          <tr className="border-b border-zinc-100 bg-zinc-50/90 dark:border-zinc-800 dark:bg-zinc-900/80">
                                            <th className="px-4 py-2.5 text-[11px] font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
                                              #
                                            </th>
                                            <th className="px-4 py-2.5 text-[11px] font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
                                              Option
                                            </th>
                                            <th className="px-4 py-2.5 text-center text-[11px] font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
                                              Exch.
                                            </th>
                                            <th className="px-4 py-2.5 text-center text-[11px] font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
                                              Side
                                            </th>
                                            <th className="px-4 py-2.5 text-center text-[11px] font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
                                              Qty
                                            </th>
                                            <th className="px-4 py-2.5 text-center text-[11px] font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
                                              Open
                                            </th>
                                            <th className="px-4 py-2.5 text-center text-[11px] font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
                                              LTP
                                            </th>
                                            <th className="px-4 py-2.5 text-center text-[11px] font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
                                              Price
                                            </th>
                                            <th className="px-4 py-2.5 text-[11px] font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
                                              Status
                                            </th>
                                            <th className="w-10 px-1 py-2.5 text-center text-[11px] font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
                                              <span className="sr-only">
                                                Clone to Place Order
                                              </span>
                                            </th>
                                            <th className="px-4 py-2.5 text-center text-[11px] font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
                                              {(g.group_open ?? 0) > 0 ? (
                                                <input
                                                  type="checkbox"
                                                  className="h-[1.125rem] w-[1.125rem] cursor-pointer rounded border-zinc-300 text-sky-600 accent-sky-600 focus:ring-sky-500/30 dark:border-zinc-600 dark:bg-zinc-900 dark:accent-sky-500"
                                                  checked={groupAllSelected(g)}
                                                  ref={(el) => {
                                                    if (!el) return;
                                                    el.indeterminate =
                                                      groupSomeSelected(g) &&
                                                      !groupAllSelected(g);
                                                  }}
                                                  onChange={(e) =>
                                                    toggleGroup(
                                                      g,
                                                      e.target.checked,
                                                    )
                                                  }
                                                  aria-label={`Select all cancelable in ${g.group_option}`}
                                                />
                                              ) : null}
                                            </th>
                                          </tr>
                                        </thead>
                                        <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800/80">
                                          {(g.group_orders ?? []).map((o, j) => {
                                            const key = `${o.order_id ?? ""}|${o.exchange_code ?? ""}`;
                                            return (
                                              <tr
                                                key={key || j}
                                                className="transition-colors hover:bg-sky-50/40 dark:hover:bg-sky-950/20"
                                              >
                                                <td className="px-4 py-2.5 align-middle tabular-nums text-zinc-400 dark:text-zinc-500">
                                                  {j + 1}
                                                </td>
                                                <td className="px-4 py-2.5 align-middle font-medium text-zinc-900 dark:text-zinc-100">
                                                  {o.option}
                                                </td>
                                                <td className="px-4 py-2.5 align-middle text-center text-xs text-zinc-600 dark:text-zinc-400">
                                                  {formatExchangeDisplay(
                                                    o.exchange_code,
                                                  )}
                                                </td>
                                                <td className="px-4 py-2.5 align-middle text-center">
                                                  <span
                                                    className={sidePillClass(
                                                      o.action,
                                                    )}
                                                  >
                                                    {o.action}
                                                  </span>
                                                </td>
                                                <td className="px-4 py-2.5 align-middle text-center tabular-nums">
                                                  {formatQtyIndian(o.quantity)}
                                                </td>
                                                <td className="px-4 py-2.5 align-middle text-center tabular-nums">
                                                  {formatQtyIndian(o.open_quantity)}
                                                </td>
                                                <td className="px-4 py-2.5 align-middle text-center tabular-nums text-zinc-600 dark:text-zinc-400">
                                                  {g.group_ltp != null
                                                    ? `₹${g.group_ltp}`
                                                    : "—"}
                                                </td>
                                                <td className="px-4 py-2.5 align-middle text-center tabular-nums text-zinc-600 dark:text-zinc-400">
                                                  {o.price != null
                                                    ? `₹${o.price}`
                                                    : "—"}
                                                </td>
                                                <td className="px-4 py-2.5 align-middle">
                                                  <span
                                                    className={statusChipClass(
                                                      o.status,
                                                    )}
                                                  >
                                                    {o.status}
                                                  </span>
                                                </td>
                                                <td className="px-1 py-2.5 align-middle text-center">
                                                  <button
                                                    type="button"
                                                    className={cloneToPlaceBtnClass}
                                                    aria-label="Clone order to Place Order"
                                                    onClick={(e) =>
                                                      cloneOrderToPlace(o, e)
                                                    }
                                                  >
                                                    <CloneOrderGlyph />
                                                  </button>
                                                </td>
                                                <td className="px-4 py-2.5 align-middle text-center">
                                                  {o.cancelable && o.order_id ? (
                                                    <input
                                                      type="checkbox"
                                                      className="h-[1.125rem] w-[1.125rem] cursor-pointer rounded border-zinc-300 text-sky-600 accent-sky-600 focus:ring-sky-500/30 dark:border-zinc-600 dark:bg-zinc-900 dark:accent-sky-500"
                                                      checked={selected.has(
                                                        key,
                                                      )}
                                                      onChange={(e) =>
                                                        toggleOne(
                                                          key,
                                                          e.target.checked,
                                                        )
                                                      }
                                                      aria-label={`Select order ${o.order_id}`}
                                                    />
                                                  ) : null}
                                                </td>
                                              </tr>
                                            );
                                          })}
                                        </tbody>
                                      </table>
                                    </div>
                                  </div>
                                </td>
                              </tr>
                            ) : null}
                          </Fragment>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>

              {(groups.some((g) => (g.group_open ?? 0) > 0) &&
                selected.size > 0) ||
              cancelMut.isPending ? (
                <div className={ordersCancelBarClass}>
                  <span className="text-sm text-zinc-600 dark:text-zinc-400">
                    {formatQtyIndian(selected.size)} order(s) selected
                  </span>
                  <button
                    type="submit"
                    disabled={cancelMut.isPending || selected.size === 0}
                    aria-busy={cancelMut.isPending}
                    className={[
                      "app-btn-primary h-10 min-h-10 shrink-0 whitespace-nowrap",
                      cancelMut.isPending ? "cursor-wait" : "",
                    ].join(" ")}
                  >
                    <AsyncLabelSpan
                      busy={cancelMut.isPending}
                      idleLabel="Cancel selected"
                      busyLabel="Cancelling…"
                      className="font-semibold"
                    />
                  </button>
                </div>
              ) : null}

              {/* Mobile: card layout */}
              <div className="space-y-3 md:hidden">
                {groups.map((g) => (
                  <div key={g.group} className="app-card-muted overflow-hidden">
                    <details>
                      <summary className="cursor-pointer list-none px-3 py-2.5 text-base font-medium text-zinc-900 dark:text-zinc-100">
                        <span className="block">{g.group_option}</span>
                        <span className="text-sm font-normal text-zinc-500 dark:text-zinc-400">
                          {g.group_action} · Open {formatQtyIndian(g.group_open)}{" "}
                          · Exec {formatQtyIndian(g.group_executed)}
                        </span>
                      </summary>
                      <div className="space-y-2 border-t border-zinc-200/80 px-3 py-2.5 text-sm text-zinc-600 dark:border-zinc-700/80 dark:text-zinc-400">
                        {(g.group_open ?? 0) > 0 ? (
                          <label className="flex items-center gap-2 font-medium">
                            <input
                              type="checkbox"
                              className="h-[1.125rem] w-[1.125rem] rounded border-zinc-400"
                              checked={groupAllSelected(g)}
                              ref={(el) => {
                                if (!el) return;
                                el.indeterminate =
                                  groupSomeSelected(g) &&
                                  !groupAllSelected(g);
                              }}
                              onChange={(e) =>
                                toggleGroup(g, e.target.checked)
                              }
                            />
                            Select all cancelable in group
                          </label>
                        ) : null}
                        {(g.group_orders ?? []).map((o, j) => {
                          const key = `${o.order_id ?? ""}|${o.exchange_code ?? ""}`;
                          const exch = formatExchangeDisplay(o.exchange_code);
                          return (
                            <div
                              key={key || j}
                              className="rounded-lg border border-zinc-200/80 bg-white/80 p-3 text-sm dark:border-zinc-700/80 dark:bg-zinc-950/40"
                            >
                              <div className="flex items-start justify-between gap-2">
                                <p className="min-w-0 flex-1 text-base font-medium text-zinc-900 dark:text-zinc-100">
                                  {o.option}
                                </p>
                                <button
                                  type="button"
                                  className={[
                                    cloneToPlaceBtnClass,
                                    "shrink-0 self-start",
                                  ].join(" ")}
                                  aria-label="Clone order to Place Order"
                                  onClick={(e) => cloneOrderToPlace(o, e)}
                                >
                                  <CloneOrderGlyph />
                                </button>
                              </div>
                              {exch ? <p>Exchange: {exch}</p> : null}
                              <p>Side: {o.action}</p>
                              <p>Qty: {formatQtyIndian(o.quantity)}</p>
                              <p>Open: {formatQtyIndian(o.open_quantity)}</p>
                              <p>
                                Price:{" "}
                                {o.price != null ? `₹${o.price}` : "—"} | LTP:{" "}
                                {g.group_ltp != null ? `₹${g.group_ltp}` : "—"}
                              </p>
                              <p>Status: {o.status}</p>
                              {o.cancelable && o.order_id ? (
                                <label className="mt-1 flex items-center gap-2">
                                  <input
                                    type="checkbox"
                                    className="h-[1.125rem] w-[1.125rem] rounded border-zinc-400"
                                    checked={selected.has(key)}
                                    onChange={(e) =>
                                      toggleOne(key, e.target.checked)
                                    }
                                  />
                                  Select to cancel
                                </label>
                              ) : null}
                            </div>
                          );
                        })}
                      </div>
                    </details>
                  </div>
                ))}
              </div>
            </form>
          )}
        </section>
      )}
    </>
  );
}

export default function OrdersPage() {
  return (
    <AppShell contentWidth="wide">
      <OrdersBody />
    </AppShell>
  );
}
