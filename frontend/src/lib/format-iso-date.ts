const MONTH_SHORT = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
] as const;

export function parseIsoDateParts(
  iso: string,
): { y: number; m: number; d: number } | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso.trim());
  if (!m) return null;
  const y = Number(m[1]);
  const mo = Number(m[2]);
  const d = Number(m[3]);
  if (mo < 1 || mo > 12 || d < 1 || d > 31) return null;
  return { y, m: mo, d };
}

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

export function toIsoDate(y: number, m: number, d: number): string {
  return `${y}-${pad2(m)}-${pad2(d)}`;
}

function formatYmdParts(y: number, m: number, d: number): string | null {
  if (m < 1 || m > 12 || d < 1 || d > 31) return null;
  return `${pad2(d)}-${MONTH_SHORT[m - 1]}-${y}`;
}

/** Always `dd-MMM-yyyy`, e.g. `27-Jun-2026`. */
export function formatIsoDateDdMmmYyyy(iso: string): string {
  const p = parseIsoDateParts(iso);
  if (!p) return iso.trim();
  return formatYmdParts(p.y, p.m, p.d) ?? iso.trim();
}

/** `YYYY-MM-DD` or `YYYYMMDD` (e.g. NSE SPAN archive) → `dd-MMM-yyyy`. */
export function formatSourceFileDate(input: string | null | undefined): string {
  if (!input) return "—";
  const s = input.trim();
  if (!s) return "—";

  const iso = parseIsoDateParts(s);
  if (iso) return formatYmdParts(iso.y, iso.m, iso.d) ?? s;

  const compact = /^(\d{4})(\d{2})(\d{2})$/.exec(s);
  if (compact) {
    const y = Number(compact[1]);
    const m = Number(compact[2]);
    const d = Number(compact[3]);
    return formatYmdParts(y, m, d) ?? s;
  }

  return s;
}

export { MONTH_SHORT };
