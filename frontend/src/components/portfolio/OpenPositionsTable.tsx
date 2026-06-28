"use client";

import { Fragment, useCallback, useMemo, useState, type MouseEvent } from "react";
import Link from "next/link";
import {
  OrderExecutionConfirmDialog,
  type ExecutionPreviewLeg,
} from "@/components/order/OrderExecutionConfirmDialog";
import { PortfolioGroupPayoffPanel } from "@/components/portfolio/PortfolioGroupPayoffPanel";
import { PortfolioHedgePanel } from "@/components/portfolio/PortfolioHedgePanel";
import type { StrategyHedgeCandidate } from "@/lib/hedge/api";
import {
  candidateToExecutionLeg,
  candidateToStrategyLeg,
} from "@/lib/hedge/legs";
import { buildPortfolioPositionGroups } from "@/lib/portfolio/groupPositions";
import type { PortfolioPositionGroup } from "@/lib/portfolio/groupPositions";
import { ltpAsOrderPrice, squareOffToOrderPayload } from "@/lib/order-confirm";
import type { PortfolioPositionRecord } from "@/lib/portfolio";
import { useBreakChunkQty } from "@/lib/use-break-chunk-qty";
import type { StrategyLeg } from "@/lib/strategy-builder/types";

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
  const carry = coerceNum(row.carry_profit);
  const span = coerceNum(row.span_margin_required);
  const elm = coerceNum(row.elm_margin_required) ?? 0;
  const dte = coerceNum(row.days_to_expiry);
  const cr = coerceNum(row.carry_margin_returns);
  const totalMargin = span != null ? span + elm : null;
  if (
    carry == null ||
    totalMargin == null ||
    dte == null ||
    cr == null ||
    totalMargin <= 0 ||
    dte <= 0
  ) {
    return undefined;
  }
  const carryS = `₹${carry.toLocaleString("en-IN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
  const spanS = `₹${span!.toLocaleString("en-IN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
  const elmS = `₹${elm.toLocaleString("en-IN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
  const marginS = `₹${totalMargin.toLocaleString("en-IN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
  return [
    "Annualised carry return on margin (Span + ELM) (%):",
    "(Carry ÷ DTE) × 365 ÷ (Span + ELM) × 100",
    "",
    `Carry (LTP × qty) = ${carryS}`,
    `DTE = ${Math.round(dte)} days`,
    `span = ${spanS}`,
    `ELM = ${elmS}`,
    `total margin (span + ELM) = ${marginS}`,
    "",
    `= (${carry.toLocaleString("en-IN", { maximumFractionDigits: 2 })} ÷ ${Math.round(dte)}) × 365 ÷ ${totalMargin.toLocaleString("en-IN", { maximumFractionDigits: 2 })} × 100`,
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
  return `/place-order?${params.toString()}`;
}

const btnPrimaryTable =
  "app-btn-primary shrink-0 px-2.5 py-1.5 text-xs font-medium 2xl:px-3 2xl:py-2 2xl:text-sm";
const btnPrimaryCard =
  "app-btn-primary px-3 py-2.5 text-sm font-medium";
const btnHedgeTable =
  "app-btn-secondary shrink-0 px-2.5 py-1.5 text-xs font-medium 2xl:px-3 2xl:py-2 2xl:text-sm";
const btnHedgeCard =
  "app-btn-secondary shrink-0 px-3 py-2 text-sm font-medium";

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

function PositionActionsTable({ row }: { row: PortfolioPositionRecord }) {
  const squareOk = squareOffToOrderPayload(row) != null;
  return (
    <div className="flex flex-nowrap items-center justify-end gap-1.5 2xl:gap-2">
      {squareOk ? (
        <Link href={squareOffHref(row)} className={btnPrimaryTable}>
          Square Off
        </Link>
      ) : (
        <span
          className={`${btnPrimaryTable} pointer-events-none cursor-not-allowed opacity-50`}
          aria-disabled
        >
          Square Off
        </span>
      )}
    </div>
  );
}

function PositionActionsCard({ row }: { row: PortfolioPositionRecord }) {
  const squareOk = squareOffToOrderPayload(row) != null;
  return (
    <div className="flex w-full flex-col gap-2 sm:flex-row sm:flex-wrap">
      {squareOk ? (
        <Link
          href={squareOffHref(row)}
          className={`${btnPrimaryCard} w-full min-h-11 sm:min-h-0 sm:flex-1`}
        >
          Square Off
        </Link>
      ) : (
        <span
          className={`${btnPrimaryCard} w-full min-h-11 cursor-not-allowed opacity-50 sm:min-h-0 sm:flex-1`}
          aria-disabled
        >
          Square Off
        </span>
      )}
    </div>
  );
}

const thBase =
  "whitespace-nowrap px-2 py-2 text-xs font-semibold text-zinc-700 2xl:px-3 2xl:py-2.5 2xl:text-sm dark:text-zinc-300";
const tdShell =
  "whitespace-nowrap px-2 py-2 align-middle text-xs 2xl:px-3 2xl:py-2.5 2xl:text-sm";
const tdInk = "text-zinc-800 dark:text-zinc-200";
const tdBase = `${tdShell} ${tdInk}`;

type OpenPositionsTableProps = {
  positions: PortfolioPositionRecord[];
  emptyMessage?: string;
};

function GroupHedgeButton({
  className,
  active,
  onClick,
}: {
  className: string;
  active: boolean;
  onClick: (e: MouseEvent) => void;
}) {
  return (
    <button
      type="button"
      className={className}
      aria-pressed={active}
      onClick={onClick}
    >
      {active ? "Hedging…" : "Hedge"}
    </button>
  );
}

function GroupExpandedExtras({
  g,
  hedgeActive,
  proposedLeg,
  selectedCandidate,
  onSelectCandidate,
  onLotSizeChange,
  onExecuteHedge,
}: {
  g: PortfolioPositionGroup;
  hedgeActive: boolean;
  proposedLeg: StrategyLeg | null;
  selectedCandidate: StrategyHedgeCandidate | null;
  onSelectCandidate: (c: StrategyHedgeCandidate | null) => void;
  onLotSizeChange: (lotSize: number) => void;
  onExecuteHedge: () => void;
}) {
  return (
    <>
      <PortfolioGroupPayoffPanel
        stockCode={g.stockCode}
        exchangeCode={g.exchangeCode}
        expiryDisplay={g.expiryDate}
        rows={g.rows}
        proposedLeg={proposedLeg}
      />
      {hedgeActive ? (
        <PortfolioHedgePanel
          group={g}
          selectedCandidate={selectedCandidate}
          onSelectCandidate={onSelectCandidate}
          onExecute={onExecuteHedge}
          onLotSizeChange={onLotSizeChange}
        />
      ) : null}
    </>
  );
}

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
  const [hedgeActiveGroupKey, setHedgeActiveGroupKey] = useState<string | null>(
    null,
  );
  const [selectedCandidate, setSelectedCandidate] =
    useState<StrategyHedgeCandidate | null>(null);
  const [hedgeLotSize, setHedgeLotSize] = useState(1);
  const [executeOpen, setExecuteOpen] = useState(false);

  const hedgeActiveGroup = useMemo(
    () => groups.find((g) => g.key === hedgeActiveGroupKey) ?? null,
    [groups, hedgeActiveGroupKey],
  );

  const proposedLegForActive = useMemo(() => {
    if (!selectedCandidate || hedgeLotSize <= 0) return null;
    return candidateToStrategyLeg(selectedCandidate, hedgeLotSize);
  }, [selectedCandidate, hedgeLotSize]);

  const executeLegs: ExecutionPreviewLeg[] = useMemo(
    () => (selectedCandidate ? [candidateToExecutionLeg(selectedCandidate)] : []),
    [selectedCandidate],
  );

  const { chunkQty, setChunkQty, defaultsQuery, chunkReady } = useBreakChunkQty({
    stockCode: hedgeActiveGroup?.stockCode ?? "",
    exchangeCode: hedgeActiveGroup?.exchangeCode ?? "NFO",
    expiryDisplay: hedgeActiveGroup?.expiryDate ?? "",
    enabled: executeOpen,
  });

  const clearHedgeState = useCallback(() => {
    setHedgeActiveGroupKey(null);
    setSelectedCandidate(null);
    setHedgeLotSize(1);
    setExecuteOpen(false);
  }, []);

  const toggleGroup = useCallback(
    (key: string) => {
      setExpandedGroups((prev) => {
        const next = new Set(prev);
        if (next.has(key)) {
          next.delete(key);
          if (hedgeActiveGroupKey === key) {
            setHedgeActiveGroupKey(null);
            setSelectedCandidate(null);
            setHedgeLotSize(1);
            setExecuteOpen(false);
          }
        } else {
          next.add(key);
        }
        return next;
      });
    },
    [hedgeActiveGroupKey],
  );

  const openHedgeForGroup = useCallback((key: string) => {
    setExpandedGroups((prev) => {
      const next = new Set(prev);
      next.add(key);
      return next;
    });
    setHedgeActiveGroupKey(key);
    setSelectedCandidate(null);
    setHedgeLotSize(1);
    setExecuteOpen(false);
  }, []);

  const handleHedgeButtonClick = useCallback(
    (e: MouseEvent, groupKey: string) => {
      e.stopPropagation();
      if (hedgeActiveGroupKey === groupKey) {
        clearHedgeState();
        return;
      }
      openHedgeForGroup(groupKey);
    },
    [clearHedgeState, hedgeActiveGroupKey, openHedgeForGroup],
  );

  const handleExecuteHedge = useCallback(() => {
    if (!selectedCandidate || !hedgeActiveGroup) return;
    setExecuteOpen(true);
  }, [hedgeActiveGroup, selectedCandidate]);

  const handleExecuteClose = useCallback(() => {
    setExecuteOpen(false);
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

  return (
    <>
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
                    "Annualised on margin (Span + ELM) (%): (Carry ÷ DTE) × 365 ÷ (Span + ELM) × 100. Hover a row value for the exact inputs."
                  }
                >
                  Carry Ret.
                </th>
                <th
                  className={`${thBase} text-right whitespace-nowrap`}
                  title="Square off this leg"
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
                        <td className={`${tdBase} text-right align-middle`}>
                          <GroupHedgeButton
                            className={btnHedgeTable}
                            active={hedgeActiveGroupKey === g.key}
                            onClick={(e) => handleHedgeButtonClick(e, g.key)}
                          />
                        </td>
                      </tr>
                      {isOpen
                        ? g.rows.map((row, localIdx) => {
                            const rowKey = childRowKey(g.key, localIdx);
                            const mtm = formatMtmCarry(row.current_profit);
                            const carry = formatMtmCarry(row.carry_profit);
                            const cr = formatCarryRet(row.carry_margin_returns);
                            const carryRoiTitle = carryMarginRoiTitle(row);
                            const spot = formatSpot(row.spot_price);
                            const qty = coerceNum(row.quantity);
                            const num =
                              childRowNumber.get(rowKey) ?? localIdx + 1;
                            return (
                              <tr
                                key={rowKey}
                                className="app-table-row bg-zinc-50/40 dark:bg-zinc-950/25"
                              >
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
                                  <PositionActionsTable row={row} />
                                </td>
                              </tr>
                            );
                          })
                        : null}
                      {isOpen ? (
                        <tr className="app-table-row">
                          <td className="p-0 align-top" colSpan={13}>
                            <GroupExpandedExtras
                              g={g}
                              hedgeActive={hedgeActiveGroupKey === g.key}
                              proposedLeg={
                                hedgeActiveGroupKey === g.key
                                  ? proposedLegForActive
                                  : null
                              }
                              selectedCandidate={
                                hedgeActiveGroupKey === g.key
                                  ? selectedCandidate
                                  : null
                              }
                              onSelectCandidate={setSelectedCandidate}
                              onLotSizeChange={setHedgeLotSize}
                              onExecuteHedge={handleExecuteHedge}
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
                <div className="flex items-start gap-3 p-4 sm:p-5">
                  <button
                    type="button"
                    aria-expanded={isOpen}
                    className="mt-0.5 shrink-0 tabular-nums text-zinc-500 dark:text-zinc-400"
                    onClick={() => toggleGroup(g.key)}
                  >
                    {isOpen ? "▼" : "▶"}
                  </button>
                  <button
                    type="button"
                    className="min-w-0 flex-1 space-y-2 text-left"
                    onClick={() => toggleGroup(g.key)}
                  >
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
                  </button>
                  <GroupHedgeButton
                    className={btnHedgeCard}
                    active={hedgeActiveGroupKey === g.key}
                    onClick={(e) => handleHedgeButtonClick(e, g.key)}
                  />
                </div>
                {isOpen ? (
                  <div className="border-t border-zinc-200/80 dark:border-zinc-700/80">
                    <div className="space-y-3 p-4 sm:space-y-4 sm:p-5">
                      {g.rows.map((row, localIdx) => {
                        const rowKey = childRowKey(g.key, localIdx);
                        const mtm = formatMtmCarry(row.current_profit);
                        const carryTitle = formatMtmCarry(row.carry_profit);
                        const cr = formatCarryRet(row.carry_margin_returns);
                        const carryRoiTitle = carryMarginRoiTitle(row);
                        const spot = formatSpot(row.spot_price);
                        const qty = coerceNum(row.quantity);
                        return (
                          <div
                            key={`card-${rowKey}`}
                            className="space-y-2.5 rounded-md border border-zinc-200/90 bg-white/80 p-4 dark:border-zinc-700 dark:bg-zinc-950/40"
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
                            <PositionActionsCard row={row} />
                          </div>
                        );
                      })}
                    </div>
                    <GroupExpandedExtras
                      g={g}
                      hedgeActive={hedgeActiveGroupKey === g.key}
                      proposedLeg={
                        hedgeActiveGroupKey === g.key
                          ? proposedLegForActive
                          : null
                      }
                      selectedCandidate={
                        hedgeActiveGroupKey === g.key ? selectedCandidate : null
                      }
                      onSelectCandidate={setSelectedCandidate}
                      onLotSizeChange={setHedgeLotSize}
                      onExecuteHedge={handleExecuteHedge}
                    />
                  </div>
                ) : null}
              </div>
            );
          })
        )}
      </div>

      {hedgeActiveGroup && selectedCandidate ? (
        <OrderExecutionConfirmDialog
          open={executeOpen}
          onClose={handleExecuteClose}
          stockCode={hedgeActiveGroup.stockCode}
          exchangeCode={hedgeActiveGroup.exchangeCode}
          expiryDisplay={hedgeActiveGroup.expiryDate}
          legs={executeLegs}
          controlledChunk={{
            chunkQty,
            onChunkQtyChange: setChunkQty,
            defaultsQuery,
            chunkReady,
          }}
        />
      ) : null}
    </>
  );
}
