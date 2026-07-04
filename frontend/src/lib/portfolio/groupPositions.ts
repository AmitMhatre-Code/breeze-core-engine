import type { PortfolioPositionRecord } from "@/lib/portfolio";
import { expiryDisplayToTimestamp } from "@/lib/strategy-builder/expiry";

function parseFinite(v: unknown): number | null {
  if (v == null || v === "") return null;
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string") {
    const t = v.trim().replace(/,/g, "");
    if (!t || t === "*") return null;
    const n = Number(t);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

function strikeSortKey(row: PortfolioPositionRecord): number {
  return parseFinite(row.strike_price) ?? 0;
}

export function portfolioGroupKey(row: PortfolioPositionRecord): string {
  const stock = String(row.stock_code ?? "").trim();
  const ex = String(row.exchange_code ?? "NFO").trim() || "NFO";
  const exp = String(row.expiry_date ?? "").trim();
  return `${stock}|${ex}|${exp}`;
}

export type PortfolioPositionGroup = {
  key: string;
  stockCode: string;
  exchangeCode: string;
  expiryDate: string;
  rows: PortfolioPositionRecord[];
};

export function buildPortfolioPositionGroups(
  positions: PortfolioPositionRecord[],
): PortfolioPositionGroup[] {
  const map = new Map<string, PortfolioPositionRecord[]>();
  for (const row of positions) {
    const k = portfolioGroupKey(row);
    const list = map.get(k);
    if (list) list.push(row);
    else map.set(k, [row]);
  }

  const groups: PortfolioPositionGroup[] = [];
  for (const [key, rows] of map) {
    const sorted = [...rows].sort((a, b) => {
      const ds = strikeSortKey(a) - strikeSortKey(b);
      if (ds !== 0) return ds;
      return String(a.right ?? "").localeCompare(String(b.right ?? ""));
    });
    const [stockCode, exchangeCode, expiryDate] = key.split("|");
    groups.push({
      key,
      stockCode,
      exchangeCode,
      expiryDate,
      rows: sorted,
    });
  }

  groups.sort((a, b) => {
    const c = a.stockCode.localeCompare(b.stockCode);
    if (c !== 0) return c;
    return (
      expiryDisplayToTimestamp(a.expiryDate) -
      expiryDisplayToTimestamp(b.expiryDate)
    );
  });

  return groups;
}

function groupMarginTotal(group: PortfolioPositionGroup): number {
  let total = 0;
  for (const row of group.rows) {
    const span = parseFinite(row.span_margin_required) ?? 0;
    const elm = parseFinite(row.elm_margin_required) ?? 0;
    total += span + elm;
  }
  return total;
}

/** Largest position by blocked margin (Span + ELM) — the group auto-expanded on page load. */
export function pickTopGroupKey(
  groups: PortfolioPositionGroup[],
): string | null {
  if (!groups.length) return null;
  let best = groups[0];
  let bestMargin = groupMarginTotal(best);
  for (let i = 1; i < groups.length; i++) {
    const margin = groupMarginTotal(groups[i]);
    if (margin > bestMargin) {
      best = groups[i];
      bestMargin = margin;
    }
  }
  return best.key;
}
