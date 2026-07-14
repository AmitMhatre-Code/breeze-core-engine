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
import Link from "next/link";
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
import {
  runCancelOrdersWithPacing,
  runModifyLegWithPacing,
  type LegModifyOrderRef,
} from "@/lib/icici-rate-limit-flow";
import { invalidateTradingShellQueries } from "@/lib/trading-cache";
import {
  deleteParkedOrdersMany,
  fetchParkedOrders,
  parkedOrderToConfirmPayload,
  type ParkedOrderListItem,
} from "@/lib/parked-orders";
import {
  buildExitRuleRows,
  isExitRuleActive,
  type ExitRuleEffectiveStatus,
  type ExitRuleRow,
  type RuleSpawnedOrderRow,
} from "@/lib/orders/exit-rules";
import { fetchSquareOffRulesForExitBoard } from "@/lib/portfolio/squareoff-rules";
import { fetchAllGttExitOrders } from "@/lib/portfolio/gtt-exit-orders";
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
  pending_quantity?: number | string;
  price?: number | string;
  status?: string;
  cancelable?: boolean;
  modifiable?: boolean;
  stock_code?: string;
  expiry_date?: string;
  strike_price?: number | string;
  right?: string;
  product_type?: string;
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

/** Common shape both BookOrderRow and RuleSpawnedOrderRow satisfy — enough to build a
 * leg-modify request regardless of which table the leg came from. */
type LegModifyableOrder = {
  order_id?: string;
  exchange_code?: string;
  quantity?: number | string;
  pending_quantity?: number | string;
  price?: number | string;
  status?: string;
  modifiable?: boolean;
};

function toIntOrZero(raw: number | string | undefined): number {
  if (raw == null) return 0;
  const n = typeof raw === "number" ? raw : parseInt(String(raw), 10);
  return Number.isFinite(n) ? n : 0;
}

function buildLegModifyOrders(orders: LegModifyableOrder[]): LegModifyOrderRef[] {
  return orders
    .filter((o) => !!o.order_id)
    .map((o) => ({
      order_id: String(o.order_id),
      exchange_code: String(o.exchange_code ?? "NFO"),
      quantity: toIntOrZero(o.quantity),
      pending_quantity: toIntOrZero(o.pending_quantity),
      status: String(o.status ?? ""),
      price: o.price != null ? String(o.price) : null,
    }));
}

/** Sum of already-filled quantity across every order in a leg — the floor a new
 * total quantity can never go below. Mirrors the backend's plan_leg_redistribution. */
function legFilledFloor(orders: LegModifyOrderRef[]): number {
  return orders.reduce((sum, o) => sum + (o.quantity - o.pending_quantity), 0);
}

function legHasModifiable(orders: LegModifyableOrder[]): boolean {
  return orders.some((o) => o.modifiable && o.order_id);
}

/** A leg + its current orders, as displayed, ready to hand to ModifyLegDialog. */
type ModifyLegTarget = {
  contract: {
    stock_code: string;
    expiry_date: string;
    strike_price: string;
    right: string;
    product_type: string;
    exchange_code: string;
    action: "Buy" | "Sell";
  };
  contractLabel: string;
  orders: LegModifyOrderRef[];
  currentQuantity: number;
  currentPrice: string | null;
  ruleId?: string;
  scripKey?: string;
};

/** Groups a flat list of PB/SL rule-spawned orders (which may span several legs
 * within one rule) by contract+action, so each leg gets its own Modify action —
 * mirrors the `option+action-exchange_code` grouping key `processor.group_orders`
 * already uses for the main Order Book. */
function groupRuleOrdersByLeg(orders: RuleSpawnedOrderRow[]): {
  key: string;
  contractLabel: string;
  orders: RuleSpawnedOrderRow[];
}[] {
  const byKey = new Map<string, RuleSpawnedOrderRow[]>();
  for (const o of orders) {
    const key = [
      o.stock_code ?? "",
      o.expiry_date ?? "",
      o.strike_price ?? "",
      o.right ?? "",
      o.action ?? "",
      o.exchange_code ?? "",
    ].join("|");
    const list = byKey.get(key);
    if (list) list.push(o);
    else byKey.set(key, [o]);
  }
  return Array.from(byKey.entries()).map(([key, legOrders]) => {
    const first = legOrders[0];
    const contractLabel = `${first.stock_code ?? ""}-${first.expiry_date ?? ""}-${first.strike_price ?? ""}-${first.right ?? ""}`;
    return { key, contractLabel, orders: legOrders };
  });
}

