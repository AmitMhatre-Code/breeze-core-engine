"use client";

import {
  Fragment,
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type MouseEvent,
} from "react";
import { createPortal } from "react-dom";
import {
  OrderExecutionConfirmDialog,
  type ExecutionPreviewLeg,
} from "@/components/shared/order/OrderExecutionConfirmDialog";
import { OptionTypeBadge } from "@/components/shared/badges/OptionTypeBadge";
import { OrderSideBadge } from "@/components/shared/badges/OrderSideBadge";
import { PortfolioGroupPayoffPanel } from "@/components/shared/payoff/PortfolioGroupPayoffPanel";
import { HedgeCandidatesModal } from "@/components/portfolio/HedgeCandidatesModal";
import { SquareOffLegsModal } from "@/components/portfolio/SquareOffLegsModal";
import { SquareOffRuleModal } from "@/components/portfolio/SquareOffRuleModal";
import {
  LegExitRuleModal,
  type LegExitRuleTarget,
} from "@/components/portfolio/LegExitRuleModal";
import { Checkbox } from "@/components/ui/Checkbox";
import {
  squareOffRuleGroupKey,
  type SquareOffRuleRecord,
} from "@/lib/portfolio/squareoff-rules";
import {
  resetChipClassName,
  resetChipLabel,
  resetHazardTier,
  resetMessage,
  resetNoteClassName,
} from "@/lib/portfolio/reset-warning";
import {
  useInvalidateSquareOffRules,
  useSquareOffRulesByGroup,
} from "@/lib/portfolio/useSquareOffRules";
import type { StrategyHedgeCandidate } from "@/lib/hedge/api";
import {
  candidateToExecutionLeg,
  candidateToStrategyLeg,
} from "@/lib/hedge/legs";
import {
  buildPortfolioPositionGroups,
  nettedMarginByKey,
} from "@/lib/portfolio/groupPositions";
import type { BackendGroupMargin } from "@/lib/portfolio/groupPositions";
import type { PortfolioPositionGroup } from "@/lib/portfolio/groupPositions";
import { formatSignedRupees } from "@/lib/portfolio/totals";
import { formatIndianMoneyCompact } from "@/lib/format-money-in";
import { useGroupLiveOverlay } from "@/lib/portfolio/useGroupLiveOverlay";
import { useGroupSubscriptionHolders } from "@/lib/portfolio/useGroupSubscriptionHolders";
import { useGroupPoP } from "@/lib/portfolio/useGroupPoP";
import { useLegPoP } from "@/lib/portfolio/useLegPoP";
import type { PortfolioPositionRecord } from "@/lib/portfolio";
import { formatOptionSymbolLabel } from "@/lib/strategy-builder/leg-ui-helpers";
import type { StrategyLeg } from "@/lib/strategy-builder/types";

export type PortfolioPositionsViewMode = "grouped" | "individual";

/**
 * Matches legacy `templates/portfolio.html` fed by `get_positions` (see
 * `legacy/app/api/v1/route_portfolio.py` serve_landing).
 *
 * Responsive: &lt;xl = card layout (phones + tablets); xl+ = wide table with
 * side-by-side actions. Fallback horizontal scroll remains inside `.app-table-wrap`.
 */
export type { PortfolioPositionRecord };

const EMPTY_SELECTION: ReadonlySet<number> = new Set();

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

/** Same tone class formatCarryRet/formatMtmCarry return, minus the bold weight — for the smaller sub-line under Carry. */
function demoteWeight(className: string): string {
  return className.replace(/\bfont-medium\b/g, "").trim();
}

/** Current MTM read-out inside the PB/SL gauge: compact (K/L/Cr) with an explicit +/− sign. */
function formatSignedCompact(raw: number | null): { text: string; className: string } {
  if (raw == null || !Number.isFinite(raw)) {
    return { text: "—", className: "app-text-muted" };
  }
  const v = Math.round(raw);
  if (v === 0) return { text: "₹0", className: mutedNumClass };
  const compact = formatIndianMoneyCompact(Math.abs(v), { shortSuffix: true });
  return v < 0
    ? { text: `−${compact}`, className: "text-down" }
    : { text: `+${compact}`, className: "text-up" };
}

/** Bundled "Span + ELM" cell (desktop table) — Span on top, muted "+ ELM" beneath. Group rows only. */
function SpanElmCell({
  span,
  elm,
}: {
  span: number | null;
  elm: number | null;
}) {
  return (
    <span className="inline-flex flex-col items-end leading-tight">
      <span>{formatSpanElm(span)}</span>
      <span className="text-[11px] font-normal app-text-muted">
        + {formatSpanElm(elm)}
      </span>
    </span>
  );
}

