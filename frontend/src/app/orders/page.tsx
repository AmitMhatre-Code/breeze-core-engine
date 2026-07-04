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
import { HelpLink } from "@/components/help/HelpLink";
import { RevokedTradingPageGuard } from "@/components/license/RevokedTradingPageGuard";
import type { ExecutionPreviewLeg } from "@/components/order/OrderExecutionConfirmDialog";
import { OrderBookDatePopover } from "@/components/order/OrderBookDatePopover";
import { useOrderConfirm } from "@/components/order/OrderConfirmProvider";
import { PrefilledOrderCard } from "@/components/order/PrefilledOrderCard";
import { AsyncLabelSpan } from "@/components/ui/AsyncLabelSpan";
import { apiClient } from "@/lib/api-client";
import {
  fetchBookGroupLtps,
  type BookGroupLtpItem,
} from "@/lib/book-ltp";
import { runCancelOrdersWithPacing } from "@/lib/icici-rate-limit-flow";
import { invalidateTradingShellQueries } from "@/lib/trading-cache";
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
  group_exchange?: string;
  group_ordered?: number;
  group_cancelled?: number;
  group_expired?: number;
  group_open?: number;
  group_executed?: number;
  group_ltp?: number | string;
  group_orders?: BookOrderRow[];
};

function formatGroupLtpText(
  groupId: string | undefined,
  fallback: number | string | null | undefined,
  ltps: Record<string, number | null> | undefined,
): string | null {
  if (groupId && ltps && groupId in ltps) {
    const v = ltps[groupId];
    if (v != null && Number.isFinite(v)) return `₹${v}`;
    return "—";
  }
  if (fallback != null && fallback !== "") return `₹${fallback}`;
  return null;
}

function GroupLtpValue({
  groupId,
  fallback,
  ltps,
  loading,
}: {
  groupId: string | undefined;
  fallback: number | string | null | undefined;
  ltps: Record<string, number | null> | undefined;
  loading: boolean;
}) {
  const text = formatGroupLtpText(groupId, fallback, ltps);
  if (text != null) return <>{text}</>;
  if (loading) {
    return (
      <span
        className="app-skeleton inline-block h-3 w-10 rounded-sm border-0"
        aria-hidden
      />
    );
  }
  return <>—</>;
}

function bookGroupLtpPayload(groups: BookGroup[]): BookGroupLtpItem[] {
  const out: BookGroupLtpItem[] = [];
  for (const g of groups) {
    if ((g.group_open ?? 0) <= 0 || !g.group) continue;
    const first = g.group_orders?.[0];
    if (!first?.stock_code || !first.expiry_date || first.strike_price == null) {
      continue;
    }
    out.push({
      group: g.group,
      stock_code: String(first.stock_code),
      expiry_date: String(first.expiry_date),
      strike_price: first.strike_price,
      right: String(first.right ?? ""),
      exchange_code: String(first.exchange_code ?? g.group_exchange ?? "NFO"),
    });
  }
  return out;
}

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
  "flex flex-wrap items-center justify-end gap-3 rounded-md border border-border bg-panel2 px-4 py-3";

const cloneToPlaceBtnClass =
  "inline-flex rounded-md p-1.5 text-accent-strong transition hover:bg-accent-tint focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/40";

function sidePillClass(action: string | undefined): string {
  const a = String(action ?? "")
    .trim()
    .toLowerCase();
  if (a === "buy" || a.startsWith("buy"))
    return "inline-flex rounded-full bg-up-tint px-2.5 py-0.5 text-[10.5px] font-bold uppercase tracking-[.05em] text-up";
  if (a === "sell" || a.startsWith("sell"))
    return "inline-flex rounded-full bg-down-tint px-2.5 py-0.5 text-[10.5px] font-bold uppercase tracking-[.05em] text-down";
  return "font-medium text-foreground";
}

