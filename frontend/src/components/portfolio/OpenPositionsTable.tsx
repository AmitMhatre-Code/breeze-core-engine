"use client";

import {
  Fragment,
  useCallback,
  useEffect,
  useMemo,
  useState,
  type CSSProperties,
  type MouseEvent,
} from "react";
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
import { formatSignedRupees } from "@/lib/portfolio/totals";
import { formatIndianMoneyCompact } from "@/lib/format-money-in";
import { useGroupLiveOverlay } from "@/lib/portfolio/useGroupLiveOverlay";
import { useGroupSubscriptionHolders } from "@/lib/portfolio/useGroupSubscriptionHolders";
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

const mutedNumClass = "text-muted";

/** MTM / Carry: gain = green +₹x, loss = red −₹x, flat = muted. */
function formatMtmCarry(raw: unknown): { text: string; className: string } {
  return formatSignedRupees(coerceNum(raw));
}

/** Span / ELM: plain grouped rupees under ₹1L, ₹X.XXL/Cr at or above. */
function formatSpanElm(raw: unknown): string {
  const n = coerceNum(raw);
  if (n == null) return "—";
  return formatIndianMoneyCompact(n, { shortSuffix: true, skipK: true });
}

/** "03-Jul-2026" (broker expiry_date format) -> "03JUL26" (compact symbol format). */
function formatExpiryCompact(raw: unknown): string {
  const s = String(raw ?? "").trim();
  const m = s.match(/^(\d{1,2})-([A-Za-z]{3})-(\d{4})$/);
  if (!m) return s.replace(/-/g, "").toUpperCase();
  const [, dd, mon, yyyy] = m;
  return `${dd.padStart(2, "0")}${mon.toUpperCase()}${yyyy.slice(-2)}`;
}

/** Compact broker-style symbol e.g. "NIFTY 03JUL26 23700" (Type column already shows PE/CE). */
function formatOptionSymbol(row: PortfolioPositionRecord): string {
  const stock = String(row.stock_code ?? "").trim();
  const expiry = formatExpiryCompact(row.expiry_date);
  const strike = coerceNum(row.strike_price);
  if (!stock || !expiry || strike == null) {
    return String(row.option ?? "—");
  }
  return `${stock} ${expiry} ${strike}`;
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
      minimumFractionDigits: 1,
      maximumFractionDigits: 1,
    });
  if (n < 0) {
    return {
      text: `(${fmt(n)}%)`,
      className: "font-medium text-down",
    };
  }
  if (n > 0) {
    return {
      text: `${fmt(n)}%`,
      className: "font-medium text-up",
    };
  }
  return { text: "0.0%", className: mutedNumClass };
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
      className: "font-medium text-down",
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

/**
 * Accent pill buttons/badges: `--accent-strong`/`--accent`/`--accent-ink` already flip per
 * theme (see globals.css), so one inline style works in both light and dark — no dark:
 * variants needed, matching the InterpretationBadge convention.
 */
const ACCENT_OUTLINE_STYLE: CSSProperties = {
  borderColor: "var(--accent-strong)",
  color: "var(--accent-strong)",
};
const ACCENT_SOLID_STYLE: CSSProperties = {
  backgroundColor: "var(--accent-strong)",
  color: "var(--accent-ink)",
};

const pillOutlineTable =
  "inline-flex shrink-0 items-center justify-center rounded-lg border px-3 py-1.5 text-xs font-semibold transition hover:bg-[var(--accent-tint)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] 2xl:px-3.5 2xl:py-2 2xl:text-sm";
const pillOutlineCard =
  "inline-flex items-center justify-center rounded-lg border px-3 py-2.5 text-sm font-semibold transition hover:bg-[var(--accent-tint)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]";
const pillSolidTable =
  "inline-flex shrink-0 items-center justify-center rounded-lg px-3 py-1.5 text-xs font-semibold transition hover:brightness-95 focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] focus-visible:ring-offset-2 focus-visible:ring-offset-background 2xl:px-3.5 2xl:py-2 2xl:text-sm";
const pillSolidCard =
  "inline-flex shrink-0 items-center justify-center rounded-lg px-3 py-2 text-sm font-semibold transition hover:brightness-95 focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]";