/** Canonical leg label e.g. "NIFTY.03-Jul-2026.23700" (Type column already shows PE/CE). */
function formatOptionSymbol(row: PortfolioPositionRecord): string {
  const stock = String(row.stock_code ?? "").trim();
  const expiry = String(row.expiry_date ?? "").trim();
  const strike = coerceNum(row.strike_price);
  if (!stock || !expiry || strike == null) {
    return String(row.option ?? "—");
  }
  return formatOptionSymbolLabel(stock, expiry, strike);
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

/**
 * Provenance note under LTP. The backend stamps `quote_source` on every position row
 * and the WS overlay restamps it with the chain's own source, so a price that isn't a
 * live tick says so instead of passing as one — the failure that made a profitable
 * book read as a loss was a previous session's settlement price rendered bare.
 * Live ticks stay unlabelled (the norm needs no note) and an unpriced leg needs none
 * either, since its LTP already reads "—".
 */
function quoteSourceNote(raw: unknown): string | null {
  switch (typeof raw === "string" ? raw : "") {
    case "snapshot":
    case "bhavcopy":
    case "icici_api":
      return "prev close";
    case "broker":
      return "broker px";
    default:
      return null;
  }
}

function LtpCellContent({ row }: { row: PortfolioPositionRecord }) {
  const note = quoteSourceNote(row.quote_source);
  return (
    <>
      {formatPriceCell(row.ltp)}
      {note ? (
        <span className="ml-1 text-micro app-text-muted" title="Not a live websocket tick">
          · {note}
        </span>
      ) : null}
    </>
  );
}

/** Spot: no forced decimals — only shown when the price actually carries fractional paise. */
function formatSpotPrice(raw: unknown): string {
  const n = coerceNum(raw);
  if (n == null) return "—";
  const hasFraction = Math.abs(n % 1) > 1e-9;
  return `₹${n.toLocaleString("en-IN", {
    minimumFractionDigits: hasFraction ? 2 : 0,
    maximumFractionDigits: 2,
  })}`;
}

function formatSpot(raw: unknown): { text: string; className: string } {
  if (raw == null || raw === "") {
    return { text: "—", className: "app-text-muted" };
  }
  const s = String(raw).trim();
  if (s === "Err" || s.toLowerCase() === "err") {
    return {
      text: "Err",
      className: "font-mono font-medium text-down",
    };
  }
  return { text: formatSpotPrice(raw), className: "font-mono tabular-nums" };
}

/** PoP: muted "—" until the chain fetch resolves. */
function formatPoP(pop: number | null): string {
  return pop != null && Number.isFinite(pop) ? `${pop.toFixed(1)}%` : "—";
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

const pillOutlineTable =
  "inline-flex shrink-0 items-center justify-center rounded-lg border px-3 py-1.5 text-xs font-semibold transition hover:bg-[var(--accent-tint)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent 2xl:px-3.5 2xl:py-2 2xl:text-sm";
const pillOutlineCard =
  "inline-flex items-center justify-center rounded-lg border px-3 py-2.5 text-sm font-semibold transition hover:bg-[var(--accent-tint)] focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent";

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

const thBase =
  "whitespace-nowrap px-2 py-2 text-heading font-semibold uppercase tracking-wide text-faint 2xl:px-3 2xl:py-2.5";
const tdShell =
  "whitespace-nowrap px-2 py-2 align-middle text-xs 2xl:px-3 2xl:py-2.5 2xl:text-sm";
const tdInk = "text-foreground";
const tdBase = `${tdShell} ${tdInk}`;

/**
 * Taller variant for a group's summary row only — every cell in that row uses
 * this (not just the title cell) so the row is uniform height whether or not
 * the group carries a PB/SL rule: the title cell always reserves a second
 * line (gauge when armed, "Set Profit Booking / Stop Loss" CTA when not), so
 * every summary row needs the same extra vertical room, armed or not.
 * Expanded child leg rows keep the shorter `tdShell`/`tdBase`.
 */
const tdSummaryShell =
  "whitespace-nowrap px-2 py-3 align-middle text-xs 2xl:px-3 2xl:py-3.5 2xl:text-sm";
const tdSummaryBase = `${tdSummaryShell} ${tdInk}`;

/** Table column count: Select, Row, Option, Type, Position, Qty, Avg, LTP, Spot, MTM, Carry, Span+ELM, PoP, Actions. */
const TABLE_COL_COUNT = 14;

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
  /** Server-computed netted SPAN + ELM per Strategy Group (`Success.groups`),
   * joined onto each group by key so the group row shows the netted figure
   * instead of a per-leg sum. */
  nettedGroups?: BackendGroupMargin[];
  emptyMessage?: string;
  /** Group key expanded by default (e.g. the largest position by margin) so it's live immediately. */
  defaultExpandedGroupKey?: string | null;
  /** Fires whenever the number of currently-live (expanded, WS-fed) groups changes. */
  onLiveGroupCountChange?: (count: number) => void;
  /** "grouped" (default): legs bundled by scrip+expiry. "individual": one row per leg, no payoff chart. */
  viewMode?: PortfolioPositionsViewMode;
};

/** One-shot pill trigger (Exit Rule / Square Off Selected / Hedge) — always the outline style, never a toggle. */
function GroupPillButton({
  variant,
  label,
  onClick,
  disabled = false,
  title,
}: {
  variant: "table" | "card";
  label: string;
  onClick: (e: MouseEvent) => void;
  disabled?: boolean;
  title?: string;
}) {
  const className = variant === "table" ? pillOutlineTable : pillOutlineCard;
  return (
    <button
      type="button"
      className={className}
      style={ACCENT_OUTLINE_STYLE}
      onClick={onClick}
      disabled={disabled}
      title={title}
    >
      {label}
    </button>
  );
}

/** Stable per-leg identity — unlike a group key, includes strike+right+action so
 * individual legs of the same scrip+expiry are distinguishable. */
function legIdentityKey(row: PortfolioPositionRecord): string {
  return [
    String(row.stock_code ?? "").trim().toUpperCase(),
    String(row.exchange_code ?? "NFO").trim().toUpperCase(),
    String(row.expiry_date ?? "").trim(),
    String(row.strike_price ?? "").trim(),
    String(row.right ?? "").trim().toUpperCase(),
    String(row.action ?? "").trim().toUpperCase(),
  ].join("|");
}

function legToExitRuleTarget(row: PortfolioPositionRecord): LegExitRuleTarget {
  return {
    stock_code: String(row.stock_code ?? "").trim(),
    exchange_code: String(row.exchange_code ?? "NFO").trim(),
    expiry_date: String(row.expiry_date ?? "").trim(),
    strike_price: String(row.strike_price ?? "").trim(),
    right: String(row.right ?? "").trim(),
    quantity: String(row.quantity ?? "").trim(),
    product_type:
      row.product_type != null ? String(row.product_type) : undefined,
    action: String(row.action ?? "").trim(),
  };
}

/** Individual-legs table column count: Row, Option, Type, Position, Qty, Avg, LTP, Spot, MTM, Carry, Span+ELM, PoP, Actions. */
const LEG_TABLE_COL_COUNT = 13;

/**
 * Compact status chip for a Strategy Group's PB/SL rule — shown next to the group title
 * whether the group is collapsed or expanded (unlike Hedge, which only surfaces once you
 * expand the row). Purely a status indicator; editing/disarming happens via the "Edit
 * Exit Rule" pill and its modal.
 *
 * A Reset chip is tiered by hazard (see `lib/portfolio/reset-warning`) rather than being
 * one uniform red — a benign Reset shouldn't shout, and a contra-risk one must.
 */
function ExitRuleBadge({ rule }: { rule: SquareOffRuleRecord }) {
  if (rule.status === "reset") {
    return (
      <span
        className={`inline-flex shrink-0 items-center rounded-full px-2 py-0.5 text-[11px] font-semibold ${resetChipClassName(rule)}`}
      >
        {resetChipLabel(rule)}
      </span>
    );
  }
  const label =
    rule.status === "completed"
      ? "Completed"
      : rule.status === "fired"
        ? "Fired"
        : "Armed";
  const colorClassName =
    rule.status === "completed"
      ? "bg-up-tint text-up-on-tint"
      : "bg-accent-strong text-accent-ink";
  const title =
    rule.status === "completed"
      ? "Profit Booking / Stop Loss completed — every exit order filled"
      : rule.status === "fired"
        ? "Profit Booking / Stop Loss fired — waiting for the exit orders to fill"
        : "Profit Booking / Stop Loss armed";
  return (
    <span
      className={`inline-flex shrink-0 items-center rounded-full px-2 py-0.5 text-[11px] font-semibold ${colorClassName}`}
      title={title}
    >
      {label}
    </span>
  );
}

const RESET_NOTE_PANEL_MAX_WIDTH = 360;
const RESET_NOTE_VIEWPORT_MARGIN = 8;

/**
 * The Reset explanation, under the group title.
 *
 * Truncated to one line so it can't paint over neighbouring cells in the fixed-height
 * summary row (see `tdSummaryShell`). It is NOT a `title` tooltip though — those are
 * invisible on touch and unannounced by screen readers, which is disqualifying for a
 * message that may be saying "a live order will open a position you didn't ask for". So:
 * the trigger is a real button (reachable by tap and keyboard, `aria-label` carries the
 * full text regardless of truncation) that opens a floating panel with the untruncated
 * message on click — same disclosure pattern as `InfoPopover`.
 */
function ExitRuleResetNote({
  rule,
  variant = "compact",
}: {
  rule: SquareOffRuleRecord;
  variant?: "compact" | "full";
}) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const panelId = useId();

  const close = useCallback(() => {
    setOpen(false);
    triggerRef.current?.focus();
  }, []);

  const reposition = useCallback(() => {
    const trigger = triggerRef.current;
    if (!trigger) return;
    const rect = trigger.getBoundingClientRect();
    const panelHeight = panelRef.current?.offsetHeight ?? 0;
    const spaceBelow = window.innerHeight - rect.bottom;
    const flipped = spaceBelow < panelHeight + RESET_NOTE_VIEWPORT_MARGIN && rect.top > panelHeight;
    const top = flipped ? rect.top - panelHeight - 6 : rect.bottom + 6;
    const left = Math.min(
      Math.max(rect.left, RESET_NOTE_VIEWPORT_MARGIN),
      window.innerWidth - RESET_NOTE_PANEL_MAX_WIDTH - RESET_NOTE_VIEWPORT_MARGIN,
    );
    setPos({ top, left });
  }, []);

  useLayoutEffect(() => {
    if (!open) return;
    reposition();
  }, [open, reposition]);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: globalThis.MouseEvent) => {
      const target = e.target as Node;
      if (triggerRef.current?.contains(target)) return;
      if (panelRef.current?.contains(target)) return;
      close();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    window.addEventListener("scroll", reposition, true);
    window.addEventListener("resize", reposition);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("scroll", reposition, true);
      window.removeEventListener("resize", reposition);
    };
  }, [open, close, reposition]);

  if (rule.status !== "reset") return null;

  const message = resetMessage(rule);
  const isAlert = resetHazardTier(rule) === "contra_risk";
  const noteClassName = resetNoteClassName(rule);

  if (variant === "full") {
    return (
      <p
        className={`max-w-[46ch] rounded-md border px-2 py-1.5 text-hint leading-relaxed ${noteClassName}`}
        role={isAlert ? "alert" : undefined}
      >
        {message}
      </p>
    );
  }

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        title={message}
        aria-label={message}
        aria-expanded={open}
        aria-controls={panelId}
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
        }}
        className={`block max-w-[46ch] truncate rounded-md border px-2 py-1.5 text-left text-hint leading-relaxed ${noteClassName}`}
      >
        {message}
      </button>
      {open && typeof document !== "undefined"
        ? createPortal(
            <div
              ref={panelRef}
              id={panelId}
              role={isAlert ? "alert" : "dialog"}
              className={`fixed z-50 w-max max-w-[360px] rounded-md border px-2.5 py-2 text-left text-hint leading-relaxed shadow-pop ${noteClassName}`}
              style={{
                top: pos?.top ?? -9999,
                left: pos?.left ?? -9999,
                visibility: pos ? "visible" : "hidden",
              }}
            >
              {message}
            </div>,
            document.body,
          )
        : null}
    </>
  );
}

