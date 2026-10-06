import type { BotRun, BotRunBundle } from "@/lib/use-bots";

/** Ranges up to this many days are fetched as raw runs and bundled here, so expanding a bundle
 *  is instant; longer ranges come back already bundled from `/bots/runs/bundles`. */
export const CLIENT_BUNDLE_MAX_DAYS = 7;
/** The longest range the Activity filter accepts (the backend enforces the same cap). */
export const MAX_RANGE_DAYS = 31;

export type RunLogPreset = "today" | "week" | "month" | "custom";
export type DateRange = { from: string; to: string };

const IST_DATE = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Kolkata",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

/** Today's date in IST as `YYYY-MM-DD` — the day every stored run timestamp is written in. */
export function istToday(now: Date = new Date()): string {
  return IST_DATE.format(now);
}

function dayNumber(isoDate: string): number {
  const [y, m, d] = isoDate.split("-").map(Number);
  return Date.UTC(y, m - 1, d) / 86_400_000;
}

export function shiftDate(isoDate: string, days: number): string {
  return new Date((dayNumber(isoDate) + days) * 86_400_000).toISOString().slice(0, 10);
}

/** Days in an inclusive range; `today..today` is 1. */
export function rangeDays({ from, to }: DateRange): number {
  return dayNumber(to) - dayNumber(from) + 1;
}

/** Week and Month are rolling windows ending today (7 and 30 days), not calendar periods. */
export function presetRange(preset: Exclude<RunLogPreset, "custom">, today: string): DateRange {
  const span = preset === "today" ? 1 : preset === "week" ? 7 : 30;
  return { from: shiftDate(today, -(span - 1)), to: today };
}

/** Why a custom range can't be used, or null when it can. */
export function customRangeError(range: DateRange, today: string): string | null {
  if (!range.from || !range.to) return "Pick both dates.";
  if (range.to < range.from) return "The end date is before the start date.";
  if (range.to > today) return "The end date is in the future.";
  if (rangeDays(range) > MAX_RANGE_DAYS) return `A range can span at most ${MAX_RANGE_DAYS} days.`;
  return null;
}

function reasonKey(run: BotRun): string {
  return run.reason_code || run.reason_text || "";
}

/** Bundle runs given newest first, as `/bots/runs` returns them.
 *
 *  Mirrors `backend/.../services/bots/run_bundles.py` exactly — the same runs must bundle the
 *  same way whichever side did it. Back-to-back is per bot: only that bot's own next run with a
 *  different day, trigger or status closes a bundle; other bots' runs in between do not. */
export function bundleRuns(runsNewestFirst: BotRun[]): BotRunBundle[] {
  const groups: { pos: number; members: BotRun[] }[] = [];
  const open = new Map<string, number>();

  for (let pos = runsNewestFirst.length - 1; pos >= 0; pos--) {
    const run = runsNewestFirst[pos];
    const day = (run.started_at ?? "").slice(0, 10);
    const idx = open.get(run.bot_type);
    if (idx !== undefined) {
      const group = groups[idx];
      const last = group.members[group.members.length - 1];
      if (
        (last.started_at ?? "").slice(0, 10) === day &&
        last.trigger === run.trigger &&
        last.status === run.status
      ) {
        group.members.push(run);
        group.pos = pos;
        continue;
      }
    }
    open.set(run.bot_type, groups.length);
    groups.push({ pos, members: [run] });
  }

  return groups
    .sort((a, b) => a.pos - b.pos)
    .map(({ members }) => {
      const first = members[0];
      const latest = members[members.length - 1];
      const count = members.length;
      return {
        bot_type: latest.bot_type,
        trigger: latest.trigger,
        status: latest.status,
        date: (latest.started_at ?? "").slice(0, 10),
        count,
        first_started_at: first.started_at,
        last_started_at: latest.started_at,
        latest,
        distinct_reasons: Math.max(1, new Set(members.map(reasonKey)).size),
        // A live bot writes one audit file per day, so every member shares the latest's link; a
        // backtest's trail is per run, so a bundle of several keeps the links on its runs.
        audit_log: latest.trigger === "backtest" && count > 1 ? null : latest.audit_log,
        runs: [...members].reverse(),
      };
    });
}

/** The key a bundle is addressed by in React and the query cache. */
export function bundleKey(b: BotRunBundle): string {
  return `${b.bot_type}|${b.trigger}|${b.status}|${b.first_started_at}|${b.last_started_at}|${b.latest.id}`;
}

/** The Activity columns with a value filter. Each is a field every run in a bundle shares, so a
 *  bundle passes or fails whole and filtering never needs to re-bundle. */
export type RunLogColumn = "bot_type" | "trigger" | "status";
/** Each column's ticked values; a column left out (or null) shows every value. */
export type RunLogFilters = Partial<Record<RunLogColumn, readonly string[] | null>>;

function passes(bundle: BotRunBundle, filters: RunLogFilters, skip?: RunLogColumn): boolean {
  return (Object.keys(filters) as RunLogColumn[]).every((col) => {
    if (col === skip) return true;
    const ticked = filters[col];
    return !ticked || ticked.includes(bundle[col]);
  });
}

export function filterBundles(bundles: BotRunBundle[], filters: RunLogFilters): BotRunBundle[] {
  return bundles.filter((b) => passes(b, filters));
}

/** Runs (not rows) per value of one column, among the bundles every *other* column's filter lets
 *  through — so a count says what ticking that value would show, as Excel's lists do. */
export function columnCounts(
  bundles: BotRunBundle[],
  filters: RunLogFilters,
  column: RunLogColumn,
): Map<string, number> {
  const counts = new Map<string, number>();
  for (const b of bundles) {
    if (!passes(b, filters, column)) continue;
    counts.set(b[column], (counts.get(b[column]) ?? 0) + b.count);
  }
  return counts;
}

export function isFiltered(filters: RunLogFilters): boolean {
  return Object.values(filters).some((ticked) => ticked != null);
}
