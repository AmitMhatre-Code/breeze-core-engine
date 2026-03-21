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