/**
 * The second line under a group title. A Reset shows its explanation instead of the
 * stop→target gauge — monitoring has stopped, so a gauge tracking MTM between thresholds
 * would be describing a rule that is no longer watching anything.
 */
function ExitRuleSummaryLine({
  rule,
  currentMtm,
  onOpenExitRuleModal,
  noteVariant = "compact",
}: {
  rule: SquareOffRuleRecord | null;
  currentMtm: number | null;
  onOpenExitRuleModal: (e: MouseEvent) => void;
  /**
   * "compact" truncates to one line with a click-to-expand panel — required in the desktop
   * grouped table, whose summary row has a fixed height it can't grow to fit a wrapped
   * multi-line note. "full" renders the complete message inline, wrapped — used on the
   * mobile card layout, which stacks rows in a free-flowing column with no such constraint.
   */
  noteVariant?: "compact" | "full";
}) {
  if (!rule) return <PnlTargetCta onClick={onOpenExitRuleModal} />;
  if (rule.status === "reset") return <ExitRuleResetNote rule={rule} variant={noteVariant} />;
  return <PnlTargetGauge rule={rule} currentMtm={currentMtm} />;
}

const gaugeLineClass =
  "inline-flex h-[18px] items-center gap-1.5 font-mono text-[10px] leading-none";

/**
 * Second line under a group's title, always present at the same height whether
 * or not a PB/SL rule is armed (see `tdSummaryShell`) — a mini stop→target
 * gauge with a dot marking where current MTM sits between the two thresholds.
 * `profit_target_pnl`/`loss_limit_pnl` are always positive magnitudes (see
 * SquareOffRuleModal), so the stop bound is the negation of `loss_limit_pnl`.
 */
function PnlTargetGauge({
  rule,
  currentMtm,
}: {
  rule: SquareOffRuleRecord;
  currentMtm: number | null;
}) {
  const target = Math.abs(rule.profit_target_pnl);
  const stopMagnitude = Math.abs(rule.loss_limit_pnl);
  const stop = -stopMagnitude;
  const span = target - stop;
  const pct =
    currentMtm != null && Number.isFinite(currentMtm) && span > 0
      ? Math.min(100, Math.max(0, ((currentMtm - stop) / span) * 100))
      : null;
  const current = formatSignedCompact(currentMtm);
  return (
    <span
      className={gaugeLineClass}
      title={`Profit Booking / Stop Loss — target +₹${target.toLocaleString("en-IN")}, stop −₹${stopMagnitude.toLocaleString("en-IN")}`}
    >
      <span className="text-down">
        −{formatIndianMoneyCompact(stopMagnitude, { shortSuffix: true })}
      </span>
      <span className="relative inline-block h-1 w-14 shrink-0 rounded-full bg-border 2xl:w-20">
        {pct != null ? (
          <span
            className="absolute top-1/2 size-2 -translate-x-1/2 -translate-y-1/2 rounded-full border border-panel2 bg-foreground"
            style={{ left: `${pct}%` }}
          />
        ) : null}
      </span>
      <span className="text-up">
        +{formatIndianMoneyCompact(target, { shortSuffix: true })}
      </span>
      <span className={`font-semibold ${current.className}`}>{current.text}</span>
    </span>
  );
}

/** Unarmed counterpart of `PnlTargetGauge` — same line height so summary rows stay uniform regardless of rule state. */
function PnlTargetCta({ onClick }: { onClick: (e: MouseEvent) => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`${gaugeLineClass} text-accent-strong opacity-80 transition hover:opacity-100 hover:underline`}
    >
      + Set Profit Booking / Stop Loss
    </button>
  );
}

/** Dummy visual hint (not its own click target — clicks bubble to the row's own expand toggle). */
function SeeActionsHint() {
  return (
    <span className="text-[11px] font-medium text-accent-strong opacity-80">
      See actions →
    </span>
  );
}

function GroupExpandedExtras({
  g,
  holderId,
  proposedLeg,
  squareOffRule,
  selectedCount,
  onOpenExitRuleModal,
  onSquareOffSelectedClick,
  onOpenHedgeModal,
}: {
  g: PortfolioPositionGroup;
  holderId: string;
  proposedLeg: StrategyLeg | null;
  squareOffRule: SquareOffRuleRecord | null;
  selectedCount: number;
  onOpenExitRuleModal: (e: MouseEvent) => void;
  onSquareOffSelectedClick: (e: MouseEvent) => void;
  onOpenHedgeModal: (e: MouseEvent) => void;
}) {
  return (
    <div className="border-t border-border-soft bg-panel2">
      <div className="grid min-w-0 divide-y divide-border-soft md:grid-cols-[3fr_2fr] md:divide-x md:divide-y-0">
        <PortfolioGroupPayoffPanel
          stockCode={g.stockCode}
          exchangeCode={g.exchangeCode}
          expiryDisplay={g.expiryDate}
          rows={g.rows}
          proposedLeg={proposedLeg}
          holderId={holderId}
        />
        <div className="flex min-w-0 items-center justify-center p-6">
          <div className="flex w-full max-w-[13rem] flex-col gap-3">
            <GroupPillButton
              variant="table"
              label={
                squareOffRule
                  ? "Edit Profit Booking / Stop Loss"
                  : "Profit Booking / Stop Loss"
              }
              onClick={onOpenExitRuleModal}
            />
            <GroupPillButton
              variant="table"
              label="Square Off Selected"
              onClick={onSquareOffSelectedClick}
              disabled={selectedCount === 0}
            />
            <GroupPillButton
              variant="table"
              label="Hedge"
              onClick={onOpenHedgeModal}
            />
          </div>
        </div>
      </div>
    </div>
  );
}

/**
 * Individual-leg counterpart of `GroupExpandedExtras` — no payoff panel (a single
 * naked leg's isolated payoff/breakevens aren't a very meaningful chart, and the
 * PoP number alone is already shown on the row), and Hedge is disabled since
 * `PortfolioHedgePanel` computes net delta/margin across a whole group's rows.
 */
