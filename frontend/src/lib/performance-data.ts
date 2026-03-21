/** Types and URL helpers for `/performance/data`. */

export type FinancialYearOption = {
  year: string;
  start: string;
  end: string;
};

export type PerformanceDataPayload = {
  performance: Record<string, unknown>;
  funds: Record<string, unknown>;
  margin: Record<string, unknown>;
  years: FinancialYearOption[];
  fy: string;
  start: string;
  end: string;
};

export type MonthlyPerformanceRow = {
  month: string;
  pnl: number;
  brokerage: number;
  taxes: number;
};

export function buildPerformanceDataPath(search: URLSearchParams): string {
  const q = new URLSearchParams();
  const fy = search.get("fy");
  const start = search.get("start");
  const end = search.get("end");
  if (fy) q.set("fy", fy);
  if (start && end) {
    q.set("start", start);
    q.set("end", end);
  }
  const s = q.toString();
  return s ? `/performance/data?${s}` : "/performance/data";
}

export function iciciSuccess<T extends Record<string, unknown>>(
  block: unknown,
): T | undefined {
  if (!block || typeof block !== "object") return undefined;
  const r = block as { Status?: number; Success?: unknown };
  if (r.Status !== 200 || r.Success == null || typeof r.Success !== "object") {
    return undefined;
  }
  return r.Success as T;
}

export function parseMonthlyPerformance(
  performanceRoot: unknown,
): MonthlyPerformanceRow[] {
  const s = iciciSuccess<Record<string, unknown>>(performanceRoot);
  if (!s) return [];
  const raw = s.monthly;
  if (!Array.isArray(raw)) return [];
  const out: MonthlyPerformanceRow[] = [];
  for (const row of raw) {
    if (!row || typeof row !== "object") continue;
    const o = row as Record<string, unknown>;
    const month = typeof o.month === "string" ? o.month : "";
    if (!month) continue;
    const pnl = Number(o.pnl);
    const brokerage = Number(o.brokerage);
    const taxes = Number(o.taxes);
    out.push({
      month,
      pnl: Number.isFinite(pnl) ? pnl : 0,
      brokerage: Number.isFinite(brokerage) ? brokerage : 0,
      taxes: Number.isFinite(taxes) ? taxes : 0,
    });
  }
  return out;
}
