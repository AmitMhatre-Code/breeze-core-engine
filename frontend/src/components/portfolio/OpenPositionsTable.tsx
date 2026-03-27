"use client";

import { Fragment, useCallback, useMemo, useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { ExecutionPreviewModal } from "@/components/order/ExecutionPreviewModal";
import { useOrderConfirm } from "@/components/order/OrderConfirmProvider";
import { PortfolioGroupPayoffPanel } from "@/components/portfolio/PortfolioGroupPayoffPanel";
import { PortfolioHedgeOrderSheet } from "@/components/portfolio/PortfolioHedgeOrderSheet";
import { apiClient } from "@/lib/api-client";
import { buildPortfolioPositionGroups } from "@/lib/portfolio/groupPositions";
import { formatIndianMoneyCompact } from "@/lib/format-money-in";
import {
  ltpAsOrderPrice,
  squareOffToOrderPayload,
} from "@/lib/order-confirm";
import type { PortfolioPositionRecord } from "@/lib/portfolio";
import type { OptionRight } from "@/lib/strategy-builder/types";

/**
 * Matches legacy `templates/portfolio.html` fed by `get_positions` (see
 * `legacy/app/api/v1/route_portfolio.py` serve_landing).
 *
 * Responsive: &lt;xl = card layout (phones + tablets); xl+ = wide table with
 * side-by-side actions. Fallback horizontal scroll remains inside `.app-table-wrap`.
 */
export type { PortfolioPositionRecord };

function coerceNum(v: unknown): number | null {
  if (v == null || v === "") return null;
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string") {
    const t = v.trim();
    if (!t || t === "*") return null;
    const n = Number(t);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

function isHedgeable(raw: unknown): boolean {
  return raw === true || raw === "true" || raw === "Y" || raw === "y";
}

const mutedNumClass = "text-zinc-600 dark:text-zinc-400";

/** MTM / Carry: gain = green ₹x, loss = red (₹x), flat = muted. */
function formatMtmCarry(raw: unknown): { text: string; className: string } {
  const n = coerceNum(raw);
  if (n == null) {
    return { text: "—", className: "app-text-muted" };
  }
  const v = Math.round(n);
  const abs = Math.abs(v).toLocaleString("en-IN");
  if (v < 0) {
    return {
      text: `(₹${abs})`,
      className: "font-medium text-red-600 dark:text-red-400",
    };
  }
  if (v > 0) {
    return {
      text: `₹${abs}`,
      className: "font-medium text-emerald-600 dark:text-emerald-400",
    };
  }
  return { text: "₹0", className: mutedNumClass };
}

/** Legacy Span / ELM: ₹{value/100000}L (integer lakhs). */
function formatSpanElmLakhs(raw: unknown): string {
  const n = coerceNum(raw);
  if (n == null) return "-";
  const lakhs = n / 100_000;
  const rounded = Math.round(lakhs);
  return `₹${rounded.toLocaleString("en-IN")}L`;
}

function carryMarginRoiTitle(row: PortfolioPositionRecord): string | undefined {
  const avg = coerceNum(row.average_price);
  const qty = coerceNum(row.quantity);
  const span = coerceNum(row.span_margin_required);
  const dte = coerceNum(row.days_to_expiry);
  const cr = coerceNum(row.carry_margin_returns);
  if (
    avg == null ||
    qty == null ||
    span == null ||
    dte == null ||
    cr == null ||
    span <= 0 ||
    dte <= 0
  ) {
    return undefined;
  }
  const premium = avg * Math.abs(qty);
  const premS = `₹${premium.toLocaleString("en-IN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
  const spanS = `₹${span.toLocaleString("en-IN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
  return [
    "Annualised carry return on SPAN (%):",
    "(premium at entry ÷ DTE) × 365 ÷ span × 100",
    "",
    `premium (avg sell price × |qty|) = ${premS}`,
    `DTE = ${Math.round(dte)} days`,
    `span = ${spanS}`,
    "",
    `= (${premium.toLocaleString("en-IN", { maximumFractionDigits: 2 })} ÷ ${Math.round(dte)}) × 365 ÷ ${span.toLocaleString("en-IN", { maximumFractionDigits: 2 })} × 100`,
    `≈ ${cr.toFixed(2)}%`,
  ].join("\n");
}

/** Carry returns %: gain = green x%, loss = red (x%) with brackets like MTM. */
function formatCarryRet(raw: unknown): { text: string; className: string } {
  const n = coerceNum(raw);
  if (n == null) {
    return { text: "—", className: "app-text-muted" };
  }
  const fmt = (x: number) =>
    Math.abs(x).toLocaleString("en-IN", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
  if (n < 0) {
    return {
      text: `(${fmt(n)}%)`,
      className: "font-medium text-red-600 dark:text-red-400",
    };
  }
  if (n > 0) {
    return {
      text: `${fmt(n)}%`,
      className: "font-medium text-emerald-600 dark:text-emerald-400",
    };
  }
  return { text: "0.00%", className: mutedNumClass };
}

/** Avg / LTP: ₹ with decimals (legacy prints raw; we normalize). */
function formatPriceCell(raw: unknown): string {
  const n = coerceNum(raw);
  if (n == null) return "—";
  return `₹${n.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function formatSpot(raw: unknown): { text: string; className: string } {
  if (raw == null || raw === "") {
    return { text: "—", className: "app-text-muted" };
  }
  const s = String(raw).trim();
  if (s === "Err" || s.toLowerCase() === "err") {
    return {
      text: "Err",
      className: "font-medium text-red-600 dark:text-red-400",
    };
  }
  return { text: formatPriceCell(raw), className: "tabular-nums" };
}

export function squareOffHref(row: PortfolioPositionRecord): string {
  const params = new URLSearchParams({
    action: "SquareOff",
    position: String(row.action ?? ""),
    product_type: String(row.product_type ?? ""),
    stock_code: String(row.stock_code ?? ""),
    exchange_code: String(row.exchange_code ?? ""),
    expiry_date: String(row.expiry_date ?? ""),
    right: String(row.right ?? ""),
    strike_price: String(row.strike_price ?? ""),
    quantity: String(row.quantity ?? ""),
  });
  const ltpPrice = ltpAsOrderPrice(row.ltp);
  if (ltpPrice !== "0") params.set("price", ltpPrice);
  return `/orders?${params.toString()}`;
}

export function hedgeHref(row: PortfolioPositionRecord): string {
  const params = new URLSearchParams({
    action: String(row.action ?? ""),
    product_type: String(row.product_type ?? ""),
    stock_code: String(row.stock_code ?? ""),
    exchange_code: String(row.exchange_code ?? ""),
    expiry_date: String(row.expiry_date ?? ""),
    right: String(row.right ?? ""),
    strike_price: String(row.strike_price ?? ""),
    quantity: String(row.quantity ?? ""),
    top: "3",
  });
  return `/hedge?${params.toString()}`;
}

/** Table row: compact on xl, roomier on 2xl. Cards: touch-friendly full-width on narrow phones. */
const btnPrimaryTable =
  "app-btn-primary shrink-0 px-2.5 py-1.5 text-xs font-medium 2xl:px-3 2xl:py-2 2xl:text-sm";
const btnSecondaryTable =
  "app-btn-outline shrink-0 px-2.5 py-1.5 text-xs 2xl:px-3 2xl:py-2 2xl:text-sm";

const btnPrimaryCard =
  "app-btn-primary px-3 py-2.5 text-sm font-medium";
const btnSecondaryCard =
  "app-btn-outline px-3 py-2.5 text-sm font-medium";

function childRowKey(groupKey: string, localIdx: number): string {
  return `${groupKey}-${localIdx}`;
}

function sumNumericField(
  rows: PortfolioPositionRecord[],
  field: string,
): number | null {
  let sum = 0;
  let any = false;
  for (const row of rows) {
    const v = coerceNum(row[field]);
    if (v != null) {
      sum += v;
      any = true;
    }
  }
  return any ? sum : null;
}

type HedgeCandidatesApiResponse = {
  Status: number;
  Error?: string;
  Success?: unknown;
};

function hedgeCandidatesQueryString(row: PortfolioPositionRecord): string {
  const qRaw = coerceNum(row.quantity);
  const qty =
    qRaw != null && qRaw !== 0
      ? String(Math.abs(Math.trunc(qRaw)))
      : "";
  const q = new URLSearchParams({
    stock_code: String(row.stock_code ?? "").trim(),
    exchange_code: (String(row.exchange_code ?? "NFO").trim() || "NFO"),
    expiry_date: String(row.expiry_date ?? "").trim(),
    right: String(row.right ?? "").trim(),
    strike_price: String(row.strike_price ?? "").trim(),
    quantity: qty,
    top: "5",
  });
  return q.toString();
}

function parseHedgeNum(v: unknown): number {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string") {
    const n = parseFloat(v.replace(/,/g, ""));
    return Number.isFinite(n) ? n : NaN;
  }
  return NaN;
}

function PortfolioHedgeExpandPanel({
  row,
  onSelect,
}: {
  row: PortfolioPositionRecord;
  onSelect: (opt: Record<string, unknown>) => void;
}) {
  const qs = useMemo(() => hedgeCandidatesQueryString(row), [row]);
  const q = useQuery({
    queryKey: ["portfolio", "hedge-candidates", qs],
    queryFn: () =>
      apiClient.get<HedgeCandidatesApiResponse>(
        `/portfolio/hedge-candidates?${qs}`,
      ),
  });

  const list: Record<string, unknown>[] = useMemo(() => {
    const d = q.data;
    if (!d || d.Status !== 200 || !Array.isArray(d.Success)) return [];
    return d.Success.filter((x): x is Record<string, unknown> =>
      Boolean(x && typeof x === "object"),
    );
  }, [q.data]);

  if (q.isLoading) {
    return (
      <div className="px-3 py-4 text-sm app-text-muted">Loading hedges…</div>
    );
  }
  if (q.isError) {
    return (
      <div className="px-3 py-4 text-sm text-red-600 dark:text-red-400">
        {q.error instanceof Error ? q.error.message : "Unable to load hedges."}
      </div>
    );
  }
  if (q.data && q.data.Status !== 200) {
    return (
      <div className="px-3 py-4 text-sm text-red-600 dark:text-red-400">
        {q.data.Error?.trim() || "No hedge candidates."}
      </div>
    );
  }
  if (!list.length) {
    return (
      <div className="px-3 py-4 text-sm app-text-muted">
        No hedge candidates for this position.
      </div>
    );
  }

  return (
    <div className="border-t border-zinc-200/80 bg-zinc-50/80 px-3 py-3 dark:border-zinc-700/80 dark:bg-zinc-900/50">
      <p className="mb-2 text-xs font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
        Top hedge strikes (by estimated premium)
      </p>
      <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap">
        {list.map((opt, hi) => {
          const strike = parseHedgeNum(opt.strike_price);
          const hq = parseHedgeNum(opt.hedge_quantity);
          const hp = parseHedgeNum(opt.hedge_premium);
          const offer = parseHedgeNum(opt.best_offer_price);
          return (
            <div
              key={`${String(strike)}-${hi}`}
              className="flex min-w-0 flex-1 flex-col gap-1.5 rounded-xl border border-zinc-200/90 bg-white p-3 text-xs shadow-sm dark:border-zinc-700 dark:bg-zinc-950 sm:min-w-[10.5rem] sm:max-w-[14rem]"
            >
              <div className="font-semibold tabular-nums text-zinc-900 dark:text-zinc-100">
                Strike{" "}
                {Number.isFinite(strike)
                  ? Math.round(strike).toLocaleString("en-IN")
                  : "—"}
              </div>
              <div className="tabular-nums text-zinc-600 dark:text-zinc-400">
                Hedge qty{" "}
                {Number.isFinite(hq)
                  ? Math.round(hq).toLocaleString("en-IN")
                  : "—"}
              </div>
              <div className="tabular-nums text-zinc-600 dark:text-zinc-400">
                Est. premium{" "}
                {Number.isFinite(hp)
                  ? formatIndianMoneyCompact(hp)
                  : "—"}
              </div>
              <div className="tabular-nums text-zinc-600 dark:text-zinc-400">
                Offer ₹
                {Number.isFinite(offer)
                  ? offer.toLocaleString("en-IN", {
                      minimumFractionDigits: 2,
                      maximumFractionDigits: 2,
                    })
                  : "—"}
              </div>
              <button
                type="button"
                className="app-btn-outline mt-1 w-full px-2 py-1.5 text-xs font-medium"
                onClick={() => onSelect(opt)}
              >
                Select
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function PositionActionsTable({
  row,
  rowKey,
  hedgeExpanded,
  onToggleHedges,
}: {
  row: PortfolioPositionRecord;
  rowKey: string;
  hedgeExpanded: boolean;
  onToggleHedges: (key: string) => void;
}) {
  const { openOrderConfirm } = useOrderConfirm();
  const squarePayload = squareOffToOrderPayload(row);
  return (
    <div className="flex flex-nowrap items-center justify-end gap-1.5 2xl:gap-2">
      {squarePayload ? (
        <button
          type="button"
          className={btnPrimaryTable}
          onClick={() => openOrderConfirm(squarePayload)}
        >
          Square Off
        </button>
      ) : (
        <Link href={squareOffHref(row)} className={btnPrimaryTable}>
          Square Off
        </Link>
      )}
      {isHedgeable(row.hedgeable) ? (
        <button
          type="button"
          className={btnSecondaryTable}
          onClick={() => onToggleHedges(rowKey)}
        >
          {hedgeExpanded ? "Hide hedges" : "Get Hedges"}
        </button>
      ) : null}
    </div>
  );
}

function PositionActionsCard({
  row,
  rowKey,
  hedgeExpanded,
  onToggleHedges,
}: {
  row: PortfolioPositionRecord;
  rowKey: string;
  hedgeExpanded: boolean;
  onToggleHedges: (key: string) => void;
}) {
  const { openOrderConfirm } = useOrderConfirm();
  const squarePayload = squareOffToOrderPayload(row);
  return (
    <div className="flex w-full flex-col gap-2 sm:flex-row sm:flex-wrap">
      {squarePayload ? (
        <button
          type="button"
          className={`${btnPrimaryCard} w-full min-h-11 sm:min-h-0 sm:flex-1`}
          onClick={() => openOrderConfirm(squarePayload)}
        >
          Square Off
        </button>
      ) : (
        <Link
          href={squareOffHref(row)}
          className={`${btnPrimaryCard} w-full min-h-11 sm:min-h-0 sm:flex-1`}
        >
          Square Off
        </Link>
      )}
      {isHedgeable(row.hedgeable) ? (
        <button
          type="button"
          className={`${btnSecondaryCard} w-full min-h-11 sm:min-h-0 sm:flex-1`}
          onClick={() => onToggleHedges(rowKey)}
        >
          {hedgeExpanded ? "Hide hedges" : "Get Hedges"}
        </button>
      ) : null}
    </div>
  );
}

const thBase =
  "whitespace-nowrap px-2 py-2 text-xs font-semibold text-zinc-700 2xl:px-3 2xl:py-2.5 2xl:text-sm dark:text-zinc-300";
/** Shell only — no `text-*` color so MTM/Carry/Carry% can use emerald/red without zinc winning in cascade. */
const tdShell =
  "whitespace-nowrap px-2 py-2 align-middle text-xs 2xl:px-3 2xl:py-2.5 2xl:text-sm";
const tdInk = "text-zinc-800 dark:text-zinc-200";
const tdBase = `${tdShell} ${tdInk}`;

type OpenPositionsTableProps = {
  positions: PortfolioPositionRecord[];
  emptyMessage?: string;
};

export function OpenPositionsTable({
  positions,
  emptyMessage = "No positions to display",
}: OpenPositionsTableProps) {
  const groups = useMemo(
    () => buildPortfolioPositionGroups(positions),
    [positions],
  );
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(
    () => new Set(),
  );
  const toggleGroup = useCallback((key: string) => {
    setExpandedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  const childRowNumber = useMemo(() => {
    const m = new Map<string, number>();
    let n = 0;
    for (const g of groups) {
      for (let i = 0; i < g.rows.length; i++) {
        n += 1;
        m.set(childRowKey(g.key, i), n);
      }
    }
    return m;
  }, [groups]);

  const [hedgeExpandedKey, setHedgeExpandedKey] = useState<string | null>(null);
  const [hedgeSheet, setHedgeSheet] = useState<{
    row: PortfolioPositionRecord;
    opt: Record<string, unknown>;
  } | null>(null);
  const [executePreview, setExecutePreview] = useState<{
    row: PortfolioPositionRecord;
    strike: number;
    right: OptionRight;
    quantity: number;
    price: string;
  } | null>(null);

  const onToggleHedges = useCallback((key: string) => {
    setHedgeExpandedKey((prev) => (prev === key ? null : key));
    setHedgeSheet(null);
  }, []);

  const closeHedgeSheet = useCallback(() => setHedgeSheet(null), []);

  const onHedgeSheetBuy = useCallback(
    (args: {
      strike: number;
      right: OptionRight;
      quantity: number;
      price: string;
    }) => {
      if (!hedgeSheet) return;
      setExecutePreview({
        row: hedgeSheet.row,
        strike: args.strike,
        right: args.right,
        quantity: args.quantity,
        price: args.price,
      });
      setHedgeSheet(null);
    },
    [hedgeSheet],
  );

  const closeExecutePreview = useCallback(() => setExecutePreview(null), []);

  const executionLegs = useMemo(() => {
    if (!executePreview) return [];
    const prem = parseFloat(executePreview.price.replace(/,/g, ""));
    return [
      {
        strike: executePreview.strike,
        right: executePreview.right,
        side: "Buy" as const,
        quantity: executePreview.quantity,
        premiumPerUnit: Number.isFinite(prem) ? prem : 0,
      },
    ];
  }, [executePreview]);

  return (
    <>
      {/* xl+ desktop table — tablets & phones use cards below */}
      <div className="hidden min-w-0 max-w-full xl:block">
        <div className="app-table-wrap w-full min-w-0 max-w-full">
          <table className="w-full min-w-max table-auto border-collapse text-left">
            <thead className="app-table-head">
              <tr>
                <th className={`${thBase} w-10 text-center`}>#</th>
                <th className={`${thBase} min-w-[11rem] text-left 2xl:min-w-[14rem]`}>
                  Option
                </th>
                <th className={`${thBase} text-left`}>Position</th>
                <th className={`${thBase} text-right`}>Qty</th>
                <th className={`${thBase} text-right`}>Avg. Price</th>
                <th className={`${thBase} text-right`}>LTP</th>
                <th className={`${thBase} text-right`}>Spot</th>
                <th className={`${thBase} text-right`}>MTM</th>
                <th className={`${thBase} text-right`}>Carry</th>
                <th className={`${thBase} text-right`} title="Span Margin">
                  Span Marg.
                </th>
                <th className={`${thBase} text-right`} title="ELM at 2%">
                  ELM
                </th>
                <th
                  className={`${thBase} text-right`}
                  title={
                    "Annualised on SPAN (%): (premium at entry ÷ DTE) × 365 ÷ span × 100. Hover a row value for the exact inputs."
                  }
                >
                  Carry Ret.
                </th>
                <th
                  className={`${thBase} text-right whitespace-nowrap`}
                  title="Square off or hedge this leg"
                >
                </th>
              </tr>
            </thead>
            <tbody>
              {positions.length === 0 ? (
                <tr>
                  <td
                    colSpan={13}
                    className="px-3 py-8 text-center text-sm app-text-muted"
                  >
                    {emptyMessage}
                  </td>
                </tr>
              ) : (
                groups.map((g) => {
                  const isOpen = expandedGroups.has(g.key);
                  const spotAgg = formatSpot(g.rows[0]?.spot_price);
                  const mtmSum = sumNumericField(g.rows, "current_profit");
                  const carrySum = sumNumericField(g.rows, "carry_profit");
                  const spanSum = sumNumericField(
                    g.rows,
                    "span_margin_required",
                  );
                  const elmSum = sumNumericField(g.rows, "elm_margin_required");
                  const gMtm = formatMtmCarry(mtmSum);
                  const gCarry = formatMtmCarry(carrySum);
                  const groupTitle = `${g.stockCode} · ${g.expiryDate} · ${g.rows.length} leg${g.rows.length === 1 ? "" : "s"}`;
                  return (
                    <Fragment key={g.key}>
                      <tr
                        role="button"
                        tabIndex={0}
                        aria-expanded={isOpen}
                        title={isOpen ? "Collapse group" : "Expand group"}
                        className="app-table-row cursor-pointer select-none hover:bg-zinc-100/80 dark:hover:bg-zinc-900/50"
                        onClick={() => toggleGroup(g.key)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter" || e.key === " ") {
                            e.preventDefault();
                            toggleGroup(g.key);
                          }
                        }}
                      >
                        <td
                          className={`${tdBase} text-center tabular-nums text-zinc-500 dark:text-zinc-400`}
                        >
                          {isOpen ? "▼" : "▶"}
                        </td>
                        <td className={`${tdBase} font-medium`}>
                          {groupTitle}
                        </td>
                        <td className={tdBase}>—</td>
                        <td className={`${tdBase} text-right`}>—</td>
                        <td className={`${tdBase} text-right`}>—</td>
                        <td className={`${tdBase} text-right`}>—</td>
                        <td
                          className={`${tdBase} text-right tabular-nums ${spotAgg.className}`}
                        >
                          {spotAgg.text}
                        </td>
                        <td
                          className={`${tdShell} text-right tabular-nums ${gMtm.className}`}
                        >
                          {gMtm.text}
                        </td>
                        <td
                          className={`${tdShell} text-right tabular-nums ${gCarry.className}`}
                        >
                          {gCarry.text}
                        </td>
                        <td className={`${tdBase} text-right tabular-nums`}>
                          {spanSum != null
                            ? formatSpanElmLakhs(spanSum)
                            : "—"}
                        </td>
                        <td className={`${tdBase} text-right tabular-nums`}>
                          {elmSum != null ? formatSpanElmLakhs(elmSum) : "—"}
                        </td>
                        <td
                          className={`${tdShell} text-right tabular-nums app-text-muted`}
                        >
                          —
                        </td>
                        <td className={`${tdBase} text-right align-middle`} />
                      </tr>
                      {isOpen
                        ? g.rows.map((row, localIdx) => {
                            const rowKey = childRowKey(g.key, localIdx);
                            const hedgeOpen = hedgeExpandedKey === rowKey;
                            const mtm = formatMtmCarry(row.current_profit);
                            const carry = formatMtmCarry(row.carry_profit);
                            const cr = formatCarryRet(row.carry_margin_returns);
                            const carryRoiTitle = carryMarginRoiTitle(row);
                            const spot = formatSpot(row.spot_price);
                            const qty = coerceNum(row.quantity);
                            const num =
                              childRowNumber.get(rowKey) ?? localIdx + 1;
                            return (
                              <Fragment key={rowKey}>
                                <tr className="app-table-row bg-zinc-50/40 dark:bg-zinc-950/25">
                                  <td
                                    className={`${tdBase} text-center tabular-nums`}
                                  >
                                    {num}
                                  </td>
                                  <td className={tdBase}>
                                    {String(row.option ?? "—")}
                                  </td>
                                  <td className={tdBase}>
                                    {String(row.action ?? "—")}
                                  </td>
                                  <td className={`${tdBase} text-right tabular-nums`}>
                                    {qty != null
                                      ? qty.toLocaleString("en-IN", {
                                          maximumFractionDigits:
                                            Number.isInteger(qty) ? 0 : 4,
                                        })
                                      : "—"}
                                  </td>
                                  <td className={`${tdBase} text-right tabular-nums`}>
                                    {formatPriceCell(row.average_price)}
                                  </td>
                                  <td className={`${tdBase} text-right tabular-nums`}>
                                    {formatPriceCell(row.ltp)}
                                  </td>
                                  <td
                                    className={`${tdBase} text-right tabular-nums ${spot.className}`}
                                  >
                                    {spot.text}
                                  </td>
                                  <td
                                    className={`${tdShell} text-right tabular-nums ${mtm.className}`}
                                  >
                                    {mtm.text}
                                  </td>
                                  <td
                                    className={`${tdShell} text-right tabular-nums ${carry.className}`}
                                  >
                                    {carry.text}
                                  </td>
                                  <td className={`${tdBase} text-right tabular-nums`}>
                                    {formatSpanElmLakhs(row.span_margin_required)}
                                  </td>
                                  <td className={`${tdBase} text-right tabular-nums`}>
                                    {formatSpanElmLakhs(row.elm_margin_required)}
                                  </td>
                                  <td
                                    className={`${tdShell} text-right tabular-nums ${cr.className}`}
                                    title={carryRoiTitle}
                                  >
                                    {cr.text}
                                  </td>
                                  <td className={`${tdBase} text-right align-middle`}>
                                    <PositionActionsTable
                                      row={row}
                                      rowKey={rowKey}
                                      hedgeExpanded={hedgeOpen}
                                      onToggleHedges={onToggleHedges}
                                    />
                                  </td>
                                </tr>
                                {hedgeOpen && isHedgeable(row.hedgeable) ? (
                                  <tr className="app-table-row bg-zinc-50/90 dark:bg-zinc-900/35">
                                    <td className="p-0" colSpan={13}>
                                      <PortfolioHedgeExpandPanel
                                        row={row}
                                        onSelect={(opt) =>
                                          setHedgeSheet({ row, opt })
                                        }
                                      />
                                    </td>
                                  </tr>
                                ) : null}
                              </Fragment>
                            );
                          })
                        : null}
                      {isOpen ? (
                        <tr className="app-table-row">
                          <td className="p-0 align-top" colSpan={13}>
                            <PortfolioGroupPayoffPanel
                              stockCode={g.stockCode}
                              exchangeCode={g.exchangeCode}
                              expiryDisplay={g.expiryDate}
                              rows={g.rows}
                            />
                          </td>
                        </tr>
                      ) : null}
                    </Fragment>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Phones & tablets: grouped cards — header expands to legs + payoff */}
      <div className="space-y-3 xl:hidden">
        {positions.length === 0 ? (
          <p className="py-6 text-center text-base app-text-muted">
            {emptyMessage}
          </p>
        ) : (
          groups.map((g) => {
            const isOpen = expandedGroups.has(g.key);
            const spotAgg = formatSpot(g.rows[0]?.spot_price);
            const mtmSum = sumNumericField(g.rows, "current_profit");
            const carrySum = sumNumericField(g.rows, "carry_profit");
            const spanSum = sumNumericField(g.rows, "span_margin_required");
            const elmSum = sumNumericField(g.rows, "elm_margin_required");
            const gMtm = formatMtmCarry(mtmSum);
            const gCarry = formatMtmCarry(carrySum);
            const groupTitle = `${g.stockCode} · ${g.expiryDate} · ${g.rows.length} leg${g.rows.length === 1 ? "" : "s"}`;
            return (
              <div
                key={`card-group-${g.key}`}
                className="app-card-muted overflow-hidden text-sm"
              >
                <button
                  type="button"
                  aria-expanded={isOpen}
                  className="flex w-full items-start gap-3 p-4 text-left sm:p-5"
                  onClick={() => toggleGroup(g.key)}
                >
                  <span
                    className="mt-0.5 shrink-0 tabular-nums text-zinc-500 dark:text-zinc-400"
                    aria-hidden
                  >
                    {isOpen ? "▼" : "▶"}
                  </span>
                  <div className="min-w-0 flex-1 space-y-2">
                    <h3 className="text-base font-semibold leading-snug text-zinc-900 dark:text-zinc-100">
                      {groupTitle}
                    </h3>
                    <p>
                      <span className="app-text-muted">Spot:</span>{" "}
                      <span className={spotAgg.className}>{spotAgg.text}</span>
                    </p>
                    <p>
                      <span className="app-text-muted">MTM (sum):</span>{" "}
                      <span className={gMtm.className}>{gMtm.text}</span>
                    </p>
                    <p>
                      <span className="app-text-muted">Carry (sum):</span>{" "}
                      <span className={gCarry.className}>{gCarry.text}</span>
                    </p>
                    <p>
                      <span className="app-text-muted">Span Margin (sum):</span>{" "}
                      {spanSum != null
                        ? formatSpanElmLakhs(spanSum)
                        : "—"}
                    </p>
                    <p>
                      <span className="app-text-muted">ELM (sum):</span>{" "}
                      {elmSum != null ? formatSpanElmLakhs(elmSum) : "—"}
                    </p>
                  </div>
                </button>
                {isOpen ? (
                  <div className="border-t border-zinc-200/80 dark:border-zinc-700/80">
                    <div className="space-y-3 p-4 sm:space-y-4 sm:p-5">
                      {g.rows.map((row, localIdx) => {
                        const rowKey = childRowKey(g.key, localIdx);
                        const hedgeOpen = hedgeExpandedKey === rowKey;
                        const mtm = formatMtmCarry(row.current_profit);
                        const carryTitle = formatMtmCarry(row.carry_profit);
                        const cr = formatCarryRet(row.carry_margin_returns);
                        const carryRoiTitle = carryMarginRoiTitle(row);
                        const spot = formatSpot(row.spot_price);
                        const qty = coerceNum(row.quantity);
                        return (
                          <div
                            key={`card-${rowKey}`}
                            className="space-y-2.5 rounded-xl border border-zinc-200/90 bg-white/80 p-4 dark:border-zinc-700 dark:bg-zinc-950/40"
                          >
                            <h4 className="text-sm font-semibold leading-snug text-zinc-900 dark:text-zinc-100">
                              {String(row.option ?? "—")}
                            </h4>
                            <p>
                              <span className="app-text-muted">Position:</span>{" "}
                              {String(row.action ?? "—")}
                            </p>
                            <p>
                              <span className="app-text-muted">Qty:</span>{" "}
                              {qty != null
                                ? qty.toLocaleString("en-IN", {
                                    maximumFractionDigits: Number.isInteger(qty)
                                      ? 0
                                      : 4,
                                  })
                                : "—"}
                            </p>
                            <p>
                              <span className="app-text-muted">Avg Price:</span>{" "}
                              {formatPriceCell(row.average_price)}
                            </p>
                            <p>
                              <span className="app-text-muted">LTP:</span>{" "}
                              {formatPriceCell(row.ltp)}
                            </p>
                            <p>
                              <span className="app-text-muted">Spot:</span>{" "}
                              <span className={spot.className}>{spot.text}</span>
                            </p>
                            <p>
                              <span className="app-text-muted">MTM:</span>{" "}
                              <span className={mtm.className}>{mtm.text}</span>
                            </p>
                            <p>
                              <span className="app-text-muted">Carry:</span>{" "}
                              <span className={carryTitle.className}>
                                {carryTitle.text}
                              </span>
                            </p>
                            <p>
                              <span className="app-text-muted">Span Margin:</span>{" "}
                              {formatSpanElmLakhs(row.span_margin_required)}
                            </p>
                            <p>
                              <span className="app-text-muted">ELM @2%:</span>{" "}
                              {formatSpanElmLakhs(row.elm_margin_required)}
                            </p>
                            <p title={carryRoiTitle}>
                              <span className="app-text-muted">
                                Carry Returns:
                              </span>{" "}
                              <span className={cr.className}>{cr.text}</span>
                            </p>
                            <PositionActionsCard
                              row={row}
                              rowKey={rowKey}
                              hedgeExpanded={hedgeOpen}
                              onToggleHedges={onToggleHedges}
                            />
                            {hedgeOpen && isHedgeable(row.hedgeable) ? (
                              <PortfolioHedgeExpandPanel
                                row={row}
                                onSelect={(opt) =>
                                  setHedgeSheet({ row, opt })
                                }
                              />
                            ) : null}
                          </div>
                        );
                      })}
                    </div>
                    <PortfolioGroupPayoffPanel
                      stockCode={g.stockCode}
                      exchangeCode={g.exchangeCode}
                      expiryDisplay={g.expiryDate}
                      rows={g.rows}
                    />
                  </div>
                ) : null}
              </div>
            );
          })
        )}
      </div>

      {hedgeSheet ? (
        <PortfolioHedgeOrderSheet
          row={hedgeSheet.row}
          hedgeOption={hedgeSheet.opt}
          onClose={closeHedgeSheet}
          onBuy={onHedgeSheetBuy}
        />
      ) : null}

      {executePreview ? (
        <ExecutionPreviewModal
          open
          onClose={closeExecutePreview}
          stockCode={String(executePreview.row.stock_code ?? "").trim()}
          exchangeCode={
            String(executePreview.row.exchange_code ?? "NFO").trim() ||
            "NFO"
          }
          expiryDisplay={String(executePreview.row.expiry_date ?? "").trim()}
          legs={executionLegs}
        />
      ) : null}
    </>
  );
}