function LegExpandedActions({
  onOpenExitRuleModal,
  onSquareOffClick,
}: {
  onOpenExitRuleModal: (e: MouseEvent) => void;
  onSquareOffClick: (e: MouseEvent) => void;
}) {
  return (
    <div className="border-t border-border-soft bg-panel2 p-6">
      <div className="flex w-full max-w-[13rem] flex-col gap-3">
        <GroupPillButton
          variant="table"
          label="Profit Booking / Stop Loss"
          onClick={onOpenExitRuleModal}
        />
        <GroupPillButton
          variant="table"
          label="Square Off"
          onClick={onSquareOffClick}
        />
        <GroupPillButton
          variant="table"
          label="Hedge"
          onClick={() => {}}
          disabled
          title="Hedge is only available in grouped view"
        />
      </div>
    </div>
  );
}

type GroupBlockProps = {
  g: PortfolioPositionGroup;
  isOpen: boolean;
  onToggle: () => void;
  holderId: string;
  proposedLeg: StrategyLeg | null;
  startNumber: number;
  onLiveChange: (groupKey: string, live: boolean) => void;
  squareOffRule: SquareOffRuleRecord | null;
  onOpenExitRuleModal: (e: MouseEvent) => void;
  selectedLegs: ReadonlySet<number>;
  onToggleLeg: (idx: number) => void;
  onToggleGroupAll: () => void;
  onSquareOffSelectedClick: (e: MouseEvent) => void;
  onOpenHedgeModal: (e: MouseEvent) => void;
};

/**
 * One group's rows (desktop table). Owns the live-chain overlay hook so it's only
 * fetched/subscribed while `isOpen` — collapsed groups render straight from the
 * polled `/portfolio/data` snapshot passed in via `g.rows`. PoP, however, is fetched
 * unconditionally (see `useGroupPoP`) so the summary row can show it even collapsed.
 */
