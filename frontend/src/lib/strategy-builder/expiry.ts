const MONTHS: Record<string, number> = {
  Jan: 0,
  Feb: 1,
  Mar: 2,
  Apr: 3,
  May: 4,
  Jun: 5,
  Jul: 6,
  Aug: 7,
  Sep: 8,
  Oct: 9,
  Nov: 10,
  Dec: 11,
};

/** Parse DD-Mon-YYYY (scrip master / ICICI display) to year fraction until expiry (minimum ~1 day). */
export function expiryDisplayToYears(expiryDisplay: string): number {
  const m = expiryDisplay.match(/^(\d{2})-([A-Za-z]{3})-(\d{4})$/);
  if (!m) return 30 / 365;
  const day = parseInt(m[1], 10);
  const monKey = m[2];
  const year = parseInt(m[3], 10);
  const mon = MONTHS[monKey];
  if (mon === undefined || !Number.isFinite(day)) return 30 / 365;
  const exp = new Date(year, mon, day);
  const ms = exp.getTime() - Date.now();
  const years = ms / (365.25 * 24 * 3600 * 1000);
  return Math.max(1 / 365, years);
}

/** F&O contracts expire at the 15:30 IST close, which is 10:00 UTC. */
const EXPIRY_CLOSE_UTC_HOUR = 10;
const MS_PER_YEAR = 365 * 24 * 3600 * 1000;
/** One minute: past the close the Greeks collapse to intrinsic instead of dividing by zero. */
const MIN_YEARS_TO_CLOSE = 60_000 / MS_PER_YEAR;

/** Year fraction until 15:30 IST on the expiry date, independent of the browser's time zone.
 * Used by the Greeks; PoP and the payoff curves still use `expiryDisplayToYears`. Invalid → null. */
export function yearsToExpiryClose(expiryDisplay: string, nowMs: number = Date.now()): number | null {
  const m = expiryDisplay.trim().match(/^(\d{2})-([A-Za-z]{3})-(\d{4})$/);
  if (!m) return null;
  const mon = MONTHS[m[2]];
  if (mon === undefined) return null;
  const closeMs = Date.UTC(parseInt(m[3], 10), mon, parseInt(m[1], 10), EXPIRY_CLOSE_UTC_HOUR);
  return Math.max(MIN_YEARS_TO_CLOSE, (closeMs - nowMs) / MS_PER_YEAR);
}

/** Parse DD-Mon-YYYY to UTC ms at local midnight (for sorting). Invalid → 0. */
export function expiryDisplayToTimestamp(expiryDisplay: string): number {
  const m = expiryDisplay.match(/^(\d{2})-([A-Za-z]{3})-(\d{4})$/);
  if (!m) return 0;
  const day = parseInt(m[1], 10);
  const monKey = m[2];
  const year = parseInt(m[3], 10);
  const mon = MONTHS[monKey];
  if (mon === undefined || !Number.isFinite(day)) return 0;
  return new Date(year, mon, day).getTime();
}

/** True when `expiryDisplay` is a valid DD-Mon-YYYY date. */
export function isValidExpiryDisplay(expiryDisplay: string): boolean {
  return expiryDisplayToTimestamp(expiryDisplay.trim()) > 0;
}

/** Earliest expiry first (DD-Mon-YYYY from scrip master). */
export function sortExpiryDatesAsc(dates: string[]): string[] {
  return [...dates].sort(
    (a, b) => expiryDisplayToTimestamp(a) - expiryDisplayToTimestamp(b),
  );
}

/** Chip label e.g. `21-Mar-2026` → `21 Mar`. */
export function formatExpiryChipShort(display: string): string {
  const m = display.match(/^(\d{2})-([A-Za-z]{3})-\d{4}$/);
  if (!m) return display;
  return `${parseInt(m[1], 10)} ${m[2]}`;
}

/** Filter expiry dates by full date, chip label, or month abbreviation. */
export function filterExpiryDates(dates: string[], query: string): string[] {
  const qn = query.trim().toLowerCase();
  if (!qn) return dates;
  return dates.filter((d) => {
    if (d.toLowerCase().includes(qn)) return true;
    if (formatExpiryChipShort(d).toLowerCase().includes(qn)) return true;
    const monthMatch = d.match(/^\d{2}-([A-Za-z]{3})-\d{4}$/);
    if (monthMatch && monthMatch[1].toLowerCase().includes(qn)) return true;
    return false;
  });
}