function statusChipClass(status: string | undefined): string {
  const s = String(status ?? "")
    .trim()
    .toLowerCase();
  const base = "inline-flex max-w-[11rem] truncate rounded-full px-2.5 py-0.5 text-[10.5px] font-bold uppercase tracking-[.05em] ";
  if (s.includes("execut")) return `${base} bg-up-tint text-up`;
  if (s.includes("reject")) return `${base} bg-down-tint text-down`;
  if (s.includes("cancel")) return `${base} bg-panel2 text-faint`;
  if (s.includes("partial") || s.includes("open") || s.includes("request"))
    return `${base} bg-accent-tint text-accent-strong`;
  if (s.includes("expir")) return `${base} bg-amber-tint text-amber-accent`;
  return `${base} bg-panel2 text-faint`;
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
  "h-[1.125rem] w-[1.125rem] cursor-pointer rounded border-border text-accent-strong accent-accent-strong focus:ring-accent/30";

const executeParkedBtnClass =
  "inline-flex rounded-md p-1.5 text-accent-strong transition hover:bg-accent-tint focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 disabled:cursor-not-allowed disabled:opacity-40";

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
  if (type === "alert-success") return "border-up/30 bg-up-tint text-up";
  if (type === "alert-danger") return "border-down/30 bg-down-tint text-down";
  if (type === "alert-warning")
    return "border-amber-accent/30 bg-amber-tint text-amber-accent";
  return "border-border bg-panel2 text-foreground";
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
    "shrink-0 rounded-md p-1 text-current opacity-70 transition hover:bg-black/5 hover:opacity-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 dark:hover:bg-white/10";

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
  const { wait } = useRateLimitCountdown();
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
      invalidateTradingShellQueries(queryClient);
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
  const ltpPayload = useMemo(
    () => (groups?.length ? bookGroupLtpPayload(groups) : []),
    [groups],
  );
  const ltpQuery = useQuery({
    queryKey: [
      "book",
      "ltp",
      appliedRange?.start ?? "__default__",
      appliedRange?.end ?? "__default__",
      ltpPayload.map((g) => g.group).join("|"),
    ],
    queryFn: () => fetchBookGroupLtps(ltpPayload),
    enabled: ltpPayload.length > 0 && !bookQuery.isLoading,
    staleTime: 30_000,
    refetchOnWindowFocus: false,
  });
  const groupLtps = ltpQuery.data?.ltps;
  const groupLtpLoading = ltpQuery.isLoading || ltpQuery.isFetching;
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
      <Suspense fallback={null}>
        <PrefilledOrderCard />
      </Suspense>

      <section className="app-card min-w-0 space-y-3 p-4">
        <header className="space-y-1">
          <div className="flex items-center justify-between gap-2">
            <h2 className="text-base font-semibold text-foreground">
              Parked Execution
            </h2>
            <span className="app-text-muted text-xs">
              Edit qty/price before execute
            </span>
          </div>
          <p className="text-sm text-muted">
            Orders placed when the market is closed are saved here until you
            execute them to ICICI.{" "}
            <HelpLink topicId="parked-orders" className="text-sm">
              How parking works
            </HelpLink>
          </p>
        </header>
        {parkedQuery.isLoading ? (
          <div className="app-card-muted space-y-2 border-dashed p-4">
            <div className="h-4 w-40 app-skeleton rounded-sm border-0" />
            {[0, 1].map((i) => (
              <div key={i} className="h-9 w-full app-skeleton rounded-sm border-0" />
            ))}
          </div>
        ) : parkedQuery.isError ? (
          <div className="app-alert-error text-xs">
            {parkedQuery.error instanceof Error
              ? parkedQuery.error.message
              : "Could not load parked orders"}
          </div>
        ) : parkedRows.length === 0 ? (
          <div className="app-card-muted border-dashed p-4 text-sm app-text-muted">
            No parked orders. After-hours placements from Place Order, Basket
            Order, or Strategy Builder appear here for execution when the market
            opens.{" "}
            <HelpLink topicId="parked-orders" className="text-sm">
              Learn more
            </HelpLink>
          </div>
        ) : (
          <div className="space-y-3">
            {parkedError ? (
              <div className="app-alert-error text-xs">{parkedError}</div>
            ) : null}
            <div className="hidden md:block">
              <div className="app-table-wrap">
                <table className="min-w-full text-left text-sm">
                  <thead className="bg-panel2">
                    <tr>
                      <th className="px-3 py-2 text-[13px] font-semibold uppercase tracking-wider text-faint">
                        #
                      </th>
                      <th className="px-3 py-2 text-[13px] font-semibold uppercase tracking-wider text-faint">
                        Contract
                      </th>
                      <th className="px-3 py-2 text-center text-[13px] font-semibold uppercase tracking-wider text-faint">
                        Side
                      </th>
                      <th className="px-3 py-2 text-[13px] font-semibold uppercase tracking-wider text-faint">
                        Quantity
                      </th>
                      <th className="px-3 py-2 text-[13px] font-semibold uppercase tracking-wider text-faint">
                        Price
                      </th>
                      <th className="px-3 py-2 text-right text-[13px] font-semibold uppercase tracking-wider text-faint">
                        <span className="sr-only">Run or clone</span>
                      </th>
                      <th className="px-3 py-2 text-center text-[13px] font-semibold uppercase tracking-wider text-faint">
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
                  <tbody className="divide-y divide-border-soft">
                    {parkedRows.map((row, idx) => {
                      const edit = parkedEdits[row.id] ?? {
                        quantity: row.quantity,
                        price: row.price,
                      };
                      const qtyOk = parsePositiveInt(edit.quantity) != null;
                      return (
                        <tr key={row.id}>
                          <td className="px-3 py-2 align-middle tabular-nums text-faint">
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
                              className="w-24 rounded-md border border-border bg-panel2 px-2 py-1 font-mono text-sm"
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
                              className="w-28 rounded-md border border-border bg-panel2 px-2 py-1 font-mono text-sm"
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
            </div>

            <div className="space-y-3 md:hidden">
              {parkedRows.length ? (
                <div className="flex items-center justify-between gap-2 rounded-lg border border-border bg-panel2 px-3 py-2.5">
                  <span className="text-xs font-medium uppercase tracking-wide text-muted">
                    Bulk select
                  </span>
                  <label className="flex items-center gap-2 text-sm text-muted">
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
                    />
                    All
                  </label>
                </div>
              ) : null}
              {parkedRows.map((row, idx) => {
                const edit = parkedEdits[row.id] ?? {
                  quantity: row.quantity,
                  price: row.price,
                };
                const qtyOk = parsePositiveInt(edit.quantity) != null;
                return (
                  <div
                    key={row.id}
                    className="rounded-lg border border-border bg-panel2 p-3 text-sm text-muted"
                  >
                    <div className="space-y-3">
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0 flex-1">
                          <p className="tabular-nums text-xs text-faint">
                            #{idx + 1}
                          </p>
                          <p className="text-base font-medium text-foreground">
                            {row.stock_code} {row.expiry_date} {row.right}{" "}
                            {row.strike_price}
                          </p>
                          <div className="mt-1">
                            <span className={sidePillClass(row.action)}>
                              {row.action}
                            </span>
                          </div>
                        </div>
                        <input
                          type="checkbox"
                          className={parkedCheckboxClass}
                          checked={parkedSelected.has(row.id)}
                          onChange={(e) =>
                            toggleParkedOne(row.id, e.target.checked)
                          }
                          aria-label={`Select parked order ${row.stock_code}`}
                        />
                      </div>
                      <div className="grid gap-3 sm:grid-cols-2">
                        <label className="block min-w-0 space-y-1">
                          <span className="text-xs font-medium text-muted">
                            Quantity
                          </span>
                          <input
                            type="number"
                            min={1}
                            className="w-full max-w-full rounded-md border border-border bg-panel2 px-2 py-1.5 font-mono text-sm"
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
                        </label>
                        <label className="block min-w-0 space-y-1">
                          <span className="text-xs font-medium text-muted">
                            Price
                          </span>
                          <input
                            type="number"
                            step={0.05}
                            className="w-full max-w-full rounded-md border border-border bg-panel2 px-2 py-1.5 font-mono text-sm"
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
                        </label>
                      </div>
                      <div className="flex flex-wrap items-center justify-end gap-1 border-t border-border-soft pt-2">
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
                    </div>
                  </div>
                );
              })}
            </div>
            {parkedSelected.size > 0 || parkedDeleteManyMut.isPending ? (
              <div className={ordersCancelBarClass}>
                <span className="text-sm text-muted">
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
        <div className="app-card mt-6 space-y-3 p-4">
          <div className="h-5 w-28 app-skeleton rounded-sm border-0" />
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="h-9 w-full app-skeleton rounded-sm border-0" />
          ))}
        </div>
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
              <h2 className="text-base font-semibold text-foreground">
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
              <div className="hidden min-w-0 overflow-hidden rounded-lg border border-border bg-panel md:block">
                <div className="min-w-0 overflow-x-auto">
                  <table className="min-w-full text-left text-sm text-foreground">
                    <thead className="sticky top-0 z-[1] border-b border-border bg-panel2">
                      <tr>
                        <th className="px-4 py-3 text-[13px] font-semibold uppercase tracking-wider text-faint">
                          #
                        </th>
                        <th className="px-4 py-3 text-[13px] font-semibold uppercase tracking-wider text-faint">
                          Group
                        </th>
                        <th className="px-4 py-3 text-center text-[13px] font-semibold uppercase tracking-wider text-faint">
                          Side
                        </th>
                        <th className="px-4 py-3 text-center text-[13px] font-semibold uppercase tracking-wider text-faint">
                          Ordered
                        </th>
                        <th className="px-4 py-3 text-center text-[13px] font-semibold uppercase tracking-wider text-faint">
                          Cancelled
                        </th>
                        <th className="px-4 py-3 text-center text-[13px] font-semibold uppercase tracking-wider text-faint">
                          Expired
                        </th>
                        <th className="px-4 py-3 text-center text-[13px] font-semibold uppercase tracking-wider text-accent-strong">
                          Open
                        </th>
                        <th className="px-4 py-3 text-center text-[13px] font-semibold uppercase tracking-wider text-up">
                          Executed
                        </th>
                        <th className="px-4 py-3 text-right text-[13px] font-semibold uppercase tracking-wider text-faint">
                          <span className="sr-only">Expand group</span>
                        </th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border-soft">
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
                              className="cursor-pointer transition-colors hover:bg-panel2"
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
                              <td className="px-4 py-3.5 align-middle tabular-nums text-faint">
                                {idx + 1}
                              </td>
                              <td className="px-4 py-3.5 align-middle font-medium text-foreground">
                                {g.group_option}
                              </td>
                              <td className="px-4 py-3.5 align-middle text-center">
                                <span className={sidePillClass(g.group_action)}>
                                  {g.group_action}
                                </span>
                              </td>
                              <td className="px-4 py-3.5 align-middle text-center tabular-nums text-muted">
                                {formatQtyIndian(g.group_ordered)}
                              </td>
                              <td className="px-4 py-3.5 align-middle text-center tabular-nums text-muted">
                                {formatQtyIndian(g.group_cancelled)}
                              </td>
                              <td className="px-4 py-3.5 align-middle text-center tabular-nums text-muted">
                                {formatQtyIndian(g.group_expired)}
                              </td>
                              <td className="px-4 py-3.5 align-middle text-center tabular-nums font-medium text-accent-strong">
                                {formatQtyIndian(g.group_open)}
                              </td>
                              <td className="px-4 py-3.5 align-middle text-center tabular-nums font-medium text-up">
                                {formatQtyIndian(g.group_executed)}
                              </td>
                              <td className="px-4 py-3.5 align-middle text-right text-muted">
                                <ChevronGlyph expanded={isOpen} />
                              </td>
                            </tr>
                            {isOpen ? (
                              <tr className="bg-panel2">
                                <td colSpan={9} className="p-0">
                                  <div className="border-t border-border-soft p-3">
                                    <div className="overflow-hidden rounded-md border border-border bg-panel2">
                                      <table className="min-w-full text-left text-sm">
                                        <thead>
                                          <tr className="border-b border-border-soft bg-panel2">
                                            <th className="px-4 py-2.5 text-[13px] font-semibold uppercase tracking-wider text-faint">
                                              #
                                            </th>
                                            <th className="px-4 py-2.5 text-[13px] font-semibold uppercase tracking-wider text-faint">
                                              Option
                                            </th>
                                            <th className="px-4 py-2.5 text-center text-[13px] font-semibold uppercase tracking-wider text-faint">
                                              Exch.
                                            </th>
                                            <th className="px-4 py-2.5 text-center text-[13px] font-semibold uppercase tracking-wider text-faint">
                                              Side
                                            </th>
                                            <th className="px-4 py-2.5 text-center text-[13px] font-semibold uppercase tracking-wider text-faint">
                                              Qty
                                            </th>
                                            <th className="px-4 py-2.5 text-center text-[13px] font-semibold uppercase tracking-wider text-faint">
                                              Open
                                            </th>
                                            <th className="px-4 py-2.5 text-center text-[13px] font-semibold uppercase tracking-wider text-faint">
                                              LTP
                                            </th>
                                            <th className="px-4 py-2.5 text-center text-[13px] font-semibold uppercase tracking-wider text-faint">
                                              Price
                                            </th>
                                            <th className="px-4 py-2.5 text-[13px] font-semibold uppercase tracking-wider text-faint">
                                              Status
                                            </th>
                                            <th className="w-10 px-1 py-2.5 text-center text-[13px] font-semibold uppercase tracking-wider text-faint">
                                              <span className="sr-only">
                                                Clone to Place Order
                                              </span>
                                            </th>
                                            <th className="px-4 py-2.5 text-center text-[13px] font-semibold uppercase tracking-wider text-faint">
                                              {(g.group_open ?? 0) > 0 ? (
                                                <input
                                                  type="checkbox"
                                                  className="h-[1.125rem] w-[1.125rem] cursor-pointer rounded border-border text-accent-strong accent-accent-strong focus:ring-accent/30"
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
                                        <tbody className="divide-y divide-border-soft">
                                          {(g.group_orders ?? []).map((o, j) => {
                                            const key = `${o.order_id ?? ""}|${o.exchange_code ?? ""}`;
                                            return (
                                              <tr
                                                key={key || j}
                                                className="transition-colors hover:bg-accent-tint"
                                              >
                                                <td className="px-4 py-2.5 align-middle tabular-nums text-faint">
                                                  {j + 1}
                                                </td>
                                                <td className="px-4 py-2.5 align-middle font-medium text-foreground">
                                                  {o.option}
                                                </td>
                                                <td className="px-4 py-2.5 align-middle text-center text-xs text-muted">
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
                                                <td className="px-4 py-2.5 align-middle text-center tabular-nums text-muted">
                                                  <GroupLtpValue
                                                    groupId={g.group}
                                                    fallback={g.group_ltp}
                                                    ltps={groupLtps}
                                                    loading={groupLtpLoading}
                                                  />
                                                </td>
                                                <td className="px-4 py-2.5 align-middle text-center tabular-nums text-muted">
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
                                                      className="h-[1.125rem] w-[1.125rem] cursor-pointer rounded border-border text-accent-strong accent-accent-strong focus:ring-accent/30"
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
                  <span className="text-sm text-muted">
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
                      <summary className="cursor-pointer list-none px-3 py-2.5 text-base font-medium text-foreground">
                        <span className="block">{g.group_option}</span>
                        <span className="text-sm font-normal text-muted">
                          {g.group_action} · Open {formatQtyIndian(g.group_open)}{" "}
                          · Exec {formatQtyIndian(g.group_executed)}
                        </span>
                      </summary>
                      <div className="space-y-2 border-t border-border-soft px-3 py-2.5 text-sm text-muted">
                        {(g.group_open ?? 0) > 0 ? (
                          <label className="flex items-center gap-2 font-medium">
                            <input
                              type="checkbox"
                              className="h-[1.125rem] w-[1.125rem] rounded border-border"
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
                              className="rounded-lg border border-border bg-panel2 p-3 text-sm"
                            >
                              <div className="flex items-start justify-between gap-2">
                                <p className="min-w-0 flex-1 text-base font-medium text-foreground">
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
                                <GroupLtpValue
                                  groupId={g.group}
                                  fallback={g.group_ltp}
                                  ltps={groupLtps}
                                  loading={groupLtpLoading}
                                />
                              </p>
                              <p>Status: {o.status}</p>
                              {o.cancelable && o.order_id ? (
                                <label className="mt-1 flex items-center gap-2">
                                  <input
                                    type="checkbox"
                                    className="h-[1.125rem] w-[1.125rem] rounded border-border"
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
      <RevokedTradingPageGuard>
        <OrdersBody />
      </RevokedTradingPageGuard>
    </AppShell>
  );
}
