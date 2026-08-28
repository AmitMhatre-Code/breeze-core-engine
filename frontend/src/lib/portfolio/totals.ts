import type { PortfolioPositionRecord } from "@/lib/portfolio";
import type {
  NettedMargin,
  PortfolioPositionGroup,
} from "@/lib/portfolio/groupPositions";
import type { GroupLiveTotal } from "@/lib/portfolio/liveGroupTotals";

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

export type PortfolioTotals = {
  totalMtm: number | null;
  totalCarry: number | null;
  /** Netted Span + ELM across the whole portfolio (per-underlying netted, summed). */
  totalMargin: number | null;
  /** Netted SPAN only — matches what ICICI actually blocks. */
  spanMargin: number | null;
  /** ELM overlay (2% notional on index shorts) — additive, our conservative
   * buffer that ICICI does not reserve. Null when SPAN itself is unavailable. */
  elmMargin: number | null;
  /** Annualised carry return on the netted portfolio margin. */
  carryReturnPct: number | null;
  groupCount: number;
  legCount: number;
};

/**
 * Rolls the page-level summary tiles. MTM and Carry are additive, so they stay
 * per-leg sums. Margin and Carry-Return come from the server's netted portfolio
 * figure (`Success.portfolio`, netted per underlying then summed) — SPAN is a
 * portfolio risk model and cannot be summed per leg without over-stating it
 * (the bug this replaced).
 *
 * `liveByGroup` (keyed by `PortfolioPositionGroup.key`) lets the tiles follow the
 * per-group WS live-overlay sums instead of the `/portfolio/data` snapshot: a
 * group with a non-null live figure uses it, every other group falls back to its
 * snapshot rows. The result is a blend while some chains are still warming up —
 * the same per-row blend the group rows already show. Omit it for pure-snapshot
 * behaviour (individual-legs view, first paint, tests).
 */
export function computePortfolioTotals(
  groups: PortfolioPositionGroup[],
  portfolio?: NettedMargin | null,
  liveByGroup?: ReadonlyMap<string, GroupLiveTotal> | null,
): PortfolioTotals {
  let mtm = 0;
  let mtmAny = false;
  let carry = 0;
  let carryAny = false;
  let legCount = 0;

  for (const g of groups) {
    legCount += g.rows.length;
    const live = liveByGroup?.get(g.key) ?? null;

    if (live?.mtm != null) {
      mtm += live.mtm;
      mtmAny = true;
    } else {
      for (const row of g.rows as PortfolioPositionRecord[]) {
        const rowMtm = coerceNum(row.current_profit);
        if (rowMtm != null) {
          mtm += rowMtm;
          mtmAny = true;
        }
      }
    }

    if (live?.carry != null) {
      carry += live.carry;
      carryAny = true;
    } else {
      for (const row of g.rows as PortfolioPositionRecord[]) {
        const rowCarry = coerceNum(row.carry_profit);
        if (rowCarry != null) {
          carry += rowCarry;
          carryAny = true;
        }
      }
    }
  }

  const span = coerceNum(portfolio?.span_margin_required);
  const elm = coerceNum(portfolio?.elm_margin_required) ?? 0;
  const totalMargin = span != null ? span + elm : null;

  return {
    totalMtm: mtmAny ? mtm : null,
    totalCarry: carryAny ? carry : null,
    totalMargin,
    spanMargin: span,
    elmMargin: span != null ? elm : null,
    carryReturnPct: coerceNum(portfolio?.carry_margin_returns),
    groupCount: groups.length,
    legCount,
  };
}

/** ₹ with an explicit +/− sign (no parens) — matches the Terminal design's MTM/Carry cells. */
export function formatSignedRupees(raw: number | null): {
  text: string;
  className: string;
} {
  if (raw == null || !Number.isFinite(raw)) {
    return { text: "—", className: "app-text-muted" };
  }
  const v = Math.round(raw);
  const abs = Math.abs(v).toLocaleString("en-IN");
  if (v < 0) {
    return {
      text: `−₹${abs}`,
      className: "text-down",
    };
  }
  if (v > 0) {
    return {
      text: `+₹${abs}`,
      className: "text-up",
    };
  }
  return { text: "₹0", className: "text-muted" };
}
