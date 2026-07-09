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

/** Same shape, but a month with no trade data is `null` across the board
 * rather than omitted — used once real months are padded out to a full FY. */
export type MonthlyChartRow = {
  month: string;
  pnl: number | null;
  brokerage: number | null;
  taxes: number | null;
};

const FY_MONTH_ABBR = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];

/** All 12 month keys (backend's `%b-%y` shape, e.g. "Apr-25") for the financial
 * year starting at `fyStartDate` ("YYYY-MM-DD", always an Apr 1). */
export function financialYearMonthKeys(fyStartDate: string): string[] {
  const d = new Date(`${fyStartDate}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return [];
  let year = d.getUTCFullYear();
  let month = d.getUTCMonth();
  const keys: string[] = [];
  for (let i = 0; i < 12; i++) {
    const yy = String(year % 100).padStart(2, "0");
    keys.push(`${FY_MONTH_ABBR[month]}-${yy}`);
    month += 1;
    if (month > 11) {
      month = 0;
      year += 1;
    }
  }
  return keys;
}

/** Fills in every month of the financial year, leaving months with no trade
 * data as `null` (so the chart renders no bar there) rather than dropping them. */
export function padMonthlyToFinancialYear(
  monthly: MonthlyPerformanceRow[],
  fyStartDate: string | undefined,
): MonthlyChartRow[] {
  if (!fyStartDate) return monthly;
  const months = financialYearMonthKeys(fyStartDate);
  if (!months.length) return monthly;
  const byMonth = new Map(monthly.map((m) => [m.month, m]));
  return months.map(
    (month) => byMonth.get(month) ?? { month, pnl: null, brokerage: null, taxes: null },
  );
}

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