const btnPrimaryTable = pillOutlineTable;
const btnPrimaryCard = pillOutlineCard;

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
        <Link href={squareOffHref(row)} className={btnPrimaryTable} style={ACCENT_OUTLINE_STYLE}>
          Square Off
        </Link>
      ) : (
        <span
          className={`${btnPrimaryTable} pointer-events-none cursor-not-allowed opacity-50`}
          style={ACCENT_OUTLINE_STYLE}
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
          style={ACCENT_OUTLINE_STYLE}
        >
          Square Off
        </Link>
      ) : (
        <span
          className={`${btnPrimaryCard} w-full min-h-11 cursor-not-allowed opacity-50 sm:min-h-0 sm:flex-1`}
          style={ACCENT_OUTLINE_STYLE}
          aria-disabled
        >
          Square Off
        </span>
      )}
    </div>
  );
}

const thBase =
  "whitespace-nowrap px-2 py-2 text-[13px] font-semibold uppercase tracking-wide text-faint 2xl:px-3 2xl:py-2.5";
const tdShell =
  "whitespace-nowrap px-2 py-2 align-middle text-xs 2xl:px-3 2xl:py-2.5 2xl:text-sm";
const tdInk = "text-foreground";
const tdBase = `${tdShell} ${tdInk}`;

/** PE/CE — outline only (no fill), red for puts / green for calls. */
function optionTypeAbbrev(raw: unknown): "PE" | "CE" | null {
  const t = String(raw ?? "").trim().toLowerCase();
  if (t === "put" || t === "p" || t === "pe") return "PE";
  if (t === "call" || t === "c" || t === "ce") return "CE";
  return null;
}

function OptionTypePill({ raw }: { raw: unknown }) {
  const abbr = optionTypeAbbrev(raw);
  if (!abbr) return <span className="app-text-muted">—</span>;
  const isPut = abbr === "PE";
  return (
    <span
      className="inline-flex items-center justify-center rounded-md border px-2 py-0.5 font-mono text-[13px] font-bold"
      style={{
        borderColor: isPut ? "var(--down)" : "var(--up)",
        color: isPut ? "var(--down)" : "var(--up)",
      }}
    >
      {abbr}
    </span>
  );
}

/** BUY/SELL — filled soft-tint pill, same convention InterpretationBadge uses. */
function PositionPill({ raw }: { raw: unknown }) {
  const t = String(raw ?? "").trim().toUpperCase();
  if (t !== "BUY" && t !== "SELL") {
    return <span className="app-text-muted">{t || "—"}</span>;
  }
  const isSell = t === "SELL";
  return (
    <span
      className="inline-flex items-center justify-center rounded-md px-2 py-0.5 text-[13px] font-bold"
      style={{
        backgroundColor: isSell ? "var(--up-tint)" : "var(--down-tint)",
        color: isSell ? "var(--up)" : "var(--down)",
      }}
    >
      {t}
    </span>
  );
}

/** Small pulsing dot — same convention as AppShell's session indicator. */
function LiveDot({ title }: { title: string }) {
  return (
    <span className="relative inline-flex size-2 shrink-0" title={title} aria-label={title}>
      <span className="absolute inline-flex size-full animate-ping rounded-full bg-up opacity-75" />
      <span className="relative inline-flex size-2 rounded-full bg-up" />
    </span>
  );
}

type OpenPositionsTableProps = {
  positions: PortfolioPositionRecord[];
  emptyMessage?: string;
  /** Group key expanded by default (e.g. the largest position by margin) so it's live immediately. */
  defaultExpandedGroupKey?: string | null;
  /** Fires whenever the number of currently-live (expanded, WS-fed) groups changes. */
  onLiveGroupCountChange?: (count: number) => void;
};

function GroupHedgeButton({
  variant,
  active,
  onClick,
}: {
  variant: "table" | "card";
  active: boolean;
  onClick: (e: MouseEvent) => void;
}) {
  const className = active
    ? variant === "table"
      ? pillSolidTable
      : pillSolidCard
    : variant === "table"
      ? pillOutlineTable
      : pillOutlineCard;
  return (
    <button
      type="button"
      className={className}
      style={active ? ACCENT_SOLID_STYLE : ACCENT_OUTLINE_STYLE}
      aria-pressed={active}
      onClick={onClick}
    >
      {active ? "Hedging…" : "Hedge"}
    </button>
  );
}