function PortfolioGroupTableBlock({
  g,
  isOpen,
  onToggle,
  holderId,
  proposedLeg,
  startNumber,
  onLiveChange,
  squareOffRule,
  onOpenExitRuleModal,
  selectedLegs,
  onToggleLeg,
  onToggleGroupAll,
  onSquareOffSelectedClick,
  onOpenHedgeModal,
}: GroupBlockProps) {
  const { rows: liveRows, isLive } = useGroupLiveOverlay(g, holderId);
  const pop = useGroupPoP(g);
  useEffect(() => {
    onLiveChange(g.key, isLive);
  }, [g.key, isLive, onLiveChange]);
  const spotAgg = formatSpot(liveRows[0]?.spot_price);
  const mtmSum = sumNumericField(liveRows, "current_profit");
  const carrySum = sumNumericField(liveRows, "carry_profit");
  // SPAN + ELM (and its carry-return) are the server's netted group figure — one
  // multi-leg margin call for the whole group — not a per-leg sum. They don't tick
  // with live quotes (margin is composition-based), so they come from `g.netted`,
  // not the live overlay, while MTM/Carry rupees stay live off `liveRows`.
  const gSpan = g.netted?.span_margin_required ?? null;
  const gElm = g.netted?.elm_margin_required ?? null;
  const gMtm = formatMtmCarry(mtmSum);
  const gCarry = formatMtmCarry(carrySum);
  const gCr = formatCarryRet(g.netted?.carry_margin_returns ?? null);
  const groupTitle = `${g.stockCode} · ${g.expiryDate} · ${g.rows.length} leg${g.rows.length === 1 ? "" : "s"}`;
  const allSelected = selectedLegs.size > 0 && selectedLegs.size === g.rows.length;
  const someSelected = selectedLegs.size > 0 && !allSelected;

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
        <td
          className={`${tdSummaryBase} text-center align-middle`}
          onClick={(e) => e.stopPropagation()}
        >
          <Checkbox
            checked={allSelected}
            indeterminate={someSelected}
            onChange={onToggleGroupAll}
            aria-label={`Select all legs in ${groupTitle}`}
          />
        </td>
        <td className={`${tdSummaryBase} text-center tabular-nums text-muted`}>
          {isOpen ? "▼" : "▶"}
        </td>
        <td className={`${tdSummaryBase} font-medium`}>
          <div className="flex flex-col gap-1">
            <span className="inline-flex items-center gap-1.5">
              {groupTitle}
              {isLive ? <LiveDot title="Live · WebSocket" /> : null}
              {squareOffRule ? (
                <ExitRuleBadge rule={squareOffRule} />
              ) : null}
            </span>
            <ExitRuleSummaryLine
              rule={squareOffRule}
              currentMtm={mtmSum}
              onOpenExitRuleModal={onOpenExitRuleModal}
            />
          </div>
        </td>
        <td className={tdSummaryBase}>—</td>
        <td className={tdSummaryBase}>—</td>
        <td className={`${tdSummaryBase} text-right`}>—</td>
        <td className={`${tdSummaryBase} text-right`}>—</td>
        <td className={`${tdSummaryBase} text-right`}>—</td>
        <td className={`${tdSummaryBase} text-right font-mono tabular-nums ${spotAgg.className}`}>
          {spotAgg.text}
        </td>
        <td className={`${tdSummaryShell} text-right font-mono tabular-nums font-medium ${gMtm.className}`}>
          {gMtm.text}
        </td>
        <td className={`${tdSummaryShell} text-right font-mono tabular-nums`}>
          <span className="inline-flex flex-col items-end leading-tight">
            <span className={`font-medium ${gCarry.className}`}>{gCarry.text}</span>
            <span className={`text-[11px] font-normal ${demoteWeight(gCr.className)}`}>
              {gCr.text}
            </span>
          </span>
        </td>
        <td className={`${tdSummaryBase} text-right font-mono tabular-nums`}>
          <SpanElmCell span={gSpan} elm={gElm} />
        </td>
        <td className={`${tdSummaryBase} text-right font-mono tabular-nums`}>
          {formatPoP(pop)}
        </td>
        <td className={`${tdSummaryBase} text-right align-middle`}>
          {!isOpen ? <SeeActionsHint /> : null}
        </td>
      </tr>
      {isOpen
        ? liveRows.map((row, localIdx) => {
            const rowKey = childRowKey(g.key, localIdx);
            const mtm = formatMtmCarry(row.current_profit);
            const carry = formatMtmCarry(row.carry_profit);
            const spot = formatSpot(row.spot_price);
            const qty = coerceNum(row.quantity);
            const num = startNumber + localIdx + 1;
            const label = formatOptionSymbol(row);
            return (
              <tr key={rowKey} className="app-table-row bg-panel2">
                <td className={`${tdBase} text-center align-middle`}>
                  <Checkbox
                    checked={selectedLegs.has(localIdx)}
                    onChange={() => onToggleLeg(localIdx)}
                    aria-label={`Select ${label}`}
                  />
                </td>
                <td className={`${tdBase} text-center font-mono tabular-nums`}>{num}</td>
                <td className={tdBase}>{label}</td>
                <td className={tdBase}>
                  <OptionTypeBadge right={row.right} />
                </td>
                <td className={tdBase}>
                  <OrderSideBadge side={row.action} />
                </td>
                <td className={`${tdBase} text-right font-mono tabular-nums`}>
                  {qty != null
                    ? qty.toLocaleString("en-IN", {
                        maximumFractionDigits: Number.isInteger(qty) ? 0 : 4,
                      })
                    : "—"}
                </td>
                <td className={`${tdBase} text-right font-mono tabular-nums`}>
                  {formatPriceCell(row.average_price)}
                </td>
                <td className={`${tdBase} text-right font-mono tabular-nums`}>
                  <LtpCellContent row={row} />
                </td>
                <td className={`${tdBase} text-right font-mono tabular-nums ${spot.className}`}>
                  {spot.text}
                </td>
                <td className={`${tdShell} text-right font-mono tabular-nums font-medium ${mtm.className}`}>
                  {mtm.text}
                </td>
                <td className={`${tdShell} text-right font-mono tabular-nums font-medium ${carry.className}`}>
                  {carry.text}
                </td>
                <td className={`${tdBase} text-right font-mono tabular-nums app-text-muted`}>
                  —
                </td>
                <td className={`${tdBase} text-right font-mono tabular-nums app-text-muted`}>
                  —
                </td>
                <td className={tdBase} />
              </tr>
            );
          })
        : null}
      {isOpen ? (
        <tr className="app-table-row">
          {/*
            w-px + min-w-full: table-auto sizes columns from each cell's natural
            content width, and the hedge-active grid's own natural width (payoff
            chart + candidates panel side by side) is wider than the table's other
            rows need — without this, that grid drags the whole table wider than
            its wrapper, pushing the candidates panel off-screen behind horizontal
            scroll. w-px tells the auto-layout pass "ignore my content, I only
            need 1px" (so column widths are driven by the real data rows instead);
            min-w-full then re-expands this cell to whatever width the table
            actually settles on, once that circular sizing dependency is broken.
          */}
          <td className="w-px min-w-full p-0 align-top" colSpan={TABLE_COL_COUNT}>
            <GroupExpandedExtras
              g={g}
              holderId={holderId}
              proposedLeg={proposedLeg}
              squareOffRule={squareOffRule}
              selectedCount={selectedLegs.size}
              onOpenExitRuleModal={onOpenExitRuleModal}
              onSquareOffSelectedClick={onSquareOffSelectedClick}
              onOpenHedgeModal={onOpenHedgeModal}
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
  proposedLeg,
  onLiveChange,
  squareOffRule,
  onOpenExitRuleModal,
  selectedLegs,
  onToggleLeg,
  onToggleGroupAll,
  onSquareOffSelectedClick,
  onOpenHedgeModal,
}: GroupBlockProps) {
  const { rows: liveRows, isLive } = useGroupLiveOverlay(g, holderId);
  const pop = useGroupPoP(g);
  useEffect(() => {
    onLiveChange(g.key, isLive);
  }, [g.key, isLive, onLiveChange]);
  const spotAgg = formatSpot(liveRows[0]?.spot_price);
  const mtmSum = sumNumericField(liveRows, "current_profit");
  const carrySum = sumNumericField(liveRows, "carry_profit");
  // SPAN + ELM (and its carry-return) are the server's netted group figure — one
  // multi-leg margin call for the whole group — not a per-leg sum. They don't tick
  // with live quotes (margin is composition-based), so they come from `g.netted`,
  // not the live overlay, while MTM/Carry rupees stay live off `liveRows`.
  const gSpan = g.netted?.span_margin_required ?? null;
  const gElm = g.netted?.elm_margin_required ?? null;
  const gMtm = formatMtmCarry(mtmSum);
  const gCarry = formatMtmCarry(carrySum);
  const gCr = formatCarryRet(g.netted?.carry_margin_returns ?? null);
  const groupTitle = `${g.stockCode} · ${g.expiryDate} · ${g.rows.length} leg${g.rows.length === 1 ? "" : "s"}`;
  const allSelected = selectedLegs.size > 0 && selectedLegs.size === g.rows.length;
  const someSelected = selectedLegs.size > 0 && !allSelected;

  return (
    <div className="app-card-muted overflow-hidden text-sm">
      <div className="flex items-start gap-3 p-4 sm:p-5">
        <div
          className="mt-0.5 shrink-0"
          onClick={(e) => e.stopPropagation()}
        >
          <Checkbox
            checked={allSelected}
            indeterminate={someSelected}
            onChange={onToggleGroupAll}
            aria-label={`Select all legs in ${groupTitle}`}
          />
        </div>
        <button
          type="button"
          aria-expanded={isOpen}
          className="mt-0.5 shrink-0 tabular-nums text-muted"
          onClick={onToggle}
        >
          {isOpen ? "▼" : "▶"}
        </button>
        <div
          role="button"
          tabIndex={0}
          aria-expanded={isOpen}
          className="min-w-0 flex-1 cursor-pointer space-y-2 text-left"
          onClick={onToggle}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              onToggle();
            }
          }}
        >
          <h3 className="flex flex-wrap items-center gap-1.5 text-base font-semibold leading-snug text-foreground">
            {groupTitle}
            {isLive ? <LiveDot title="Live · WebSocket" /> : null}
            {squareOffRule ? (
              <ExitRuleBadge rule={squareOffRule} />
            ) : null}
          </h3>
          <ExitRuleSummaryLine
            rule={squareOffRule}
            currentMtm={mtmSum}
            onOpenExitRuleModal={onOpenExitRuleModal}
            noteVariant="full"
          />
          <p>
            <span className="app-text-muted">Spot:</span>{" "}
            <span className={spotAgg.className}>{spotAgg.text}</span>
          </p>
          <p>
            <span className="app-text-muted">MTM (sum):</span>{" "}
            <span className={`font-mono tabular-nums font-medium ${gMtm.className}`}>{gMtm.text}</span>
          </p>
          <p>
            <span className="app-text-muted">Carry (sum):</span>{" "}
            <span className={`font-mono tabular-nums font-medium ${gCarry.className}`}>{gCarry.text}</span>{" "}
            <span className={`font-mono tabular-nums text-xs font-normal ${demoteWeight(gCr.className)}`}>
              {gCr.text}
            </span>
          </p>
          <p>
            <span className="app-text-muted">Span + ELM:</span>{" "}
            <span className="font-mono tabular-nums">
              {formatSpanElm(gSpan)}{" "}
              <span className="text-xs font-normal app-text-muted">
                + {formatSpanElm(gElm)}
              </span>
            </span>
          </p>
          <p>
            <span className="app-text-muted">PoP:</span>{" "}
            <span className="font-mono tabular-nums">{formatPoP(pop)}</span>
          </p>
          {!isOpen ? (
            <p>
              <SeeActionsHint />
            </p>
          ) : null}
        </div>
      </div>
      {isOpen ? (
        <div className="border-t border-border-soft">
          <div className="space-y-3 p-4 sm:space-y-4 sm:p-5">
            {liveRows.map((row, localIdx) => {
              const rowKey = childRowKey(g.key, localIdx);
              const mtm = formatMtmCarry(row.current_profit);
              const carryTitle = formatMtmCarry(row.carry_profit);
              const spot = formatSpot(row.spot_price);
              const qty = coerceNum(row.quantity);
              const label = formatOptionSymbol(row);
              return (
                <div
                  key={`card-${rowKey}`}
                  className="space-y-2.5 rounded-md border border-border bg-panel2 p-4"
                >
                  <div className="flex items-center gap-2">
                    <Checkbox
                      checked={selectedLegs.has(localIdx)}
                      onChange={() => onToggleLeg(localIdx)}
                      aria-label={`Select ${label}`}
                    />
                    <h4 className="text-sm font-semibold leading-snug text-foreground">
                      {label}
                    </h4>
                  </div>
                  <p className="flex items-center gap-2">
                    <OptionTypeBadge right={row.right} />
                    <OrderSideBadge side={row.action} />
                  </p>
                  <p>
                    <span className="app-text-muted">Qty:</span>{" "}
                    <span className="font-mono tabular-nums">
                      {qty != null
                        ? qty.toLocaleString("en-IN", {
                            maximumFractionDigits: Number.isInteger(qty) ? 0 : 4,
                          })
                        : "—"}
                    </span>
                  </p>
                  <p>
                    <span className="app-text-muted">Avg Price:</span>{" "}
                    <span className="font-mono tabular-nums">
                      {formatPriceCell(row.average_price)}
                    </span>
                  </p>
                  <p>
                    <span className="app-text-muted">LTP:</span>{" "}
                    <span className="font-mono tabular-nums">
                      <LtpCellContent row={row} />
                    </span>
                  </p>
                  <p>
                    <span className="app-text-muted">Spot:</span>{" "}
                    <span className={spot.className}>{spot.text}</span>
                  </p>
                  <p>
                    <span className="app-text-muted">MTM:</span>{" "}
                    <span className={`font-mono tabular-nums font-medium ${mtm.className}`}>{mtm.text}</span>
                  </p>
                  <p>
                    <span className="app-text-muted">Carry:</span>{" "}
                    <span className={`font-mono tabular-nums font-medium ${carryTitle.className}`}>
                      {carryTitle.text}
                    </span>
                  </p>
                </div>
              );
            })}
          </div>
          <GroupExpandedExtras
            g={g}
            holderId={holderId}
            proposedLeg={proposedLeg}
            squareOffRule={squareOffRule}
            selectedCount={selectedLegs.size}
            onOpenExitRuleModal={onOpenExitRuleModal}
            onSquareOffSelectedClick={onSquareOffSelectedClick}
            onOpenHedgeModal={onOpenHedgeModal}
          />
        </div>
      ) : null}
    </div>
  );
}

