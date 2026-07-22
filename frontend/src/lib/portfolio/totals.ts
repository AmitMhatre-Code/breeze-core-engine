import type { PortfolioPositionRecord } from "@/lib/portfolio";
import type { PortfolioPositionGroup } from "@/lib/portfolio/groupPositions";

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
  /** Span + ELM margin summed across every leg. */
  totalMargin: number | null;
  /** Weighted (by per-row margin) annualised carry return, matching the backend's per-row formula. */
  carryReturnPct: number | null;
  groupCount: number;
  legCount: number;
};

/** Rolls per-leg backend fields into the page-level summary tiles (Total MTM / Carry / Margin / Carry Return). */
export function computePortfolioTotals(
  groups: PortfolioPositionGroup[],
): PortfolioTotals {
  let mtm = 0;
  let mtmAny = false;
  let carry = 0;
  let carryAny = false;
  let margin = 0;
  let marginAny = false;
  let weightedNum = 0;
  let weightedDen = 0;
  let legCount = 0;

  for (const g of groups) {
    for (const row of g.rows as PortfolioPositionRecord[]) {
      legCount += 1;
      const rowMtm = coerceNum(row.current_profit);
      if (rowMtm != null) {
        mtm += rowMtm;
        mtmAny = true;
      }
      const rowCarry = coerceNum(row.carry_profit);
      if (rowCarry != null) {
        carry += rowCarry;
        carryAny = true;
      }
      const span = coerceNum(row.span_margin_required);
      const elm = coerceNum(row.elm_margin_required) ?? 0;
      const rowMargin = span != null ? span + elm : null;
      if (rowMargin != null) {
        margin += rowMargin;
        marginAny = true;
      }
      const carryRet = coerceNum(row.carry_margin_returns);
      if (carryRet != null && rowMargin != null && rowMargin > 0) {
        weightedNum += carryRet * rowMargin;
        weightedDen += rowMargin;
      }
    }
  }

  return {
    totalMtm: mtmAny ? mtm : null,
    totalCarry: carryAny ? carry : null,
    totalMargin: marginAny ? margin : null,
    carryReturnPct: weightedDen > 0 ? weightedNum / weightedDen : null,
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