function GroupExpandedExtras({
  g,
  holderId,
  hedgeActive,
  proposedLeg,
  selectedCandidate,
  onSelectCandidate,
  onLotSizeChange,
  onExecuteHedge,
}: {
  g: PortfolioPositionGroup;
  holderId: string;
  hedgeActive: boolean;
  proposedLeg: StrategyLeg | null;
  selectedCandidate: StrategyHedgeCandidate | null;
  onSelectCandidate: (c: StrategyHedgeCandidate | null) => void;
  onLotSizeChange: (lotSize: number) => void;
  onExecuteHedge: () => void;
}) {
  return (
    <div className="border-t border-border-soft bg-panel2">
      <div
        className={
          hedgeActive
            ? "grid min-w-0 divide-y divide-border-soft md:grid-cols-[3fr_2fr] md:divide-x md:divide-y-0"
            : "min-w-0"
        }
      >
        <PortfolioGroupPayoffPanel
          stockCode={g.stockCode}
          exchangeCode={g.exchangeCode}
          expiryDisplay={g.expiryDate}
          rows={g.rows}
          proposedLeg={proposedLeg}
          holderId={holderId}
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
      </div>
    </div>
  );
}

type GroupBlockProps = {
  g: PortfolioPositionGroup;
  isOpen: boolean;
  onToggle: () => void;
  holderId: string;
  hedgeActive: boolean;
  onHedgeButtonClick: (e: MouseEvent) => void;
  proposedLeg: StrategyLeg | null;
  selectedCandidate: StrategyHedgeCandidate | null;
  onSelectCandidate: (c: StrategyHedgeCandidate | null) => void;
  onLotSizeChange: (lotSize: number) => void;
  onExecuteHedge: () => void;
  startNumber: number;
  onLiveChange: (groupKey: string, live: boolean) => void;
};

/**
 * One group's rows (desktop table). Owns the live-chain overlay hook so it's only
 * fetched/subscribed while `isOpen` — collapsed groups render straight from the
 * polled `/portfolio/data` snapshot passed in via `g.rows`.
 */
