"use client";

import Link from "next/link";
import { useOrderConfirm } from "@/components/order/OrderConfirmProvider";
import {
  ltpAsOrderPrice,
  squareOffToOrderPayload,
} from "@/lib/order-confirm";

/**
 * Matches legacy `templates/portfolio.html` fed by `get_positions` (see
 * `legacy/app/api/v1/route_portfolio.py` serve_landing).
 *
 * Responsive: &lt;xl = card layout (phones + tablets); xl+ = wide table with
 * side-by-side actions. Fallback horizontal scroll remains inside `.app-table-wrap`.
 */
export type PortfolioPositionRecord = Record<string, unknown>;

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

function PositionActionsTable({ row }: { row: PortfolioPositionRecord }) {
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
        <Link href={hedgeHref(row)} className={btnSecondaryTable}>
          Get Hedges
        </Link>
      ) : null}
    </div>
  );
}

function PositionActionsCard({ row }: { row: PortfolioPositionRecord }) {
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
        <Link
          href={hedgeHref(row)}
          className={`${btnSecondaryCard} w-full min-h-11 sm:min-h-0 sm:flex-1`}
        >
          Get Hedges
        </Link>
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
                positions.map((row, i) => {
                  const mtm = formatMtmCarry(row.current_profit);
                  const carry = formatMtmCarry(row.carry_profit);
                  const cr = formatCarryRet(row.carry_margin_returns);
                  const carryRoiTitle = carryMarginRoiTitle(row);
                  const spot = formatSpot(row.spot_price);
                  const qty = coerceNum(row.quantity);
                  return (
                    <tr
                      key={`${String(row.option)}-${String(row.stock_code)}-${i}`}
                      className="app-table-row"
                    >
                      <td className={`${tdBase} text-center tabular-nums`}>
                        {i + 1}
                      </td>
                      <td className={tdBase}>{String(row.option ?? "—")}</td>
                      <td className={tdBase}>{String(row.action ?? "—")}</td>
                      <td className={`${tdBase} text-right tabular-nums`}>
                        {qty != null
                          ? qty.toLocaleString("en-IN", {
                              maximumFractionDigits: Number.isInteger(qty)
                                ? 0
                                : 4,
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
                        <PositionActionsTable row={row} />
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Phones & tablets: stacked cards, comfortable tap targets */}
      <div className="space-y-3 xl:hidden">
        {positions.length === 0 ? (
          <p className="py-6 text-center text-base app-text-muted">
            {emptyMessage}
          </p>
        ) : (
          positions.map((row, i) => {
            const mtm = formatMtmCarry(row.current_profit);
            const carry = formatMtmCarry(row.carry_profit);
            const cr = formatCarryRet(row.carry_margin_returns);
            const carryRoiTitle = carryMarginRoiTitle(row);
            const spot = formatSpot(row.spot_price);
            const qty = coerceNum(row.quantity);
            return (
              <div
                key={`card-${String(row.option)}-${String(row.stock_code)}-${i}`}
                className="app-card-muted space-y-2.5 p-4 text-sm sm:p-5"
              >
                <h3 className="text-base font-semibold leading-snug text-zinc-900 dark:text-zinc-100">
                  {String(row.option ?? "—")}
                </h3>
                <p>
                  <span className="app-text-muted">Position:</span>{" "}
                  {String(row.action ?? "—")}
                </p>
                <p>
                  <span className="app-text-muted">Qty:</span>{" "}
                  {qty != null
                    ? qty.toLocaleString("en-IN", {
                        maximumFractionDigits: Number.isInteger(qty) ? 0 : 4,
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
                  <span className={carry.className}>{carry.text}</span>
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
                  <span className="app-text-muted">Carry Returns:</span>{" "}
                  <span className={cr.className}>{cr.text}</span>
                </p>
                <PositionActionsCard row={row} />
              </div>
            );
          })
        )}
      </div>
    </>
  );
}
