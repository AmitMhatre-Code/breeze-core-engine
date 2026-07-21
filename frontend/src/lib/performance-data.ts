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

/** Backend `weekly` row. `week` is the ISO date of that week's Monday. */
export type WeeklyPerformanceRow = {
  week: string;
  pnl: number;
  brokerage: number;
  taxes: number;
};

/** A chart/table row for either granularity: `label` is already display-ready,
 * and a period with no trade data is `null` across the board rather than
 * omitted — used once real periods are padded out to a full FY. */
export type PeriodChartRow = {
  label: string;
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
): PeriodChartRow[] {
  const asRows = (rows: MonthlyPerformanceRow[]): PeriodChartRow[] =>
    rows.map(({ month, ...rest }) => ({ label: month, ...rest }));
  if (!fyStartDate) return asRows(monthly);
  const months = financialYearMonthKeys(fyStartDate);
  if (!months.length) return asRows(monthly);
  const byMonth = new Map(monthly.map((m) => [m.month, m]));
  return months.map((month) => {
    const hit = byMonth.get(month);
    return hit
      ? { label: month, pnl: hit.pnl, brokerage: hit.brokerage, taxes: hit.taxes }
      : { label: month, pnl: null, brokerage: null, taxes: null };
  });
}

/** ISO date ("YYYY-MM-DD") of the Monday of the week containing `isoDate`. */
function mondayOf(isoDate: string): string {
  const d = new Date(`${isoDate}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return "";
  // getUTCDay(): 0=Sun … 6=Sat. Sunday belongs to the week that started 6 days earlier.
  const offset = (d.getUTCDay() + 6) % 7;
  d.setUTCDate(d.getUTCDate() - offset);
  return d.toISOString().slice(0, 10);
}

/** Every week key (ISO Monday) covering the financial year, from the Monday of
 * the week containing `fyStartDate` through the Monday of the week containing
 * `fyEndDate`. Boundary weeks are clipped to the FY, so the first and last
 * bucket can hold a partial week's trades. */
export function financialYearWeekKeys(
  fyStartDate: string,
  fyEndDate: string,
): string[] {
  const first = mondayOf(fyStartDate);
  const last = mondayOf(fyEndDate);
  if (!first || !last || last < first) return [];
  const keys: string[] = [];
  const cursor = new Date(`${first}T00:00:00Z`);
  // Guard against a malformed range spinning forever — an FY is ~53 weeks.
  for (let i = 0; i < 60; i++) {
    const key = cursor.toISOString().slice(0, 10);
    keys.push(key);
    if (key === last) break;
    cursor.setUTCDate(cursor.getUTCDate() + 7);
  }
  return keys;
}

const WEEK_LABEL_MONTH_ABBR = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];

/** "2026-07-20" → "20 Jul" (the week's Monday). */
export function formatWeekLabel(isoDate: string): string {
  const d = new Date(`${isoDate}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return isoDate;
  return `${String(d.getUTCDate()).padStart(2, "0")} ${WEEK_LABEL_MONTH_ABBR[d.getUTCMonth()]}`;
}

/** Fills in every week of the financial year, leaving weeks with no trade data
 * as `null` (so the chart renders no bar there) rather than dropping them. */
export function padWeeklyToFinancialYear(
  weekly: WeeklyPerformanceRow[],
  fyStartDate: string | undefined,
  fyEndDate: string | undefined,
): PeriodChartRow[] {
  const asRows = (rows: WeeklyPerformanceRow[]): PeriodChartRow[] =>
    rows.map(({ week, ...rest }) => ({ label: formatWeekLabel(week), ...rest }));
  if (!fyStartDate || !fyEndDate) return asRows(weekly);
  const weeks = financialYearWeekKeys(fyStartDate, fyEndDate);
  if (!weeks.length) return asRows(weekly);
  const byWeek = new Map(weekly.map((w) => [w.week, w]));
  return weeks.map((week) => {
    const hit = byWeek.get(week);
    const label = formatWeekLabel(week);
    return hit
      ? { label, pnl: hit.pnl, brokerage: hit.brokerage, taxes: hit.taxes }
      : { label, pnl: null, brokerage: null, taxes: null };
  });
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

/** Shared body of the `monthly` / `weekly` parsers — they differ only in which
 * array they read and which field carries the period key. */
function parsePeriodRows<K extends string>(
  performanceRoot: unknown,
  arrayKey: "monthly" | "weekly",
  periodKey: K,
): (Record<K, string> & { pnl: number; brokerage: number; taxes: number })[] {
  const s = iciciSuccess<Record<string, unknown>>(performanceRoot);
  if (!s) return [];
  const raw = s[arrayKey];
  if (!Array.isArray(raw)) return [];
  const out: (Record<K, string> & {
    pnl: number;
    brokerage: number;
    taxes: number;
  })[] = [];
  for (const row of raw) {
    if (!row || typeof row !== "object") continue;
    const o = row as Record<string, unknown>;
    const period = typeof o[periodKey] === "string" ? (o[periodKey] as string) : "";
    if (!period) continue;
    const pnl = Number(o.pnl);
    const brokerage = Number(o.brokerage);
    const taxes = Number(o.taxes);
    out.push({
      ...({ [periodKey]: period } as Record<K, string>),
      pnl: Number.isFinite(pnl) ? pnl : 0,
      brokerage: Number.isFinite(brokerage) ? brokerage : 0,
      taxes: Number.isFinite(taxes) ? taxes : 0,
    });
  }
  return out;
}

export function parseMonthlyPerformance(
  performanceRoot: unknown,
): MonthlyPerformanceRow[] {
  return parsePeriodRows(performanceRoot, "monthly", "month");
}

export function parseWeeklyPerformance(
  performanceRoot: unknown,
): WeeklyPerformanceRow[] {
  return parsePeriodRows(performanceRoot, "weekly", "week");
}