type LegBlockProps = {
  row: PortfolioPositionRecord;
  isOpen: boolean;
  onToggle: () => void;
  rowNumber: number;
  onOpenExitRuleModal: (e: MouseEvent) => void;
  onSquareOffClick: (e: MouseEvent) => void;
};

/**
 * One leg's own row (desktop table), individual-legs view. No live-chain overlay
 * or WS subscription — PoP is a one-shot REST fetch that dedupes with sibling
 * legs' (and the grouped view's) fetch for the same scrip+expiry, same as a
 * collapsed group's PoP column.
 */
function PortfolioLegTableBlock({
  row,
  isOpen,
  onToggle,
  rowNumber,
  onOpenExitRuleModal,
  onSquareOffClick,
}: LegBlockProps) {
  const stockCode = String(row.stock_code ?? "");
  const expiryDate = String(row.expiry_date ?? "");
  const exchangeCode = String(row.exchange_code ?? "NFO");
  const pop = useLegPoP(row, stockCode, expiryDate, exchangeCode);
  const mtm = formatMtmCarry(row.current_profit);
  const carry = formatMtmCarry(row.carry_profit);
  const cr = formatCarryRet(coerceNum(row.carry_margin_returns));
  const spot = formatSpot(row.spot_price);
  const qty = coerceNum(row.quantity);
  const label = formatOptionSymbol(row);

  return (
    <Fragment>
      <tr
        role="button"
        tabIndex={0}
        aria-expanded={isOpen}
        title={isOpen ? "Collapse" : "Expand"}
        className="app-table-row cursor-pointer select-none hover:bg-panel2"
        onClick={onToggle}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onToggle();
          }
        }}
      >
        <td className={`${tdBase} text-center font-mono tabular-nums`}>
          {rowNumber}
        </td>
        <td className={`${tdBase} font-medium`}>{label}</td>
        <td className={tdBase}>
          <OptionTypeBadge right={row.right} />
        </td>
        <td className={tdBase}>
          <OrderSideBadge side={row.action} />
        </td>
        <td className={`${tdBase} text-right font-mono tabular-nums`}>
          {qty != null
            ? qty.toLocaleString("en-IN", {
                maximumFractionDigits: Number.isInteger(qty) ? 0 : 4,
              })
            : "—"}
        </td>
        <td className={`${tdBase} text-right font-mono tabular-nums`}>
          {formatPriceCell(row.average_price)}
        </td>
        <td className={`${tdBase} text-right font-mono tabular-nums`}>
          <LtpCellContent row={row} />
        </td>
        <td className={`${tdBase} text-right font-mono tabular-nums ${spot.className}`}>
          {spot.text}
        </td>
        <td className={`${tdShell} text-right font-mono tabular-nums font-medium ${mtm.className}`}>
          {mtm.text}
        </td>
        <td className={`${tdShell} text-right font-mono tabular-nums`}>
          <span className="inline-flex flex-col items-end leading-tight">
            <span className={`font-medium ${carry.className}`}>{carry.text}</span>
            <span className={`text-[11px] font-normal ${demoteWeight(cr.className)}`}>
              {cr.text}
            </span>
          </span>
        </td>
        <td className={`${tdBase} text-right font-mono tabular-nums app-text-muted`}>
          {/* SPAN is netted at group/portfolio level — never attributable to one leg. */}
          —
        </td>
        <td className={`${tdBase} text-right font-mono tabular-nums`}>
          {formatPoP(pop)}
        </td>
        <td className={`${tdBase} text-right align-middle`}>
          {!isOpen ? <SeeActionsHint /> : null}
        </td>
      </tr>
      {isOpen ? (
        <tr className="app-table-row">
          <td className="w-px min-w-full p-0 align-top" colSpan={LEG_TABLE_COL_COUNT}>
            <LegExpandedActions
              onOpenExitRuleModal={onOpenExitRuleModal}
              onSquareOffClick={onSquareOffClick}
            />
          </td>
        </tr>
      ) : null}
    </Fragment>
  );
}

/** One leg's own card (mobile), individual-legs view. */
function PortfolioLegCardBlock({
  row,
  isOpen,
  onToggle,
  onOpenExitRuleModal,
  onSquareOffClick,
}: LegBlockProps) {
  const stockCode = String(row.stock_code ?? "");
  const expiryDate = String(row.expiry_date ?? "");
  const exchangeCode = String(row.exchange_code ?? "NFO");
  const pop = useLegPoP(row, stockCode, expiryDate, exchangeCode);
  const mtm = formatMtmCarry(row.current_profit);
  const carry = formatMtmCarry(row.carry_profit);
  const cr = formatCarryRet(coerceNum(row.carry_margin_returns));
  const spot = formatSpot(row.spot_price);
  const qty = coerceNum(row.quantity);
  const label = formatOptionSymbol(row);

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
        <div
          role="button"
          tabIndex={0}
          aria-expanded={isOpen}
          className="min-w-0 flex-1 cursor-pointer space-y-2 text-left"
          onClick={onToggle}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              onToggle();
            }
          }}
        >
          <h3 className="flex flex-wrap items-center gap-1.5 text-base font-semibold leading-snug text-foreground">
            {label}
          </h3>
          <p className="flex items-center gap-2">
            <OptionTypeBadge right={row.right} />
            <OrderSideBadge side={row.action} />
          </p>
          <p>
            <span className="app-text-muted">Qty:</span>{" "}
            <span className="font-mono tabular-nums">
              {qty != null
                ? qty.toLocaleString("en-IN", {
                    maximumFractionDigits: Number.isInteger(qty) ? 0 : 4,
                  })
                : "—"}
            </span>
          </p>
          <p>
            <span className="app-text-muted">Avg Price:</span>{" "}
            <span className="font-mono tabular-nums">
              {formatPriceCell(row.average_price)}
            </span>
          </p>
          <p>
            <span className="app-text-muted">LTP:</span>{" "}
            <span className="font-mono tabular-nums">
              <LtpCellContent row={row} />
            </span>
          </p>
          <p>
            <span className="app-text-muted">Spot:</span>{" "}
            <span className={spot.className}>{spot.text}</span>
          </p>
          <p>
            <span className="app-text-muted">MTM:</span>{" "}
            <span className={`font-mono tabular-nums font-medium ${mtm.className}`}>{mtm.text}</span>
          </p>
          <p>
            <span className="app-text-muted">Carry:</span>{" "}
            <span className={`font-mono tabular-nums font-medium ${carry.className}`}>{carry.text}</span>{" "}
            <span className={`font-mono tabular-nums text-xs font-normal ${demoteWeight(cr.className)}`}>
              {cr.text}
            </span>
          </p>
          <p>
            {/* SPAN is netted at group/portfolio level — never attributable to one leg. */}
            <span className="app-text-muted">Span + ELM:</span>{" "}
            <span className="font-mono tabular-nums app-text-muted">—</span>
          </p>
          <p>
            <span className="app-text-muted">PoP:</span>{" "}
            <span className="font-mono tabular-nums">{formatPoP(pop)}</span>
          </p>
          {!isOpen ? (
            <p>
              <SeeActionsHint />
            </p>
          ) : null}
        </div>
      </div>
      {isOpen ? (
        <LegExpandedActions
          onOpenExitRuleModal={onOpenExitRuleModal}
          onSquareOffClick={onSquareOffClick}
        />
      ) : null}
    </div>
  );
}