type BookDataResponse = {
  messages: BookMessage[];
  grouped_orders: BookGroup[] | null;
  start: string;
  end: string;
  orders_failed: boolean;
  rule_spawned_orders: RuleSpawnedOrderRow[] | null;
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

const modifyOutlineBtnSmallClass =
  "inline-flex items-center justify-center rounded-md border border-accent/40 bg-transparent px-2.5 py-1 text-hint font-semibold text-accent-strong transition hover:bg-accent-tint focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/35 disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50";

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

const exitRuleScopeGroupClass =
  "inline-flex rounded-full border border-border px-2.5 py-0.5 text-micro font-bold uppercase tracking-[.05em] text-muted";
const exitRuleScopeLegClass =
  "inline-flex rounded-full bg-gtt-tint px-2.5 py-0.5 text-micro font-bold uppercase tracking-[.05em] text-gtt";

function exitRuleStatusChipClass(status: ExitRuleEffectiveStatus): string {
  const base =
    "inline-flex rounded-full px-2.5 py-0.5 text-micro font-bold uppercase tracking-[.05em] ";
  switch (status) {
    case "armed":
      return `${base} bg-accent-tint text-accent-strong`;
    case "triggered":
      return `${base} bg-amber-tint text-amber-accent`;
    case "fired":
      return `${base} border border-amber-accent/45 bg-transparent text-amber-accent`;
    case "exited":
      return `${base} bg-up-tint text-up`;
    case "fire_failed":
      return `${base} bg-down-tint text-down`;
  }
}

function exitRuleStatusLabel(status: ExitRuleEffectiveStatus): string {
  switch (status) {
    case "armed":
      return "Armed";
    case "triggered":
      return "Triggered";
    case "fired":
      return "Fired";
    case "exited":
      return "Exited";
    case "fire_failed":
      return "Fire Failed";
  }
}

/** Canonical title for a rule row, mirroring `bookGroupTitle`'s "stock · expiry · N
 * legs" shape for Group rules, or a single option symbol label for Leg·GTT rules. */
function exitRuleTitle(row: ExitRuleRow): string {
  if (row.kind === "group") {
    const count = row.legCount ?? 0;
    return `${row.stockCode} · ${row.expiryDisplay}${
      count ? ` · ${count} leg${count === 1 ? "" : "s"}` : ""
    }`;
  }
  const strike = row.strikePrice != null ? Number(row.strikePrice) : NaN;
  if (row.stockCode && row.expiryDisplay && Number.isFinite(strike) && row.right) {
    return formatOptionSymbolLabel(row.stockCode, row.expiryDisplay, strike);
  }
  return row.stockCode || "Leg";
}

function formatExitRuleAmount(value: number | null, kind: "group" | "leg_gtt"): string {
  if (value == null) return "—";
  if (kind === "group") {
    return `₹${Math.round(value).toLocaleString("en-IN")}`;
  }
  return `₹${value.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

/** Same lookup shape as `cancelDetailForOrderKey`, over a flat exit-rule order list
 * instead of `BookGroup[]`. */
function cancelDetailForExitRuleOrderKey(
  key: string,
  orders: RuleSpawnedOrderRow[],
): { option: string; open_quantity: number } {
  for (const o of orders) {
    const k = `${o.order_id ?? ""}|${o.exchange_code ?? ""}`;
    if (k === key) {
      const raw = o.open_quantity ?? o.quantity ?? 0;
      const open_quantity =
        typeof raw === "number" ? raw : parseInt(String(raw), 10) || 0;
      return { option: String(o.option ?? "").trim(), open_quantity };
    }
  }
  return { option: "", open_quantity: 0 };
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
  | { kind: "parked-bulk" }
  | { kind: "exitrule-single"; key: string }
  | { kind: "exitrule-bulk" };

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
    prompt?.kind === "book-single" || prompt?.kind === "exitrule-single"
      ? "Cancel this order?"
      : prompt?.kind === "book-bulk" || prompt?.kind === "exitrule-bulk"
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
          Keep order
          {prompt?.kind !== "book-single" && prompt?.kind !== "exitrule-single" ? "s" : ""}
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

function ModifyLegDialog({
  target,
  pending,
  error,
  onClose,
  onConfirm,
}: {
  target: ModifyLegTarget | null;
  pending: boolean;
  error: string | null;
  onClose: () => void;
  onConfirm: (quantity: string, price: string) => void;
}) {
  const titleId = "modify-leg-title";
  const [quantity, setQuantity] = useState("");
  const [price, setPrice] = useState("");
  const lastKeyRef = useRef<string | null>(null);
  const key = target ? `${target.contractLabel}|${target.orders.map((o) => o.order_id).join(",")}` : null;
  if (key !== lastKeyRef.current) {
    lastKeyRef.current = key;
    if (target) {
      setQuantity(String(target.currentQuantity));
      setPrice(target.currentPrice ?? "");
    }
  }

  const floor = target ? legFilledFloor(target.orders) : 0;
  const parsedQty = parsePositiveInt(quantity);
  const qtyValid = parsedQty != null && parsedQty >= floor;
  const qtyChanged = target ? String(parsedQty ?? "") !== String(target.currentQuantity) : false;
  const priceChanged = target ? price.trim() !== "" && price.trim() !== (target.currentPrice ?? "") : false;
  const canConfirm = !!target && qtyValid && (qtyChanged || priceChanged) && !pending;

  return (
    <Modal
      open={target != null}
      onClose={onClose}
      pending={pending}
      titleId={titleId}
      role="alertdialog"
      panelClassName="w-full max-w-[420px] rounded-[14px] border border-border bg-panel p-[22px] shadow-pop"
    >
      <div className="mb-3 flex items-center gap-2.5">
        <span id={titleId} className="text-[15px] font-bold text-foreground">
          Modify {target?.contractLabel ?? "order"}
        </span>
      </div>
      <p className="mb-3 text-heading leading-relaxed text-muted">
        Changes apply to the whole leg — the app will cancel, resize, or add
        orders as needed to reach the new total. Already-filled quantity
        ({formatQtyIndian(floor)}) can&apos;t be reduced.
      </p>
      <div className="mb-3 space-y-3">
        <label className="block text-sm">
          <span className="mb-1 block font-medium text-foreground">Quantity</span>
          <input
            type="number"
            min={floor}
            className="app-input w-full"
            value={quantity}
            onChange={(e) => setQuantity(e.target.value)}
            disabled={pending}
          />
          {target && qtyChanged ? (
            <span className="mt-1 block font-mono text-xs text-accent-strong">
              {formatQtyIndian(target.currentQuantity)} → {quantity || "—"}
            </span>
          ) : null}
          {!qtyValid && quantity.trim() !== "" ? (
            <span className="mt-1 block text-xs text-down">
              Cannot be less than the filled quantity ({formatQtyIndian(floor)}).
            </span>
          ) : null}
        </label>
        <label className="block text-sm">
          <span className="mb-1 block font-medium text-foreground">Price</span>
          <input
            type="number"
            step={0.05}
            className="app-input w-full"
            value={price}
            onChange={(e) => setPrice(e.target.value)}
            disabled={pending}
          />
          {target && priceChanged ? (
            <span className="mt-1 block font-mono text-xs text-accent-strong">
              ₹{target.currentPrice ?? "0"} → ₹{price}
            </span>
          ) : null}
        </label>
      </div>
      {error ? <p className="mb-3 text-xs text-down">{error}</p> : null}
      <div className="flex gap-2.5">
        <button
          type="button"
          className="app-btn-secondary h-10 min-h-10 flex-1"
          disabled={pending}
          onClick={onClose}
        >
          Cancel
        </button>
        <button
          type="button"
          className="app-btn-primary h-10 min-h-10 flex-1"
          disabled={!canConfirm}
          aria-busy={pending}
          onClick={() => onConfirm(quantity.trim(), price.trim())}
        >
          <AsyncLabelSpan busy={pending} idleLabel="Modify order" busyLabel="Modifying…" />
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
  const [modifyTarget, setModifyTarget] = useState<ModifyLegTarget | null>(null);
  const [modifyError, setModifyError] = useState<string | null>(null);

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

  const modifyLegMut = useMutation({
    mutationFn: (payload: { new_quantity: string; new_price: string }) => {
      if (!modifyTarget) throw new Error("Nothing to modify");
      return runModifyLegWithPacing({
        stock_code: modifyTarget.contract.stock_code,
        expiry_date: modifyTarget.contract.expiry_date,
        strike_price: modifyTarget.contract.strike_price,
        right: modifyTarget.contract.right,
        product_type: modifyTarget.contract.product_type,
        exchange_code: modifyTarget.contract.exchange_code,
        action: modifyTarget.contract.action,
        orders: modifyTarget.orders,
        new_quantity: payload.new_quantity,
        new_price: payload.new_price || undefined,
        rule_id: modifyTarget.ruleId,
        scrip_key: modifyTarget.scripKey,
        onRateLimitWait: wait,
      });
    },
    onSuccess: (res) => {
      if (!res.success) {
        setModifyError(
          res.failures.length
            ? res.failures.map((f) => `${f.ref}: ${f.error}`).join("; ")
            : "Could not modify this leg",
        );
        return;
      }
      setModifyTarget(null);
      setModifyError(null);
      invalidateTradingShellQueries(queryClient);
      queryClient.invalidateQueries({ queryKey: ["exit-rules", "squareoff"] });
      queryClient.invalidateQueries({ queryKey: ["exit-rules", "gtt"] });
    },
    onError: (e) =>
      setModifyError(e instanceof Error ? e.message : "Could not modify this leg"),
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

  // ---- Profit Booking / Stop Loss ----
  const [exitRuleSelected, setExitRuleSelected] = useState<Set<string>>(
    () => new Set(),
  );
  const [exitRuleExpanded, setExitRuleExpanded] = useState<
    Record<string, boolean>
  >({});
  const [exitRuleTab, setExitRuleTab] = useState<"active" | "history">(
    "active",
  );

  const squareOffExitBoardQuery = useQuery({
    queryKey: ["exit-rules", "squareoff"],
    queryFn: fetchSquareOffRulesForExitBoard,
    refetchOnWindowFocus: false,
  });
  const gttExitBoardQuery = useQuery({
    queryKey: ["exit-rules", "gtt"],
    queryFn: fetchAllGttExitOrders,
    refetchOnWindowFocus: false,
  });

  const exitRuleRows = useMemo(
    () =>
      buildExitRuleRows(
        squareOffExitBoardQuery.data ?? [],
        gttExitBoardQuery.data ?? [],
        data?.rule_spawned_orders ?? [],
      ),
    [squareOffExitBoardQuery.data, gttExitBoardQuery.data, data?.rule_spawned_orders],
  );
  const exitRuleRowsForTab = useMemo(
    () =>
      exitRuleRows.filter((row) =>
        exitRuleTab === "active"
          ? isExitRuleActive(row.effectiveStatus)
          : !isExitRuleActive(row.effectiveStatus),
      ),
    [exitRuleRows, exitRuleTab],
  );
  const allExitRuleOrders = useMemo(
    () => exitRuleRows.flatMap((row) => row.orders),
    [exitRuleRows],
  );

  const toggleExitRuleRow = useCallback((rowId: string) => {
    setExitRuleExpanded((prev) => ({ ...prev, [rowId]: !prev[rowId] }));
  }, []);

  const toggleExitRuleOne = useCallback((value: string, checked: boolean) => {
    setExitRuleSelected((prev) => {
      const next = new Set(prev);
      if (checked) next.add(value);
      else next.delete(value);
      return next;
    });
  }, []);

  const toggleExitRuleGroup = useCallback(
    (row: ExitRuleRow, checked: boolean) => {
      setExitRuleSelected((prev) => {
        const next = new Set(prev);
        for (const o of row.orders) {
          if (!o.cancelable || !o.order_id) continue;
          const key = `${o.order_id}|${o.exchange_code ?? ""}`;
          if (checked) next.add(key);
          else next.delete(key);
        }
        return next;
      });
    },
    [],
  );

  const exitRuleGroupAllSelected = useCallback(
    (row: ExitRuleRow) => {
      const cancelable = row.orders.filter((o) => o.cancelable && o.order_id);
      if (!cancelable.length) return false;
      return cancelable.every((o) =>
        exitRuleSelected.has(`${o.order_id}|${o.exchange_code ?? ""}`),
      );
    },
    [exitRuleSelected],
  );

  const exitRuleGroupSomeSelected = useCallback(
    (row: ExitRuleRow) => {
      const cancelable = row.orders.filter((o) => o.cancelable && o.order_id);
      return cancelable.some((o) =>
        exitRuleSelected.has(`${o.order_id}|${o.exchange_code ?? ""}`),
      );
    },
    [exitRuleSelected],
  );

  const exitRuleCancelMut = useMutation({
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
      setExitRuleSelected(new Set());
      setCancelPrompt(null);
      invalidateTradingShellQueries(queryClient);
      queryClient.invalidateQueries({ queryKey: ["exit-rules", "squareoff"] });
      queryClient.invalidateQueries({ queryKey: ["exit-rules", "gtt"] });
    },
  });

  const openModifyForRuleLeg = useCallback(
    (row: ExitRuleRow, leg: { key: string; contractLabel: string; orders: RuleSpawnedOrderRow[] }) => {
      const first = leg.orders[0];
      if (!first) return;
      const legOrders = buildLegModifyOrders(leg.orders);
      if (!legOrders.length) return;
      const legQty = leg.orders.reduce((sum, o) => sum + toIntOrZero(o.quantity), 0);
      const firstWithPrice = leg.orders.find((o) => o.price != null);
      const sameRuleScrip =
        row.kind === "group" &&
        leg.orders.every(
          (o) =>
            o.exit_rule_source === "squareoff_rule" &&
            o.exit_rule_scrip_key === first.exit_rule_scrip_key,
        );
      setModifyError(null);
      setModifyTarget({
        contract: {
          stock_code: String(first.stock_code ?? ""),
          expiry_date: String(first.expiry_date ?? ""),
          strike_price: String(first.strike_price ?? ""),
          right: String(first.right ?? ""),
          product_type: String(first.product_type ?? "Options"),
          exchange_code: String(first.exchange_code ?? "NFO"),
          action: (first.action === "Sell" ? "Sell" : "Buy") as "Buy" | "Sell",
        },
        contractLabel: leg.contractLabel,
        orders: legOrders,
        currentQuantity: legQty,
        currentPrice: firstWithPrice?.price != null ? String(firstWithPrice.price) : null,
        ruleId: sameRuleScrip && first.exit_rule_scrip_key ? row.id : undefined,
        scripKey: sameRuleScrip ? first.exit_rule_scrip_key : undefined,
      });
    },
    [],
  );

  const cloneExitRuleOrderToPlace = useCallback(
    (o: RuleSpawnedOrderRow, e: MouseEvent) => {
      e.preventDefault();
      e.stopPropagation();
      const payload = buildPlaceOrderCloneFromBookRow(o);
      if (!payload) return;
      setPlaceOrderClonePayload(payload);
      router.push("/place-order");
    },
    [router],
  );

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

  const openModifyForBookGroup = useCallback((g: BookGroup) => {
    const orders = g.group_orders ?? [];
    const first = orders[0];
    if (!first) return;
    const legOrders = buildLegModifyOrders(orders);
    if (!legOrders.length) return;
    setModifyError(null);
    setModifyTarget({
      contract: {
        stock_code: String(first.stock_code ?? ""),
        expiry_date: String(first.expiry_date ?? ""),
        strike_price: String(first.strike_price ?? ""),
        right: String(first.right ?? ""),
        product_type: String(first.product_type ?? "Options"),
        exchange_code: String(first.exchange_code ?? g.group_exchange ?? "NFO"),
        action: (g.group_action === "Sell" ? "Sell" : "Buy") as "Buy" | "Sell",
      },
      contractLabel: bookGroupTitle(g),
      orders: legOrders,
      currentQuantity: g.group_ordered ?? 0,
      currentPrice: first.price != null ? String(first.price) : null,
    });
  }, []);

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
    if (cancelPrompt.kind === "exitrule-single" || cancelPrompt.kind === "exitrule-bulk") {
      const ids =
        cancelPrompt.kind === "exitrule-single"
          ? [cancelPrompt.key]
          : Array.from(exitRuleSelected);
      if (!ids.length) return;
      exitRuleCancelMut.mutate({
        order_ids: ids,
        cancel_details: ids.map((id) =>
          cancelDetailForExitRuleOrderKey(id, allExitRuleOrders),
        ),
      });
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
  }, [
    cancelPrompt,
    parkedDeleteManyMut,
    parkedSelected,
    groups,
    selected,
    cancelMut,
    exitRuleSelected,
    allExitRuleOrders,
    exitRuleCancelMut,
  ]);

  const confirmModify = useCallback(
    (quantity: string, price: string) => {
      setModifyError(null);
      modifyLegMut.mutate({ new_quantity: quantity, new_price: price });
    },
    [modifyLegMut],
  );

  const cancelPromptPending =
    cancelPrompt?.kind === "parked-bulk"
      ? parkedDeleteManyMut.isPending
      : cancelPrompt?.kind === "exitrule-single" || cancelPrompt?.kind === "exitrule-bulk"
        ? exitRuleCancelMut.isPending
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
                              <td
                                className="py-3.5 pl-4 pr-[18px] align-middle text-right text-faint"
                                onClick={(e) => e.stopPropagation()}
                              >
                                <div className="flex items-center justify-end gap-2">
                                  {legHasModifiable(g.group_orders ?? []) ? (
                                    <button
                                      type="button"
                                      className={modifyOutlineBtnSmallClass}
                                      onClick={() => openModifyForBookGroup(g)}
                                    >
                                      Modify
                                    </button>
                                  ) : null}
                                  <ChevronGlyph expanded={isOpen} />
                                </div>
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
                        <div className="flex items-center justify-between gap-2">
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
                          ) : (
                            <span />
                          )}
                          {legHasModifiable(g.group_orders ?? []) ? (
                            <button
                              type="button"
                              className={modifyOutlineBtnSmallClass}
                              onClick={() => openModifyForBookGroup(g)}
                            >
                              Modify
                            </button>
                          ) : null}
                        </div>
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

      <section className="app-card min-w-0 overflow-hidden">
        <div className="flex flex-wrap items-center justify-between gap-2.5 border-b border-border-soft px-[18px] py-3.5">
          <span className="text-hint font-bold uppercase tracking-[.07em] text-faint">
            Profit Booking / Stop Loss
          </span>
          <div className="inline-flex gap-0.5 rounded-lg border border-border bg-panel2 p-0.5">
            {(["active", "history"] as const).map((tab) => (
              <button
                key={tab}
                type="button"
                className={`rounded-md px-3 py-1 text-xs font-semibold capitalize transition ${
                  exitRuleTab === tab
                    ? "bg-accent-strong text-accent-ink"
                    : "text-muted hover:text-foreground"
                }`}
                onClick={() => setExitRuleTab(tab)}
              >
                {tab}
              </button>
            ))}
          </div>
        </div>

        <div className="px-[18px] py-3">
          <p className="text-sm text-muted">
            Rules armed from Portfolio, and the broker orders they&apos;ve placed.
            Orders here are excluded from Order Book above.
          </p>
        </div>

        {squareOffExitBoardQuery.isLoading || gttExitBoardQuery.isLoading ? (
          <div className="app-card-muted mx-[18px] mb-4 space-y-2 border-dashed p-4">
            {[0, 1].map((i) => (
              <div key={i} className="h-9 w-full app-skeleton rounded-sm border-0" />
            ))}
          </div>
        ) : exitRuleRowsForTab.length === 0 ? (
          <div className="app-card-muted mx-[18px] mb-4 border-dashed p-8 text-center text-sm app-text-muted">
            {exitRuleTab === "active"
              ? "No active Profit Booking / Stop Loss rules."
              : "No resolved Profit Booking / Stop Loss rules yet."}
          </div>
        ) : (
          <div className="px-[18px] pb-4">
            <div className="hidden min-w-0 -mx-[18px] overflow-x-auto md:block">
              <table className="min-w-full text-left text-sm text-foreground">
                <thead className="border-b border-border bg-panel2">
                  <tr>
                    <th className="px-4 py-3 text-heading font-semibold uppercase tracking-wider text-faint">
                      Scope
                    </th>
                    <th className="px-4 py-3 text-heading font-semibold uppercase tracking-wider text-faint">
                      Contract
                    </th>
                    <th className="px-4 py-3 text-heading font-semibold uppercase tracking-wider text-faint">
                      Target / Stop
                    </th>
                    <th className="px-4 py-3 text-heading font-semibold uppercase tracking-wider text-faint">
                      Status
                    </th>
                    <th className="px-4 py-3 text-heading font-semibold uppercase tracking-wider text-faint">
                      Placed / Resolved
                    </th>
                    <th className="px-4 py-3 text-right text-heading font-semibold uppercase tracking-wider text-faint">
                      <span className="sr-only">Portfolio link</span>
                    </th>
                    <th className="py-3 pl-4 pr-[18px] text-right text-heading font-semibold uppercase tracking-wider text-faint">
                      Legs
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border-soft">
                  {exitRuleRowsForTab.map((row) => {
                    const rowKey = `${row.kind}:${row.id}`;
                    const isOpen = !!exitRuleExpanded[rowKey];
                    const cancelableLegs = row.orders.filter(
                      (o) => o.cancelable && o.order_id,
                    );
                    return (
                      <Fragment key={rowKey}>
                        <tr
                          className={
                            cancelableLegs.length
                              ? "cursor-pointer transition-colors hover:bg-panel2"
                              : ""
                          }
                          role={cancelableLegs.length ? "button" : undefined}
                          tabIndex={cancelableLegs.length ? 0 : undefined}
                          aria-expanded={cancelableLegs.length ? isOpen : undefined}
                          onClick={
                            cancelableLegs.length || row.orders.length
                              ? () => toggleExitRuleRow(rowKey)
                              : undefined
                          }
                        >
                          <td className="px-4 py-3.5 align-middle">
                            <span
                              className={
                                row.kind === "group"
                                  ? exitRuleScopeGroupClass
                                  : exitRuleScopeLegClass
                              }
                            >
                              {row.kind === "group" ? "Group" : "Leg · GTT"}
                            </span>
                          </td>
                          <td className="px-4 py-3.5 align-middle font-medium text-foreground">
                            {exitRuleTitle(row)}
                          </td>
                          <td className="px-4 py-3.5 align-middle">
                            <div className="flex flex-col gap-0.5 font-mono text-xs tabular-nums">
                              <span className="text-up">
                                Target {formatExitRuleAmount(row.targetValue, row.kind)}
                              </span>
                              <span className="text-down">
                                Stop {formatExitRuleAmount(row.stopValue, row.kind)}
                              </span>
                            </div>
                          </td>
                          <td className="px-4 py-3.5 align-middle">
                            <span className={exitRuleStatusChipClass(row.effectiveStatus)}>
                              {exitRuleStatusLabel(row.effectiveStatus)}
                            </span>
                          </td>
                          <td className="px-4 py-3.5 align-middle font-mono text-xs text-muted">
                            <div>{row.placedAt || "—"}</div>
                            {row.resolvedAt ? <div>{row.resolvedAt}</div> : null}
                          </td>
                          <td
                            className="px-4 py-3.5 align-middle text-right"
                            onClick={(e) => e.stopPropagation()}
                          >
                            <Link
                              href="/portfolio"
                              className="inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1 text-xs font-semibold text-accent-strong transition hover:bg-accent-tint"
                            >
                              Portfolio
                            </Link>
                          </td>
                          <td className="py-3.5 pl-4 pr-[18px] align-middle text-right text-faint">
                            {row.orders.length ? (
                              <span className="inline-flex items-center gap-1">
                                <ChevronGlyph expanded={isOpen} />
                                {row.orders.length}
                              </span>
                            ) : (
                              <span aria-hidden>—</span>
                            )}
                          </td>
                        </tr>
                        {isOpen && row.orders.length ? (
                          <tr className="border-b border-border-soft bg-panel2">
                            <td colSpan={7} className="px-[18px] pt-3">
                              {groupRuleOrdersByLeg(row.orders).some((leg) =>
                                legHasModifiable(leg.orders),
                              ) ? (
                                <div className="mb-3 space-y-1.5 rounded-lg border border-border-soft bg-panel p-2.5">
                                  <p className="text-hint font-semibold uppercase tracking-wider text-faint">
                                    Legs
                                  </p>
                                  {groupRuleOrdersByLeg(row.orders).map((leg) => {
                                    if (!legHasModifiable(leg.orders)) return null;
                                    const legQty = leg.orders.reduce(
                                      (sum, o) => sum + toIntOrZero(o.quantity),
                                      0,
                                    );
                                    return (
                                      <div
                                        key={leg.key}
                                        className="flex items-center justify-between gap-2 text-sm"
                                      >
                                        <span className="font-mono text-muted">
                                          {leg.contractLabel} · {leg.orders[0]?.action} · Qty{" "}
                                          {formatQtyIndian(legQty)}
                                        </span>
                                        <button
                                          type="button"
                                          className={modifyOutlineBtnSmallClass}
                                          onClick={() => openModifyForRuleLeg(row, leg)}
                                        >
                                          Modify
                                        </button>
                                      </div>
                                    );
                                  })}
                                </div>
                              ) : null}
                              <table className="min-w-full text-left text-sm">
                                <thead>
                                  <tr className="border-b border-border-soft">
                                    <th className="w-10 px-4 py-2.5 text-center text-heading font-semibold uppercase tracking-wider text-faint">
                                      {cancelableLegs.length ? (
                                        <Checkbox
                                          checked={exitRuleGroupAllSelected(row)}
                                          indeterminate={
                                            exitRuleGroupSomeSelected(row) &&
                                            !exitRuleGroupAllSelected(row)
                                          }
                                          onChange={(checked) =>
                                            toggleExitRuleGroup(row, checked)
                                          }
                                          aria-label={`Select all cancelable orders in ${exitRuleTitle(row)}`}
                                        />
                                      ) : null}
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
                                      Price ₹
                                    </th>
                                    <th className="px-4 py-2.5 text-heading font-semibold uppercase tracking-wider text-faint">
                                      Status
                                    </th>
                                    <th className="w-10 px-1 py-2.5 text-center text-heading font-semibold uppercase tracking-wider text-faint">
                                      <span className="sr-only">Clone to Place Order</span>
                                    </th>
                                    <th className="w-20 px-2 py-2.5 text-right text-heading font-semibold uppercase tracking-wider text-faint">
                                      <span className="sr-only">Cancel</span>
                                    </th>
                                  </tr>
                                </thead>
                                <tbody className="divide-y divide-border-soft">
                                  {row.orders.map((o, j) => {
                                    const key = `${o.order_id ?? ""}|${o.exchange_code ?? ""}`;
                                    const canCancel = o.cancelable && !!o.order_id;
                                    return (
                                      <tr
                                        key={key || j}
                                        className="transition-colors hover:bg-accent-tint"
                                      >
                                        <td className="px-4 py-2.5 text-center align-middle">
                                          {canCancel ? (
                                            <Checkbox
                                              checked={exitRuleSelected.has(key)}
                                              onChange={(checked) =>
                                                toggleExitRuleOne(key, checked)
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
                                          {o.price != null ? `₹${o.price}` : "—"}
                                        </td>
                                        <td className="px-4 py-2.5 align-middle">
                                          <span className={statusChipClass(o.status)}>
                                            {o.status}
                                          </span>
                                        </td>
                                        <td className="px-1 py-2.5 align-middle text-center">
                                          <button
                                            type="button"
                                            className={cloneToPlaceBtnClass}
                                            aria-label="Clone order to Place Order"
                                            onClick={(e) => cloneExitRuleOrderToPlace(o, e)}
                                          >
                                            <CloneOrderGlyph />
                                          </button>
                                        </td>
                                        <td className="px-2 py-2.5 text-right align-middle">
                                          {canCancel ? (
                                            <button
                                              type="button"
                                              className={cancelOutlineBtnSmallClass}
                                              onClick={() =>
                                                setCancelPrompt({
                                                  kind: "exitrule-single",
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

            {exitRuleSelected.size > 0 || exitRuleCancelMut.isPending ? (
              <div className={ordersCancelBarClass}>
                <span className="text-sm text-muted">
                  {formatQtyIndian(exitRuleSelected.size)} leg order(s) selected
                </span>
                <button
                  type="button"
                  disabled={exitRuleCancelMut.isPending || exitRuleSelected.size === 0}
                  aria-busy={exitRuleCancelMut.isPending}
                  className={cancelOutlineBtnClass}
                  onClick={() => setCancelPrompt({ kind: "exitrule-bulk" })}
                >
                  <AsyncLabelSpan
                    busy={exitRuleCancelMut.isPending}
                    idleLabel="Cancel selected"
                    busyLabel="Cancelling…"
                  />
                </button>
              </div>
            ) : null}

            {/* Mobile: card layout */}
            <div className="space-y-3 md:hidden">
              {exitRuleRowsForTab.map((row) => {
                const rowKey = `${row.kind}:${row.id}`;
                return (
                  <div key={rowKey} className="app-card-muted overflow-hidden">
                    <details>
                      <summary className="cursor-pointer list-none px-3 py-2.5 text-base font-medium text-foreground">
                        <span className="block">{exitRuleTitle(row)}</span>
                        <span className="mt-1 flex flex-wrap items-center gap-1.5 text-sm font-normal text-muted">
                          <span
                            className={
                              row.kind === "group"
                                ? exitRuleScopeGroupClass
                                : exitRuleScopeLegClass
                            }
                          >
                            {row.kind === "group" ? "Group" : "Leg · GTT"}
                          </span>
                          <span className={exitRuleStatusChipClass(row.effectiveStatus)}>
                            {exitRuleStatusLabel(row.effectiveStatus)}
                          </span>
                        </span>
                      </summary>
                      <div className="space-y-2 border-t border-border-soft px-3 py-2.5 text-sm text-muted">
                        <div className="flex items-center justify-between">
                          <span>
                            Target: {formatExitRuleAmount(row.targetValue, row.kind)} · Stop:{" "}
                            {formatExitRuleAmount(row.stopValue, row.kind)}
                          </span>
                          <Link
                            href="/portfolio"
                            className="rounded-md border border-border px-2.5 py-1 text-xs font-semibold text-accent-strong"
                          >
                            Portfolio
                          </Link>
                        </div>
                        {groupRuleOrdersByLeg(row.orders).some((leg) =>
                          legHasModifiable(leg.orders),
                        ) ? (
                          <div className="space-y-1.5 rounded-lg border border-border-soft bg-panel2 p-2.5">
                            <p className="text-hint font-semibold uppercase tracking-wider text-faint">
                              Legs
                            </p>
                            {groupRuleOrdersByLeg(row.orders).map((leg) => {
                              if (!legHasModifiable(leg.orders)) return null;
                              const legQty = leg.orders.reduce(
                                (sum, o) => sum + toIntOrZero(o.quantity),
                                0,
                              );
                              return (
                                <div
                                  key={leg.key}
                                  className="flex items-center justify-between gap-2 text-sm"
                                >
                                  <span className="font-mono text-muted">
                                    {leg.contractLabel} · {leg.orders[0]?.action} · Qty{" "}
                                    {formatQtyIndian(legQty)}
                                  </span>
                                  <button
                                    type="button"
                                    className={modifyOutlineBtnSmallClass}
                                    onClick={() => openModifyForRuleLeg(row, leg)}
                                  >
                                    Modify
                                  </button>
                                </div>
                              );
                            })}
                          </div>
                        ) : null}
                        {row.orders.map((o, j) => {
                          const key = `${o.order_id ?? ""}|${o.exchange_code ?? ""}`;
                          const canCancel = o.cancelable && !!o.order_id;
                          return (
                            <div
                              key={key || j}
                              className="rounded-lg border border-border bg-panel2 p-3 text-sm"
                            >
                              <p className="font-mono text-base font-medium text-foreground">
                                #{o.order_id ?? "—"}
                              </p>
                              <p>
                                Qty: {formatQtyIndian(o.quantity)} · Open:{" "}
                                {formatQtyIndian(o.open_quantity)}
                              </p>
                              <p>Price: {o.price != null ? `₹${o.price}` : "—"}</p>
                              <p>Status: {o.status}</p>
                              {canCancel ? (
                                <div className="mt-1 flex justify-end">
                                  <button
                                    type="button"
                                    className={cancelOutlineBtnSmallClass}
                                    onClick={() =>
                                      setCancelPrompt({ kind: "exitrule-single", key })
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
                );
              })}
            </div>
          </div>
        )}
      </section>

      <CancelConfirmDialog
        prompt={cancelPrompt}
        pending={cancelPromptPending}
        onClose={() => setCancelPrompt(null)}
        onConfirm={confirmCancel}
      />
      <ModifyLegDialog
        target={modifyTarget}
        pending={modifyLegMut.isPending}
        error={modifyError}
        onClose={() => {
          setModifyTarget(null);
          setModifyError(null);
        }}
        onConfirm={confirmModify}
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