function PortfolioGroupTableBlock({
  g,
  isOpen,
  onToggle,
  holderId,
  hedgeActive,
  onHedgeButtonClick,
  proposedLeg,
  selectedCandidate,
  onSelectCandidate,
  onLotSizeChange,
  onExecuteHedge,
  startNumber,
  onLiveChange,
}: GroupBlockProps) {
  const { rows: liveRows, isLive } = useGroupLiveOverlay(g, holderId, isOpen);
  useEffect(() => {
    onLiveChange(g.key, isLive);
  }, [g.key, isLive, onLiveChange]);
  const spotAgg = formatSpot(liveRows[0]?.spot_price);
  const mtmSum = sumNumericField(liveRows, "current_profit");
  const carrySum = sumNumericField(liveRows, "carry_profit");
  const spanSum = sumNumericField(liveRows, "span_margin_required");
  const elmSum = sumNumericField(liveRows, "elm_margin_required");
  const gMtm = formatMtmCarry(mtmSum);
  const gCarry = formatMtmCarry(carrySum);
  const groupTitle = `${g.stockCode} · ${g.expiryDate} · ${g.rows.length} leg${g.rows.length === 1 ? "" : "s"}`;

  return (
    <Fragment>
      <tr
        role="button"
        tabIndex={0}
        aria-expanded={isOpen}
        title={isOpen ? "Collapse group" : "Expand group"}
        className="app-table-row cursor-pointer select-none hover:bg-panel2"
        onClick={onToggle}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onToggle();
          }
        }}
      >
        <td className={`${tdBase} text-center tabular-nums text-muted`}>
          {isOpen ? "▼" : "▶"}
        </td>
        <td className={`${tdBase} font-medium`}>
          <span className="inline-flex items-center gap-1.5">
            {groupTitle}
            {isLive ? <LiveDot title="Live · WebSocket" /> : null}
          </span>
        </td>
        <td className={tdBase}>—</td>
        <td className={tdBase}>—</td>
        <td className={`${tdBase} text-right`}>—</td>
        <td className={`${tdBase} text-right`}>—</td>
        <td className={`${tdBase} text-right`}>—</td>
        <td className={`${tdBase} text-right tabular-nums ${spotAgg.className}`}>
          {spotAgg.text}
        </td>
        <td className={`${tdShell} text-right tabular-nums font-medium ${gMtm.className}`}>
          {gMtm.text}
        </td>
        <td className={`${tdShell} text-right tabular-nums font-medium ${gCarry.className}`}>
          {gCarry.text}
        </td>
        <td className={`${tdBase} text-right tabular-nums`}>
          {formatSpanElm(spanSum)}
        </td>
        <td className={`${tdBase} text-right tabular-nums`}>
          {formatSpanElm(elmSum)}
        </td>
        <td className={`${tdShell} text-right tabular-nums app-text-muted`}>—</td>
        <td className={`${tdBase} text-right align-middle`}>
          <GroupHedgeButton variant="table" active={hedgeActive} onClick={onHedgeButtonClick} />
        </td>
      </tr>
      {isOpen
        ? liveRows.map((row, localIdx) => {
            const rowKey = childRowKey(g.key, localIdx);
            const mtm = formatMtmCarry(row.current_profit);
            const carry = formatMtmCarry(row.carry_profit);
            const cr = formatCarryRet(row.carry_margin_returns);
            const carryRoiTitle = carryMarginRoiTitle(row);
            const spot = formatSpot(row.spot_price);
            const qty = coerceNum(row.quantity);
            const num = startNumber + localIdx + 1;
            return (
              <tr key={rowKey} className="app-table-row bg-panel2">
                <td className={`${tdBase} text-center tabular-nums`}>{num}</td>
                <td className={tdBase}>{formatOptionSymbol(row)}</td>
                <td className={tdBase}>
                  <OptionTypePill raw={row.right} />
                </td>
                <td className={tdBase}>
                  <PositionPill raw={row.action} />
                </td>
                <td className={`${tdBase} text-right tabular-nums`}>
                  {qty != null
                    ? qty.toLocaleString("en-IN", {
                        maximumFractionDigits: Number.isInteger(qty) ? 0 : 4,
                      })
                    : "—"}
                </td>
                <td className={`${tdBase} text-right tabular-nums`}>
                  {formatPriceCell(row.average_price)}
                </td>
                <td className={`${tdBase} text-right tabular-nums`}>
                  {formatPriceCell(row.ltp)}
                </td>
                <td className={`${tdBase} text-right tabular-nums ${spot.className}`}>
                  {spot.text}
                </td>
                <td className={`${tdShell} text-right tabular-nums font-medium ${mtm.className}`}>
                  {mtm.text}
                </td>
                <td className={`${tdShell} text-right tabular-nums font-medium ${carry.className}`}>
                  {carry.text}
                </td>
                <td className={`${tdBase} text-right tabular-nums`}>
                  {formatSpanElm(row.span_margin_required)}
                </td>
                <td className={`${tdBase} text-right tabular-nums`}>
                  {formatSpanElm(row.elm_margin_required)}
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
          <td className="p-0 align-top" colSpan={14}>
            <GroupExpandedExtras
              g={g}
              holderId={holderId}
              hedgeActive={hedgeActive}
              proposedLeg={hedgeActive ? proposedLeg : null}
              selectedCandidate={hedgeActive ? selectedCandidate : null}
              onSelectCandidate={onSelectCandidate}
              onLotSizeChange={onLotSizeChange}
              onExecuteHedge={onExecuteHedge}
            />
          </td>
        </tr>
      ) : null}
    </Fragment>
  );
}

