// Client component so auth cookies are included with browser fetch.
"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Fragment,
  Suspense,
  useCallback,
  useMemo,
  useRef,
  useState,
  type MouseEvent,
} from "react";
import { useRouter } from "next/navigation";
import { AppShell } from "@/components/layout/AppShell";
import { HelpLink } from "@/components/help/HelpLink";
import { RevokedTradingPageGuard } from "@/components/license/RevokedTradingPageGuard";
import type { ExecutionPreviewLeg } from "@/components/shared/order/OrderExecutionConfirmDialog";
import { OrderBookDatePopover } from "@/components/order/OrderBookDatePopover";
import { useOrderConfirm } from "@/components/shared/order/OrderConfirmProvider";
import { OptionTypeBadge } from "@/components/shared/badges/OptionTypeBadge";
import { OrderSideBadge } from "@/components/shared/badges/OrderSideBadge";
import { PrefilledOrderCard } from "@/components/shared/order/PrefilledOrderCard";
import { AsyncLabelSpan } from "@/components/ui/AsyncLabelSpan";
import { Checkbox } from "@/components/ui/Checkbox";
import { Modal } from "@/components/ui/Modal";
import { apiClient } from "@/lib/api-client";
import { fetchBreakChunkDefaults } from "@/lib/break-chunk-defaults";
import {
  formatOptionSymbolLabel,
  snapQuantityToLotMultiple,
} from "@/lib/strategy-builder/leg-ui-helpers";
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

/** Groups parked rows by contract so lot size is looked up once per (stock, exchange, expiry). */
function parkedLotKey(
  stockCode: string,
  exchangeCode: string | undefined,
  expiryDate: string,
): string {
  return `${stockCode}|${exchangeCode || "NFO"}|${expiryDate}`;
}

const ordersCancelBarClass =
  "flex flex-wrap items-center justify-end gap-3 border-t border-border-soft bg-panel2 px-[18px] py-3";

const cancelOutlineBtnClass =
  "inline-flex h-[34px] items-center justify-center rounded-lg border border-down/40 bg-transparent px-3.5 py-1.5 text-xs font-semibold text-down transition hover:bg-down-tint focus:outline-none focus-visible:ring-2 focus-visible:ring-down/35 disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50";

const fetchOrdersBtnClass =
  "inline-flex items-center justify-center rounded-lg border border-accent/40 bg-transparent px-3.5 py-1.5 text-xs font-semibold text-accent-strong transition hover:bg-accent-tint focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/35 disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50";

const cancelOutlineBtnSmallClass =
  "inline-flex items-center justify-center rounded-md border border-down/40 bg-transparent px-2.5 py-1 text-hint font-semibold text-down transition hover:bg-down-tint focus:outline-none focus-visible:ring-2 focus-visible:ring-down/35 disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50";

const cloneToPlaceBtnClass =
  "inline-flex rounded-md p-1.5 text-faint transition hover:bg-border-soft hover:text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/40";

/** Canonical leg label e.g. "BSESEN.09-Jul-2026.82000" (Type column already shows PE/CE). */
function bookGroupLegLabel(g: BookGroup): string {
  const first = g.group_orders?.[0];
  const stock = String(first?.stock_code ?? "").trim();
  const expiry = String(first?.expiry_date ?? "").trim();
  const strike = Number(first?.strike_price);
  if (!stock || !expiry || !Number.isFinite(strike)) {
    return String(g.group_option ?? g.group ?? "Group");
  }
  return formatOptionSymbolLabel(stock, expiry, strike);
}

/** Leg label plus order count, mirroring Portfolio's "stock · expiry · N legs" group title. */
function bookGroupTitle(g: BookGroup): string {
  const count = g.group_orders?.length ?? 0;
  return `${bookGroupLegLabel(g)} · ${count} order${count === 1 ? "" : "s"}`;
}

function statusChipClass(status: string | undefined): string {
  const s = String(status ?? "")
    .trim()
    .toLowerCase();
  const base = "inline-flex max-w-[11rem] truncate rounded-full px-2.5 py-0.5 text-micro font-bold uppercase tracking-[.05em] ";
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
      fill="currentColor"
      stroke="none"
      aria-hidden
    >
      <polygon points="5 3 19 12 5 21 5 3" />
    </svg>
  );
}

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

