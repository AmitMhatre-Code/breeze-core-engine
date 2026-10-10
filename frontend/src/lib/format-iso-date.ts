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

const MONTH_BY_NAME: ReadonlyMap<string, number> = new Map(
  [
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
  ].flatMap((name, i) => [
    [name, i + 1],
    [name.slice(0, 3), i + 1],
    ...(name === "september" ? [["sept", 9] as [string, number]] : []),
  ]),
);

function realDateIso(y: number, m: number, d: number): string | null {
  if (y < 1000 || m < 1 || m > 12 || d < 1) return null;
  if (d > new Date(y, m, 0).getDate()) return null;
  return toIsoDate(y, m, d);
}

/**
 * A date typed into a date field → `YYYY-MM-DD`, or null when it is not a real date.
 *
 * Accepts the app's own `dd-MMM-yyyy` (any case, full or short month name, `-`, `/`,
 * `.` or space between parts), ISO `yyyy-mm-dd`, and numeric `dd-mm-yyyy` — day first,
 * as dates are written in India, never the US month-first reading. The year must have
 * four digits so a half-typed `202` is never taken for the year 202.
 */
export function parseTypedDate(text: string): string | null {
  const parts = text.trim().replace(/,/g, " ").split(/[\s\-/.]+/).filter(Boolean);
  if (parts.length !== 3) return null;
  const [a, b, c] = parts;
  if (/^\d{4}$/.test(a) && /^\d{1,2}$/.test(b) && /^\d{1,2}$/.test(c)) {
    return realDateIso(Number(a), Number(b), Number(c));
  }
  if (!/^\d{1,2}$/.test(a) || !/^\d{4}$/.test(c)) return null;
  const month = /^\d{1,2}$/.test(b) ? Number(b) : MONTH_BY_NAME.get(b.toLowerCase());
  if (month == null) return null;
  return realDateIso(Number(c), month, Number(a));
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

const IST_TIME_ZONE = "Asia/Kolkata";
const IST_LOCALE = "en-IN";

/** `YYYY-MM-DD HH:MM:SS` (or the `T` variant) with no zone marker. */
const NAIVE_STAMP_RE = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?$/;

/**
 * A zoneless stamp is IST wall-clock already — the backend writes it with
 * `core.timezone.ist_timestamp()` — so it must be formatted as-is, never re-zoned.
 *
 * This is the whole reason `Date` is bypassed here. `new Date("2026-07-21 13:07:54")`
 * has no zone to go on, so JS reads it as *browser-local*; feeding that through
 * `toLocaleString({timeZone: 'Asia/Kolkata'})` then shifts it by the viewer's offset
 * from IST. The value displayed changed with where the viewer was sitting, which is
 * exactly what a trading log must never do.
 */
function formatNaiveIstStamp(raw: string): string | null {
  const m = NAIVE_STAMP_RE.exec(raw.trim());
  if (!m) return null;
  const [, y, mo, d, hh, mm] = m;
  const month = MONTH_SHORT[Number(mo) - 1];
  if (!month) return null;
  const hour24 = Number(hh);
  const period = hour24 < 12 ? "am" : "pm";
  const hour12 = hour24 % 12 === 0 ? 12 : hour24 % 12;
  return `${Number(d)} ${month} ${y}, ${hour12}:${mm} ${period} IST`;
}

function parseApiDateTime(raw: string): Date | null {
  const d = new Date(raw);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** API datetime → `29 Jun 2026, 8:52 pm IST`.
 *
 * Handles both stored shapes: a zoneless IST wall-clock stamp (rendered verbatim) and
 * an instant that carries its own offset, e.g. `reference_data_ingest_history.ingested_at`
 * (`...+05:30`) — those are real instants, so they are converted into IST. */
export function formatApiDateTime(raw: string | null | undefined): string {
  if (!raw) return "—";
  try {
    const naive = formatNaiveIstStamp(raw);
    if (naive) return naive;
    const d = parseApiDateTime(raw);
    if (!d) return raw;
    return `${d.toLocaleString(IST_LOCALE, {
      timeZone: IST_TIME_ZONE,
      dateStyle: "medium",
      timeStyle: "short",
    })} IST`;
  } catch {
    return raw;
  }
}

export { MONTH_SHORT };