export function OpenPositionsTable({
  positions,
  nettedGroups,
  emptyMessage = "No positions to display",
  defaultExpandedGroupKey = null,
  onLiveGroupCountChange,
  viewMode = "grouped",
}: OpenPositionsTableProps) {
  const groups = useMemo(() => {
    const built = buildPortfolioPositionGroups(positions);
    const netted = nettedMarginByKey(nettedGroups);
    return built.map((g) => ({ ...g, netted: netted.get(g.key) ?? null }));
  }, [positions, nettedGroups]);
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(
    () => new Set(defaultExpandedGroupKey ? [defaultExpandedGroupKey] : []),
  );
  const [expandedLegKeys, setExpandedLegKeys] = useState<Set<string>>(
    () => new Set(),
  );
  const [legExitRuleTarget, setLegExitRuleTarget] = useState<{
    leg: LegExitRuleTarget;
    label: string;
  } | null>(null);
  const [selectedLegsByGroup, setSelectedLegsByGroup] = useState<
    Map<string, Set<number>>
  >(() => new Map());
  const [hedgeGroupKey, setHedgeGroupKey] = useState<string | null>(null);
  const [hedgeModalOpen, setHedgeModalOpen] = useState(false);
  const [selectedCandidate, setSelectedCandidate] =
    useState<StrategyHedgeCandidate | null>(null);
  const [hedgeLotSize, setHedgeLotSize] = useState(1);
  const [executeOpen, setExecuteOpen] = useState(false);
  const [squareOffRows, setSquareOffRows] = useState<
    PortfolioPositionRecord[] | null
  >(null);
  const [exitRuleModalGroupKey, setExitRuleModalGroupKey] = useState<
    string | null
  >(null);
  const squareOffRulesByGroup = useSquareOffRulesByGroup();
  const invalidateSquareOffRules = useInvalidateSquareOffRules();
  const { getHolderId, releaseStaleGroups } = useGroupSubscriptionHolders();
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

  const getSelectedLegs = useCallback(
    (groupKey: string): ReadonlySet<number> =>
      selectedLegsByGroup.get(groupKey) ?? EMPTY_SELECTION,
    [selectedLegsByGroup],
  );

  const toggleLeg = useCallback((groupKey: string, idx: number) => {
    setSelectedLegsByGroup((prev) => {
      const next = new Map(prev);
      const cur = new Set(next.get(groupKey) ?? []);
      if (cur.has(idx)) cur.delete(idx);
      else cur.add(idx);
      next.set(groupKey, cur);
      return next;
    });
  }, []);

  const toggleGroupAll = useCallback((groupKey: string, rowCount: number) => {
    setSelectedLegsByGroup((prev) => {
      const next = new Map(prev);
      const cur = next.get(groupKey) ?? new Set<number>();
      const allSelected = cur.size === rowCount && rowCount > 0;
      next.set(
        groupKey,
        allSelected
          ? new Set()
          : new Set(Array.from({ length: rowCount }, (_, i) => i)),
      );
      return next;
    });
  }, []);

  const clearGroupSelection = useCallback((groupKey: string) => {
    setSelectedLegsByGroup((prev) => {
      if (!prev.has(groupKey)) return prev;
      const next = new Map(prev);
      next.delete(groupKey);
      return next;
    });
  }, []);

  const hedgeActiveGroup = useMemo(
    () => groups.find((g) => g.key === hedgeGroupKey) ?? null,
    [groups, hedgeGroupKey],
  );

  const proposedLegForActive = useMemo(() => {
    if (!selectedCandidate || hedgeLotSize <= 0) return null;
    return candidateToStrategyLeg(selectedCandidate, hedgeLotSize);
  }, [selectedCandidate, hedgeLotSize]);

  const executeLegs: ExecutionPreviewLeg[] = useMemo(
    () => (selectedCandidate ? [candidateToExecutionLeg(selectedCandidate)] : []),
    [selectedCandidate],
  );

  const clearHedgeState = useCallback(() => {
    setHedgeGroupKey(null);
    setHedgeModalOpen(false);
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
          clearGroupSelection(key);
          if (hedgeGroupKey === key) {
            clearHedgeState();
          }
        } else {
          next.add(key);
        }
        return next;
      });
    },
    [hedgeGroupKey, clearGroupSelection, clearHedgeState],
  );

  // WS holders are registered per open-position group regardless of expand
  // state (collapsed groups get live LTP too), so they're only released once
  // the position itself closes and drops out of `groups` — not on collapse.
  useEffect(() => {
    const liveKeys = new Set(groups.map((g) => g.key));
    releaseStaleGroups(liveKeys);
  }, [groups, releaseStaleGroups]);

  const openHedgeModalForGroup = useCallback(
    (key: string) => {
      if (hedgeGroupKey !== key) {
        setSelectedCandidate(null);
        setHedgeLotSize(1);
      }
      setHedgeGroupKey(key);
      setExecuteOpen(false);
      setHedgeModalOpen(true);
    },
    [hedgeGroupKey],
  );

  const handleCloseHedgeModal = useCallback(() => {
    setHedgeModalOpen(false);
  }, []);

  const handleSquareOffSelectedClick = useCallback(
    (e: MouseEvent, groupKey: string, rows: PortfolioPositionRecord[]) => {
      e.stopPropagation();
      const selected = selectedLegsByGroup.get(groupKey);
      if (!selected || selected.size === 0) return;
      const filteredRows = rows.filter((_, idx) => selected.has(idx));
      if (!filteredRows.length) return;
      setSquareOffRows(filteredRows);
    },
    [selectedLegsByGroup],
  );

  const handleOpenExitRuleModal = useCallback((e: MouseEvent, groupKey: string) => {
    e.stopPropagation();
    setExitRuleModalGroupKey(groupKey);
  }, []);

  const exitRuleModalGroup = useMemo(
    () => groups.find((g) => g.key === exitRuleModalGroupKey) ?? null,
    [groups, exitRuleModalGroupKey],
  );

  const toggleLegExpand = useCallback((key: string) => {
    setExpandedLegKeys((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  const handleOpenLegExitRuleModal = useCallback(
    (e: MouseEvent, row: PortfolioPositionRecord) => {
      e.stopPropagation();
      setLegExitRuleTarget({
        leg: legToExitRuleTarget(row),
        label: formatOptionSymbol(row),
      });
    },
    [],
  );

  const handleSquareOffLegClick = useCallback(
    (e: MouseEvent, row: PortfolioPositionRecord) => {
      e.stopPropagation();
      setSquareOffRows([row]);
    },
    [],
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
      {viewMode === "grouped" ? (
        <div className="hidden min-w-0 max-w-full xl:block">
          <div className="app-table-wrap w-full min-w-0 max-w-full rounded-none border-0 bg-transparent dark:bg-transparent">
            <table className="w-full min-w-max table-auto border-collapse text-left">
              <thead>
                <tr className="border-b border-border bg-panel2">
                  <th className={`${thBase} w-10 text-center`}>
                    <span className="sr-only">Select</span>
                  </th>
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
                  <th
                    className={`${thBase} text-right`}
                    title="Carry Ret. (annualised %, group level only): (Carry ÷ DTE) × 365 ÷ (Span + ELM) × 100"
                  >
                    Carry
                  </th>
                  <th
                    className={`${thBase} text-right`}
                    title="Span Margin + ELM at 2% — shown at group level only"
                  >
                    <span className="inline-flex flex-col items-end leading-tight">
                      <span>Span</span>
                      <span className="text-[10px] font-normal app-text-muted">
                        + ELM
                      </span>
                    </span>
                  </th>
                  <th
                    className={`${thBase} text-right`}
                    title="Probability of profit at expiry — group level only"
                  >
                    PoP
                  </th>
                  <th className={`${thBase} text-right whitespace-nowrap`}></th>
                </tr>
              </thead>
              <tbody>
                {positions.length === 0 ? (
                  <tr>
                    <td
                      colSpan={TABLE_COL_COUNT}
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
                      proposedLeg={hedgeGroupKey === g.key ? proposedLegForActive : null}
                      startNumber={groupStartNumbers[gi]}
                      onLiveChange={handleLiveChange}
                      squareOffRule={squareOffRulesByGroup.get(squareOffRuleGroupKey(g.stockCode, g.expiryDate)) ?? null}
                      onOpenExitRuleModal={(e) => handleOpenExitRuleModal(e, g.key)}
                      selectedLegs={getSelectedLegs(g.key)}
                      onToggleLeg={(idx) => toggleLeg(g.key, idx)}
                      onToggleGroupAll={() => toggleGroupAll(g.key, g.rows.length)}
                      onSquareOffSelectedClick={(e) =>
                        handleSquareOffSelectedClick(e, g.key, g.rows)
                      }
                      onOpenHedgeModal={(e) => {
                        e.stopPropagation();
                        openHedgeModalForGroup(g.key);
                      }}
                    />
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      ) : (
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
                  <th className={`${thBase} text-right`} title="Carry Ret. (annualised %): (Carry ÷ DTE) × 365 ÷ (Span + ELM) × 100">
                    Carry
                  </th>
                  <th className={`${thBase} text-right`} title="Span Margin + ELM at 2%">
                    <span className="inline-flex flex-col items-end leading-tight">
                      <span>Span</span>
                      <span className="text-[10px] font-normal app-text-muted">
                        + ELM
                      </span>
                    </span>
                  </th>
                  <th
                    className={`${thBase} text-right`}
                    title="Probability of profit at expiry for this single leg — no payoff chart in this view"
                  >
                    PoP
                  </th>
                  <th className={`${thBase} text-right whitespace-nowrap`}></th>
                </tr>
              </thead>
              <tbody>
                {positions.length === 0 ? (
                  <tr>
                    <td
                      colSpan={LEG_TABLE_COL_COUNT}
                      className="px-3 py-8 text-center text-sm app-text-muted"
                    >
                      {emptyMessage}
                    </td>
                  </tr>
                ) : (
                  positions.map((row, idx) => {
                    const key = legIdentityKey(row);
                    return (
                      <PortfolioLegTableBlock
                        key={key}
                        row={row}
                        isOpen={expandedLegKeys.has(key)}
                        onToggle={() => toggleLegExpand(key)}
                        rowNumber={idx + 1}
                        onOpenExitRuleModal={(e) => handleOpenLegExitRuleModal(e, row)}
                        onSquareOffClick={(e) => handleSquareOffLegClick(e, row)}
                      />
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {viewMode === "grouped" ? (
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
                proposedLeg={hedgeGroupKey === g.key ? proposedLegForActive : null}
                startNumber={0}
                onLiveChange={handleLiveChange}
                squareOffRule={squareOffRulesByGroup.get(squareOffRuleGroupKey(g.stockCode, g.expiryDate)) ?? null}
                onOpenExitRuleModal={(e) => handleOpenExitRuleModal(e, g.key)}
                selectedLegs={getSelectedLegs(g.key)}
                onToggleLeg={(idx) => toggleLeg(g.key, idx)}
                onToggleGroupAll={() => toggleGroupAll(g.key, g.rows.length)}
                onSquareOffSelectedClick={(e) =>
                  handleSquareOffSelectedClick(e, g.key, g.rows)
                }
                onOpenHedgeModal={(e) => {
                  e.stopPropagation();
                  openHedgeModalForGroup(g.key);
                }}
              />
            ))
          )}
        </div>
      ) : (
        <div className="space-y-3 xl:hidden">
          {positions.length === 0 ? (
            <p className="py-6 text-center text-base app-text-muted">
              {emptyMessage}
            </p>
          ) : (
            positions.map((row) => {
              const key = legIdentityKey(row);
              return (
                <PortfolioLegCardBlock
                  key={`card-leg-${key}`}
                  row={row}
                  isOpen={expandedLegKeys.has(key)}
                  onToggle={() => toggleLegExpand(key)}
                  rowNumber={0}
                  onOpenExitRuleModal={(e) => handleOpenLegExitRuleModal(e, row)}
                  onSquareOffClick={(e) => handleSquareOffLegClick(e, row)}
                />
              );
            })
          )}
        </div>
      )}

      <HedgeCandidatesModal
        group={hedgeActiveGroup}
        open={hedgeModalOpen}
        onClose={handleCloseHedgeModal}
        selectedCandidate={selectedCandidate}
        onSelectCandidate={setSelectedCandidate}
        onExecute={handleExecuteHedge}
        onLotSizeChange={setHedgeLotSize}
      />

      {hedgeActiveGroup && selectedCandidate ? (
        <OrderExecutionConfirmDialog
          open={executeOpen}
          onClose={handleExecuteClose}
          stockCode={hedgeActiveGroup.stockCode}
          exchangeCode={hedgeActiveGroup.exchangeCode}
          expiryDisplay={hedgeActiveGroup.expiryDate}
          legs={executeLegs}
        />
      ) : null}

      <SquareOffLegsModal
        rows={squareOffRows ?? []}
        open={squareOffRows != null}
        onClose={() => setSquareOffRows(null)}
      />

      {exitRuleModalGroup ? (
        <SquareOffRuleModal
          open={exitRuleModalGroup != null}
          onClose={() => setExitRuleModalGroupKey(null)}
          stockCode={exitRuleModalGroup.stockCode}
          expiryDisplay={exitRuleModalGroup.expiryDate}
          exchangeCode={exitRuleModalGroup.exchangeCode}
          currentPnl={sumNumericField(exitRuleModalGroup.rows, "current_profit")}
          existingRule={
            squareOffRulesByGroup.get(
              squareOffRuleGroupKey(exitRuleModalGroup.stockCode, exitRuleModalGroup.expiryDate),
            ) ?? null
          }
          onArmed={invalidateSquareOffRules}
          onDisarmed={invalidateSquareOffRules}
        />
      ) : null}

      {legExitRuleTarget ? (
        <LegExitRuleModal
          open={legExitRuleTarget != null}
          onClose={() => setLegExitRuleTarget(null)}
          leg={legExitRuleTarget.leg}
          label={legExitRuleTarget.label}
        />
      ) : null}
    </>
  );
}