/** One group's card (mobile) — same live-overlay hook as the desktop block. */
function PortfolioGroupCardBlock({
  g,
  isOpen,
  onToggle,
  holderId,
  hedgeActive,
  onHedgeButtonClick,
  proposedLeg,
  selectedCandidate,
  onSelectCandidate,
  onLotSizeChange,
  onExecuteHedge,
  onLiveChange,
}: GroupBlockProps) {
  const { rows: liveRows, isLive } = useGroupLiveOverlay(g, holderId, isOpen);
  useEffect(() => {
    onLiveChange(g.key, isLive);
  }, [g.key, isLive, onLiveChange]);
  const spotAgg = formatSpot(liveRows[0]?.spot_price);
  const mtmSum = sumNumericField(liveRows, "current_profit");
  const carrySum = sumNumericField(liveRows, "carry_profit");
  const spanSum = sumNumericField(liveRows, "span_margin_required");
  const elmSum = sumNumericField(liveRows, "elm_margin_required");
  const gMtm = formatMtmCarry(mtmSum);
  const gCarry = formatMtmCarry(carrySum);
  const groupTitle = `${g.stockCode} · ${g.expiryDate} · ${g.rows.length} leg${g.rows.length === 1 ? "" : "s"}`;

  return (
    <div className="app-card-muted overflow-hidden text-sm">
      <div className="flex items-start gap-3 p-4 sm:p-5">
        <button
          type="button"
          aria-expanded={isOpen}
          className="mt-0.5 shrink-0 tabular-nums text-muted"
          onClick={onToggle}
        >
          {isOpen ? "▼" : "▶"}
        </button>
        <button
          type="button"
          className="min-w-0 flex-1 space-y-2 text-left"
          onClick={onToggle}
        >
          <h3 className="flex items-center gap-1.5 text-base font-semibold leading-snug text-foreground">
            {groupTitle}
            {isLive ? <LiveDot title="Live · WebSocket" /> : null}
          </h3>
          <p>
            <span className="app-text-muted">Spot:</span>{" "}
            <span className={spotAgg.className}>{spotAgg.text}</span>
          </p>
          <p>
            <span className="app-text-muted">MTM (sum):</span>{" "}
            <span className={`font-medium ${gMtm.className}`}>{gMtm.text}</span>
          </p>
          <p>
            <span className="app-text-muted">Carry (sum):</span>{" "}
            <span className={`font-medium ${gCarry.className}`}>{gCarry.text}</span>
          </p>
          <p>
            <span className="app-text-muted">Span Margin (sum):</span>{" "}
            {formatSpanElm(spanSum)}
          </p>
          <p>
            <span className="app-text-muted">ELM (sum):</span>{" "}
            {formatSpanElm(elmSum)}
          </p>
        </button>
        <GroupHedgeButton variant="card" active={hedgeActive} onClick={onHedgeButtonClick} />
      </div>
      {isOpen ? (
        <div className="border-t border-border-soft">
          <div className="space-y-3 p-4 sm:space-y-4 sm:p-5">
            {liveRows.map((row, localIdx) => {
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
                  className="space-y-2.5 rounded-md border border-border bg-panel2 p-4"
                >
                  <h4 className="text-sm font-semibold leading-snug text-foreground">
                    {formatOptionSymbol(row)}
                  </h4>
                  <p className="flex items-center gap-2">
                    <OptionTypePill raw={row.right} />
                    <PositionPill raw={row.action} />
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
                    <span className={`font-medium ${mtm.className}`}>{mtm.text}</span>
                  </p>
                  <p>
                    <span className="app-text-muted">Carry:</span>{" "}
                    <span className={`font-medium ${carryTitle.className}`}>
                      {carryTitle.text}
                    </span>
                  </p>
                  <p>
                    <span className="app-text-muted">Span Margin:</span>{" "}
                    {formatSpanElm(row.span_margin_required)}
                  </p>
                  <p>
                    <span className="app-text-muted">ELM @2%:</span>{" "}
                    {formatSpanElm(row.elm_margin_required)}
                  </p>
                  <p title={carryRoiTitle}>
                    <span className="app-text-muted">Carry Returns:</span>{" "}
                    <span className={cr.className}>{cr.text}</span>
                  </p>
                  <PositionActionsCard row={row} />
                </div>
              );
            })}
          </div>
          <GroupExpandedExtras
            g={g}
            holderId={holderId}
            hedgeActive={hedgeActive}
            proposedLeg={hedgeActive ? proposedLeg : null}
            selectedCandidate={hedgeActive ? selectedCandidate : null}
            onSelectCandidate={onSelectCandidate}
            onLotSizeChange={onLotSizeChange}
            onExecuteHedge={onExecuteHedge}
          />
        </div>
      ) : null}
    </div>
  );
}