function CancelWarnGlyph() {
  return (
    <svg
      width="17"
      height="17"
      viewBox="0 0 24 24"
      fill="none"
      stroke="var(--down)"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M12 9v4M12 17h.01" />
      <path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" />
    </svg>
  );
}

type CancelPrompt =
  | { kind: "book-single"; key: string }
  | { kind: "book-bulk" }
  | { kind: "parked-bulk" };

function CancelConfirmDialog({
  prompt,
  pending,
  onClose,
  onConfirm,
}: {
  prompt: CancelPrompt | null;
  pending: boolean;
  onClose: () => void;
  onConfirm: () => void;
}) {
  const titleId = "cancel-confirm-title";
  const title =
    prompt?.kind === "book-single"
      ? "Cancel this order?"
      : prompt?.kind === "book-bulk"
        ? "Cancel selected orders?"
        : "Cancel selected parked orders?";
  const body =
    prompt?.kind === "parked-bulk"
      ? "This removes the selected parked order(s). This cannot be undone."
      : "This will cancel the open, unexecuted quantity. Filled quantity is unaffected. This cannot be undone.";

  return (
    <Modal
      open={prompt != null}
      onClose={onClose}
      pending={pending}
      titleId={titleId}
      role="alertdialog"
      panelClassName="w-full max-w-[380px] rounded-[14px] border border-border bg-panel p-[22px] shadow-pop"
    >
      <div className="mb-3 flex items-center gap-2.5">
        <span className="flex size-8 shrink-0 items-center justify-center rounded-[9px] bg-down-tint">
          <CancelWarnGlyph />
        </span>
        <span id={titleId} className="text-[15px] font-bold text-foreground">
          {title}
        </span>
      </div>
      <p className="mb-[18px] text-heading leading-relaxed text-muted">{body}</p>
      <div className="flex gap-2.5">
        <button
          type="button"
          className="app-btn-secondary h-10 min-h-10 flex-1"
          disabled={pending}
          onClick={onClose}
        >
          Keep order{prompt?.kind !== "book-single" ? "s" : ""}
        </button>
        <button
          type="button"
          className="app-btn-danger h-10 min-h-10 flex-1"
          disabled={pending}
          aria-busy={pending}
          onClick={onConfirm}
        >
          <AsyncLabelSpan
            busy={pending}
            idleLabel="Cancel order"
            busyLabel="Cancelling…"
          />
        </button>
      </div>
    </Modal>
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
  const [cancelPrompt, setCancelPrompt] = useState<CancelPrompt | null>(null);

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

  // Lazily fetched + cached per (stock, exchange, expiry) the first time a row's quantity
  // is edited, so the fields below can snap to lot multiples the same way Place/Basket/
  // Strategy do, without pre-fetching for every parked contract up front.
  const parkedLotSizeCacheRef = useRef<Record<string, number>>({});

  const snapParkedQuantity = useCallback(
    async (row: ParkedOrderListItem, edit: { quantity: string; price: string }) => {
      const key = parkedLotKey(row.stock_code, row.exchange_code, row.expiry_date);
      let lotSize = parkedLotSizeCacheRef.current[key];
      if (!lotSize) {
        try {
          const res = await fetchBreakChunkDefaults({
            stock_code: row.stock_code,
            exchange_code: row.exchange_code || "NFO",
            expiry_date: row.expiry_date,
          });
          if (res.ok && res.lot_size && res.lot_size > 0) {
            lotSize = res.lot_size;
            parkedLotSizeCacheRef.current[key] = lotSize;
          }
        } catch {
          return;
        }
      }
      if (!lotSize) return;
      const n = parsePositiveInt(edit.quantity);
      if (n == null) return;
      const snapped = String(snapQuantityToLotMultiple(n, lotSize));
      if (snapped === edit.quantity) return;
      setParkedEdits((prev) => ({
        ...prev,
        [row.id]: { ...edit, quantity: snapped },
      }));
    },
    [],
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
      setCancelPrompt(null);
      invalidateTradingShellQueries(queryClient);
    },
  });

  const parkedDeleteManyMut = useMutation({
    mutationFn: (ids: string[]) => deleteParkedOrdersMany(ids),
    onSuccess: () => {
      setParkedSelected(new Set());
      setCancelPrompt(null);
      queryClient.invalidateQueries({
        queryKey: ["parked-orders"],
      });
    },
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

  const confirmCancel = useCallback(() => {
    if (!cancelPrompt) return;
    if (cancelPrompt.kind === "parked-bulk") {
      parkedDeleteManyMut.mutate(Array.from(parkedSelected));
      return;
    }
    if (!groups) return;
    const ids =
      cancelPrompt.kind === "book-single"
        ? [cancelPrompt.key]
        : Array.from(selected);
    if (!ids.length) return;
    cancelMut.mutate({
      order_ids: ids,
      cancel_details: ids.map((id) => cancelDetailForOrderKey(id, groups)),
    });
  }, [cancelPrompt, parkedDeleteManyMut, parkedSelected, groups, selected, cancelMut]);

  const cancelPromptPending =
    cancelPrompt?.kind === "parked-bulk"
      ? parkedDeleteManyMut.isPending
      : cancelMut.isPending;

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
    <div className="space-y-4">
      <div>
        <h1 className="app-text-title">Order Book</h1>
        <p className="mt-0.5 text-sm app-text-muted">
          Parked orders and today&apos;s order activity
        </p>
      </div>

      <Suspense fallback={null}>
        <PrefilledOrderCard />
      </Suspense>

      <section className="app-card min-w-0 overflow-hidden">
        <div className="flex flex-wrap items-center justify-between gap-2.5 border-b border-border-soft px-[18px] py-3.5">
          <span className="text-hint font-bold uppercase tracking-[.07em] text-faint">
            Parked execution
          </span>
          <span className="text-table text-muted">
            Edit qty/price before execute
          </span>
        </div>
        <div className="space-y-3 px-[18px] py-4">
          <p className="text-sm text-muted">
            Orders placed when the market is closed are saved here until you
            execute them to ICICI.{" "}
            <HelpLink topicId="parked-orders" className="text-sm">
              How parking works
            </HelpLink>
          </p>
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
            <div className="hidden -mx-[18px] md:block">
              <div className="overflow-x-auto">
                <table className="min-w-full text-left text-sm">
                  <thead className="bg-panel2">
                    <tr>
                      <th className="py-2 pl-[18px] pr-3 text-center text-heading font-semibold uppercase tracking-wider text-faint">
                        {parkedRows.length ? (
                          <Checkbox
                            checked={allParkedSelected}
                            indeterminate={someParkedSelected && !allParkedSelected}
                            onChange={toggleParkedAll}
                            aria-label="Select all parked orders"
                          />
                        ) : null}
                      </th>
                      <th className="px-3 py-2 text-heading font-semibold uppercase tracking-wider text-faint">
                        Contract
                      </th>
                      <th className="px-3 py-2 text-center text-heading font-semibold uppercase tracking-wider text-faint">
                        Type
                      </th>
                      <th className="px-3 py-2 text-center text-heading font-semibold uppercase tracking-wider text-faint">
                        Side
                      </th>
                      <th className="px-3 py-2 text-heading font-semibold uppercase tracking-wider text-faint">
                        Quantity
                      </th>
                      <th className="px-3 py-2 text-heading font-semibold uppercase tracking-wider text-faint">
                        Price
                      </th>
                      <th className="py-2 pl-3 pr-[18px] text-right text-heading font-semibold uppercase tracking-wider text-faint">
                        <span className="sr-only">Run or clone</span>
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border-soft">
                    {parkedRows.map((row) => {
                      const edit = parkedEdits[row.id] ?? {
                        quantity: row.quantity,
                        price: row.price,
                      };
                      const qtyOk = parsePositiveInt(edit.quantity) != null;
                      return (
                        <tr key={row.id}>
                          <td className="py-2 pl-[18px] pr-3 text-center align-middle">
                            <Checkbox
                              checked={parkedSelected.has(row.id)}
                              onChange={(checked) =>
                                toggleParkedOne(row.id, checked)
                              }
                              aria-label={`Select parked order ${row.stock_code}`}
                            />
                          </td>
                          <td className="px-3 py-2 align-middle">
                            {formatOptionSymbolLabel(
                              row.stock_code,
                              row.expiry_date,
                              Number(row.strike_price),
                            )}
                          </td>
                          <td className="px-3 py-2 align-middle text-center">
                            <OptionTypeBadge right={row.right} />
                          </td>
                          <td className="px-3 py-2 align-middle text-center">
                            <OrderSideBadge side={row.action} />
                          </td>
                          <td className="px-3 py-2 align-middle">
                            <input
                              type="number"
                              min={1}
                              className="w-24 rounded-t-[2px] border-0 border-b border-muted bg-background dark:bg-elevated px-2.5 py-1.5 font-mono text-sm font-semibold text-foreground transition hover:border-accent focus:border-accent-strong focus:bg-panel focus:outline-none"
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
                              onBlur={() => snapParkedQuantity(row, edit)}
                            />
                          </td>
                          <td className="px-3 py-2 align-middle">
                            <input
                              type="number"
                              step={0.05}
                              className="w-28 rounded-t-[2px] border-0 border-b border-muted bg-background dark:bg-elevated px-2.5 py-1.5 font-mono text-sm font-semibold text-foreground transition hover:border-accent focus:border-accent-strong focus:bg-panel focus:outline-none"
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
                          <td className="py-2 pl-3 pr-[18px] align-middle">
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
                    <Checkbox
                      checked={allParkedSelected}
                      indeterminate={someParkedSelected && !allParkedSelected}
                      onChange={toggleParkedAll}
                      aria-label="Select all parked orders"
                    />
                    All
                  </label>
                </div>
              ) : null}
              {parkedRows.map((row) => {
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
                          <p className="text-base font-medium text-foreground">
                            {formatOptionSymbolLabel(
                              row.stock_code,
                              row.expiry_date,
                              Number(row.strike_price),
                            )}
                          </p>
                          <div className="mt-1 flex items-center gap-1.5">
                            <OptionTypeBadge right={row.right} />
                            <OrderSideBadge side={row.action} />
                          </div>
                        </div>
                        <Checkbox
                          checked={parkedSelected.has(row.id)}
                          onChange={(checked) =>
                            toggleParkedOne(row.id, checked)
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
                            className="w-full max-w-full rounded-t-[2px] border-0 border-b border-muted bg-background dark:bg-elevated px-2.5 py-1.5 font-mono text-sm transition hover:border-accent focus:border-accent-strong focus:bg-panel focus:outline-none"
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
                            onBlur={() => snapParkedQuantity(row, edit)}
                          />
                        </label>
                        <label className="block min-w-0 space-y-1">
                          <span className="text-xs font-medium text-muted">
                            Price
                          </span>
                          <input
                            type="number"
                            step={0.05}
                            className="w-full max-w-full rounded-t-[2px] border-0 border-b border-muted bg-background dark:bg-elevated px-2.5 py-1.5 font-mono text-sm transition hover:border-accent focus:border-accent-strong focus:bg-panel focus:outline-none"
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
                  className={cancelOutlineBtnClass}
                  disabled={
                    parkedSelected.size === 0 || parkedDeleteManyMut.isPending
                  }
                  aria-busy={parkedDeleteManyMut.isPending}
                  onClick={() => setCancelPrompt({ kind: "parked-bulk" })}
                >
                  <AsyncLabelSpan
                    busy={parkedDeleteManyMut.isPending}
                    idleLabel="Cancel selected"
                    busyLabel="Cancelling…"
                  />
                </button>
                <button
                  type="button"
                  className="app-btn-primary h-[34px] shrink-0 whitespace-nowrap"
                  disabled={parkedSelected.size === 0}
                  onClick={executeSelectedParked}
                >
                  Execute selected
                </button>
              </div>
            ) : null}
          </div>
        )}
        </div>
      </section>

      {bookQuery.isLoading ? (
        <div className="app-card space-y-3 p-4">
          <div className="h-5 w-28 app-skeleton rounded-sm border-0" />
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="h-9 w-full app-skeleton rounded-sm border-0" />
          ))}
        </div>
      ) : bookQuery.error ? (
        <div className="app-alert-error">
          Unable to load order book:{" "}
          {bookQuery.error instanceof Error
            ? bookQuery.error.message
            : "Unknown error"}
        </div>
      ) : (
        <section className="app-card min-w-0 overflow-hidden">
          <div className="border-b border-border-soft px-[18px] py-3.5">
            <span className="text-hint font-bold uppercase tracking-[.07em] text-faint">
              Order book
            </span>
          </div>

          <div className="px-[18px] py-3">
            <BookMessages key={brokerMessagesKey} messages={messages} />
          </div>

          <div className="flex flex-col gap-3 border-b border-border-soft px-[18px] py-3.5 sm:flex-row sm:flex-wrap sm:items-center">
            <span className="text-table text-muted">Date range</span>
            <div className="flex min-w-0 flex-1 flex-col gap-3 overflow-visible sm:flex-row sm:items-center sm:gap-3">
              <OrderBookDatePopover
                id="order-book-start"
                label="Start date"
                value={inputStart}
                onChange={setDraftStart}
              />
              <span className="shrink-0 text-center text-xs text-faint" aria-hidden>
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
              className={`${fetchOrdersBtnClass} h-[34px] w-full sm:ml-auto sm:w-auto sm:self-center`}
              onClick={applyDateRange}
              disabled={!(inputStart && inputEnd)}
            >
              Fetch orders
            </button>
          </div>

          <div className="px-[18px] py-4">
          {cancelMut.isError ? (
            <div className="app-alert-error mb-3 text-xs">
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
            <div className="space-y-3">
              <div className="hidden min-w-0 -mx-[18px] overflow-x-auto md:block">
                <table className="min-w-full text-left text-sm text-foreground">
                  <thead className="sticky top-0 z-[1] border-b border-border bg-panel2">
                      <tr>
                        <th className="py-3 pl-[18px] pr-4 text-center text-heading font-semibold uppercase tracking-wider text-faint">
                          <span className="sr-only">Select group</span>
                        </th>
                        <th className="px-4 py-3 text-heading font-semibold uppercase tracking-wider text-faint">
                          Group
                        </th>
                        <th className="px-4 py-3 text-center text-heading font-semibold uppercase tracking-wider text-faint">
                          Type
                        </th>
                        <th className="px-4 py-3 text-center text-heading font-semibold uppercase tracking-wider text-faint">
                          Side
                        </th>
                        <th className="px-4 py-3 text-center text-heading font-semibold uppercase tracking-wider text-faint">
                          Ordered
                        </th>
                        <th className="px-4 py-3 text-center text-heading font-semibold uppercase tracking-wider text-faint">
                          Cancelled
                        </th>
                        <th className="px-4 py-3 text-center text-heading font-semibold uppercase tracking-wider text-faint">
                          Expired
                        </th>
                        <th className="px-4 py-3 text-center text-heading font-semibold uppercase tracking-wider text-accent-strong">
                          Open
                        </th>
                        <th className="px-4 py-3 text-center text-heading font-semibold uppercase tracking-wider text-up">
                          Executed
                        </th>
                        <th className="py-3 pl-4 pr-[18px] text-right text-heading font-semibold uppercase tracking-wider text-faint">
                          <span className="sr-only">Expand group</span>
                        </th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-border-soft">
                      {groups.map((g) => {
                        const isOpen = !!expanded[g.group];
                        const groupLabel = bookGroupLegLabel(g);
                        const groupTitle = bookGroupTitle(g);
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
                              <td
                                className="py-3.5 pl-[18px] pr-4 text-center align-middle"
                                onClick={(e) => e.stopPropagation()}
                              >
                                {(g.group_open ?? 0) > 0 ? (
                                  <Checkbox
                                    checked={groupAllSelected(g)}
                                    indeterminate={
                                      groupSomeSelected(g) && !groupAllSelected(g)
                                    }
                                    onChange={(checked) => toggleGroup(g, checked)}
                                    aria-label={`Select all cancelable in ${groupLabel}`}
                                  />
                                ) : null}
                              </td>
                              <td className="px-4 py-3.5 align-middle font-medium text-foreground">
                                {groupTitle}
                              </td>
                              <td className="px-4 py-3.5 align-middle text-center">
                                <OptionTypeBadge right={g.group_orders?.[0]?.right} />
                              </td>
                              <td className="px-4 py-3.5 align-middle text-center">
                                <OrderSideBadge side={g.group_action} />
                              </td>
                              <td className="px-4 py-3.5 align-middle text-center font-mono tabular-nums text-muted">
                                {formatQtyIndian(g.group_ordered)}
                              </td>
                              <td className="px-4 py-3.5 align-middle text-center font-mono tabular-nums text-muted">
                                {formatQtyIndian(g.group_cancelled)}
                              </td>
                              <td className="px-4 py-3.5 align-middle text-center font-mono tabular-nums text-muted">
                                {formatQtyIndian(g.group_expired)}
                              </td>
                              <td className="px-4 py-3.5 align-middle text-center font-mono tabular-nums font-medium text-accent-strong">
                                {formatQtyIndian(g.group_open)}
                              </td>
                              <td className="px-4 py-3.5 align-middle text-center font-mono tabular-nums font-medium text-up">
                                {formatQtyIndian(g.group_executed)}
                              </td>
                              <td className="py-3.5 pl-4 pr-[18px] align-middle text-right text-faint">
                                <ChevronGlyph expanded={isOpen} />
                              </td>
                            </tr>
                            {isOpen ? (
                              <tr className="border-b border-border-soft bg-panel2">
                                <td colSpan={10} className="px-[18px]">
                                  <table className="min-w-full text-left text-sm">
                                    <thead>
                                      <tr className="border-b border-border-soft">
                                        <th className="w-10 px-4 py-2.5 text-center text-heading font-semibold uppercase tracking-wider text-faint">
                                          <span className="sr-only">
                                            Select order
                                          </span>
                                        </th>
                                        <th className="py-2.5 pl-10 pr-4 text-heading font-semibold uppercase tracking-wider text-faint">
                                          Order
                                        </th>
                                        <th className="px-4 py-2.5 text-center text-heading font-semibold uppercase tracking-wider text-faint">
                                          Qty
                                        </th>
                                        <th className="px-4 py-2.5 text-center text-heading font-semibold uppercase tracking-wider text-faint">
                                          Open
                                        </th>
                                        <th className="px-4 py-2.5 text-center text-heading font-semibold uppercase tracking-wider text-faint">
                                          LTP
                                        </th>
                                        <th className="px-4 py-2.5 text-center text-heading font-semibold uppercase tracking-wider text-faint">
                                          Price ₹
                                        </th>
                                        <th className="px-4 py-2.5 text-heading font-semibold uppercase tracking-wider text-faint">
                                          Status
                                        </th>
                                        <th className="w-10 px-1 py-2.5 text-center text-heading font-semibold uppercase tracking-wider text-faint">
                                          <span className="sr-only">
                                            Clone to Place Order
                                          </span>
                                        </th>
                                        <th className="w-20 px-2 py-2.5 text-right text-heading font-semibold uppercase tracking-wider text-faint">
                                          <span className="sr-only">
                                            Cancel
                                          </span>
                                        </th>
                                      </tr>
                                    </thead>
                                    <tbody className="divide-y divide-border-soft">
                                      {(g.group_orders ?? []).map((o, j) => {
                                        const key = `${o.order_id ?? ""}|${o.exchange_code ?? ""}`;
                                        const canCancel =
                                          o.cancelable && !!o.order_id;
                                        return (
                                          <tr
                                            key={key || j}
                                            className="transition-colors hover:bg-accent-tint"
                                          >
                                            <td className="px-4 py-2.5 text-center align-middle">
                                              {canCancel ? (
                                                <Checkbox
                                                  checked={selected.has(key)}
                                                  onChange={(checked) =>
                                                    toggleOne(key, checked)
                                                  }
                                                  aria-label={`Select order ${o.order_id}`}
                                                />
                                              ) : null}
                                            </td>
                                            <td className="py-2.5 pl-10 pr-4 align-middle font-mono text-muted">
                                              #{o.order_id ?? "—"}
                                            </td>
                                            <td className="px-4 py-2.5 align-middle text-center font-mono tabular-nums">
                                              {formatQtyIndian(o.quantity)}
                                            </td>
                                            <td className="px-4 py-2.5 align-middle text-center font-mono tabular-nums">
                                              {formatQtyIndian(o.open_quantity)}
                                            </td>
                                            <td className="px-4 py-2.5 align-middle text-center font-mono tabular-nums text-muted">
                                              <GroupLtpValue
                                                groupId={g.group}
                                                fallback={g.group_ltp}
                                                ltps={groupLtps}
                                                loading={groupLtpLoading}
                                              />
                                            </td>
                                            <td className="px-4 py-2.5 align-middle text-center font-mono tabular-nums text-muted">
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
                                            <td className="px-2 py-2.5 text-right align-middle">
                                              {canCancel ? (
                                                <button
                                                  type="button"
                                                  className={
                                                    cancelOutlineBtnSmallClass
                                                  }
                                                  onClick={() =>
                                                    setCancelPrompt({
                                                      kind: "book-single",
                                                      key,
                                                    })
                                                  }
                                                >
                                                  Cancel
                                                </button>
                                              ) : null}
                                            </td>
                                          </tr>
                                        );
                                      })}
                                    </tbody>
                                  </table>
                                </td>
                              </tr>
                            ) : null}
                          </Fragment>
                        );
                      })}
                    </tbody>
                  </table>
              </div>

              {(groups.some((g) => (g.group_open ?? 0) > 0) &&
                selected.size > 0) ||
              cancelMut.isPending ? (
                <div className={ordersCancelBarClass}>
                  <span className="text-sm text-muted">
                    {formatQtyIndian(selected.size)} order(s) selected
                  </span>
                  <button
                    type="button"
                    disabled={cancelMut.isPending || selected.size === 0}
                    aria-busy={cancelMut.isPending}
                    className={cancelOutlineBtnClass}
                    onClick={() => setCancelPrompt({ kind: "book-bulk" })}
                  >
                    <AsyncLabelSpan
                      busy={cancelMut.isPending}
                      idleLabel="Cancel selected"
                      busyLabel="Cancelling…"
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
                        <span className="block">{bookGroupTitle(g)}</span>
                        <span className="mt-1 flex flex-wrap items-center gap-1.5 text-sm font-normal text-muted">
                          <OptionTypeBadge right={g.group_orders?.[0]?.right} />
                          {g.group_action} · Open {formatQtyIndian(g.group_open)}{" "}
                          · Exec {formatQtyIndian(g.group_executed)}
                        </span>
                      </summary>
                      <div className="space-y-2 border-t border-border-soft px-3 py-2.5 text-sm text-muted">
                        {(g.group_open ?? 0) > 0 ? (
                          <label className="flex items-center gap-2 font-medium">
                            <Checkbox
                              checked={groupAllSelected(g)}
                              indeterminate={
                                groupSomeSelected(g) && !groupAllSelected(g)
                              }
                              onChange={(checked) => toggleGroup(g, checked)}
                              aria-label={`Select all cancelable in ${bookGroupLegLabel(g)}`}
                            />
                            Select all cancelable in group
                          </label>
                        ) : null}
                        {(g.group_orders ?? []).map((o, j) => {
                          const key = `${o.order_id ?? ""}|${o.exchange_code ?? ""}`;
                          const canCancel = o.cancelable && !!o.order_id;
                          return (
                            <div
                              key={key || j}
                              className="rounded-lg border border-border bg-panel2 p-3 text-sm"
                            >
                              <div className="flex items-start justify-between gap-2">
                                <p className="min-w-0 flex-1 font-mono text-base font-medium text-foreground">
                                  #{o.order_id ?? "—"}
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
                              {canCancel ? (
                                <div className="mt-1 flex items-center justify-between gap-2">
                                  <label className="flex items-center gap-2">
                                    <Checkbox
                                      checked={selected.has(key)}
                                      onChange={(checked) =>
                                        toggleOne(key, checked)
                                      }
                                      aria-label={`Select order ${o.order_id}`}
                                    />
                                    Select to cancel
                                  </label>
                                  <button
                                    type="button"
                                    className={cancelOutlineBtnSmallClass}
                                    onClick={() =>
                                      setCancelPrompt({
                                        kind: "book-single",
                                        key,
                                      })
                                    }
                                  >
                                    Cancel
                                  </button>
                                </div>
                              ) : null}
                            </div>
                          );
                        })}
                      </div>
                    </details>
                  </div>
                ))}
              </div>
            </div>
          )}
          </div>
        </section>
      )}

      <CancelConfirmDialog
        prompt={cancelPrompt}
        pending={cancelPromptPending}
        onClose={() => setCancelPrompt(null)}
        onConfirm={confirmCancel}
      />
    </div>
  );
}

export default function OrdersPage() {
  return (
    <AppShell>
      <RevokedTradingPageGuard>
        <OrdersBody />
      </RevokedTradingPageGuard>
    </AppShell>
  );
}