export function OpenPositionsTable({
  positions,
  emptyMessage = "No positions to display",
  defaultExpandedGroupKey = null,
  onLiveGroupCountChange,
}: OpenPositionsTableProps) {
  const groups = useMemo(
    () => buildPortfolioPositionGroups(positions),
    [positions],
  );
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(
    () => new Set(defaultExpandedGroupKey ? [defaultExpandedGroupKey] : []),
  );
  const [hedgeActiveGroupKey, setHedgeActiveGroupKey] = useState<string | null>(
    null,
  );
  const [selectedCandidate, setSelectedCandidate] =
    useState<StrategyHedgeCandidate | null>(null);
  const [hedgeLotSize, setHedgeLotSize] = useState(1);
  const [executeOpen, setExecuteOpen] = useState(false);
  const { getHolderId, releaseGroup } = useGroupSubscriptionHolders();
  const [liveGroupKeys, setLiveGroupKeys] = useState<Set<string>>(
    () => new Set(),
  );

  const handleLiveChange = useCallback((groupKey: string, live: boolean) => {
    setLiveGroupKeys((prev) => {
      if (prev.has(groupKey) === live) return prev;
      const next = new Set(prev);
      if (live) next.add(groupKey);
      else next.delete(groupKey);
      return next;
    });
  }, []);

  useEffect(() => {
    onLiveGroupCountChange?.(liveGroupKeys.size);
  }, [liveGroupKeys, onLiveGroupCountChange]);

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
          releaseGroup(key);
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
    [hedgeActiveGroupKey, releaseGroup],
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

  const groupStartNumbers = useMemo(() => {
    const starts: number[] = [];
    let n = 0;
    for (const g of groups) {
      starts.push(n);
      n += g.rows.length;
    }
    return starts;
  }, [groups]);

  return (
    <>
      <div className="hidden min-w-0 max-w-full xl:block">
        <div className="app-table-wrap w-full min-w-0 max-w-full rounded-none border-0 bg-transparent dark:bg-transparent">
          <table className="w-full min-w-max table-auto border-collapse text-left">
            <thead>
              <tr className="border-b border-border bg-panel2">
                <th className={`${thBase} w-10 text-center`}>
                  <span className="sr-only">Row</span>
                </th>
                <th className={`${thBase} min-w-[11rem] text-left 2xl:min-w-[14rem]`}>
                  Option
                </th>
                <th className={`${thBase} text-left`}>Type</th>
                <th className={`${thBase} text-left`}>Position</th>
                <th className={`${thBase} text-right`}>Qty</th>
                <th className={`${thBase} text-right`}>Avg</th>
                <th className={`${thBase} text-right`}>LTP</th>
                <th className={`${thBase} text-right`}>Spot</th>
                <th className={`${thBase} text-right`}>MTM</th>
                <th className={`${thBase} text-right`}>Carry</th>
                <th className={`${thBase} text-right`} title="Span Margin">
                  Span
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
                    colSpan={14}
                    className="px-3 py-8 text-center text-sm app-text-muted"
                  >
                    {emptyMessage}
                  </td>
                </tr>
              ) : (
                groups.map((g, gi) => (
                  <PortfolioGroupTableBlock
                    key={g.key}
                    g={g}
                    isOpen={expandedGroups.has(g.key)}
                    onToggle={() => toggleGroup(g.key)}
                    holderId={getHolderId(g.key)}
                    hedgeActive={hedgeActiveGroupKey === g.key}
                    onHedgeButtonClick={(e) => handleHedgeButtonClick(e, g.key)}
                    proposedLeg={proposedLegForActive}
                    selectedCandidate={selectedCandidate}
                    onSelectCandidate={setSelectedCandidate}
                    onLotSizeChange={setHedgeLotSize}
                    onExecuteHedge={handleExecuteHedge}
                    startNumber={groupStartNumbers[gi]}
                    onLiveChange={handleLiveChange}
                  />
                ))
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
          groups.map((g) => (
            <PortfolioGroupCardBlock
              key={`card-group-${g.key}`}
              g={g}
              isOpen={expandedGroups.has(g.key)}
              onToggle={() => toggleGroup(g.key)}
              holderId={getHolderId(g.key)}
              hedgeActive={hedgeActiveGroupKey === g.key}
              onHedgeButtonClick={(e) => handleHedgeButtonClick(e, g.key)}
              proposedLeg={proposedLegForActive}
              selectedCandidate={selectedCandidate}
              onSelectCandidate={setSelectedCandidate}
              onLotSizeChange={setHedgeLotSize}
              onExecuteHedge={handleExecuteHedge}
              startNumber={0}
              onLiveChange={handleLiveChange}
            />
          ))
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
