/** Trading-window rules for the two scalpers, mirroring `app.domain.bots`.
 *
 *  The backend model is the authority -- these same rules are enforced in
 *  `validate_session_windows`, and a PATCH that bypasses this UI is still refused. This
 *  copy exists so the drawer can disable Save and say what is wrong next to the field,
 *  rather than sending a request to be told no.
 *
 *  Zero-padded HH:MM compares correctly as a string, which is why nothing here parses a
 *  time. Keep it that way: `"09:35" < "13:30"` is the whole trick.
 */
import type { SessionWindow, SignalChoice } from "@/lib/use-bots";

/** NSE's session. A window outside it can only ever log `outside_session_window`. */
export const MARKET_OPEN = "09:15";
export const MARKET_CLOSE = "15:30";

/** The earliest a scalper can usefully start by default. The signals' size rankings carry over
 *  from the previous sessions, so most read from about 09:30; a slower momentum signal starts
 *  later -- see `warmupReadyAt`. */
export const EARLIEST_SESSION_START = "09:35";

export const MAX_SESSION_WINDOWS = 4;

function addMinutes(hhmm: string, minutes: number): string {
  const [h, m] = hhmm.split(":").map(Number);
  const total = h * 60 + m + minutes;
  if (total >= 24 * 60) return "23:59";
  const hh = String(Math.floor(total / 60)).padStart(2, "0");
  const mm = String(total % 60).padStart(2, "0");
  return `${hh}:${mm}`;
}

/** When a signal can first read today (docs/signals-streamline-plan.md decision 15).
 *
 *  Levels reset every morning; only size rankings carry over from the previous sessions. So
 *  volume expansion reads once NIFTY's 15-minute open-interest window has filled (09:30), and
 *  momentum once nine of today's candles exist for its trend line: 09:24 at 1 minute, 10:00 at
 *  5, 11:30 at 15. A restart mid-session rebuilds the day from its stored bars, so it does not
 *  re-pay this.
 */
export function warmupReadyAt(signal: Pick<SignalChoice, "mechanism" | "duration">): string {
  if (signal.mechanism === "momentum") return addMinutes(MARKET_OPEN, 9 * signal.duration);
  return addMinutes(MARKET_OPEN, 15);
}

/** A non-blocking note when the chosen signal first reads after the first window opens.
 *
 *  Deliberately not an error: blocking here would let an edit on the Signal tab invalidate a
 *  window saved on the Schedule tab.
 */
export function warmupWarning(
  sessions: SessionWindow[],
  signal: Pick<SignalChoice, "mechanism" | "duration"> | null,
): string | null {
  if (!signal || sessions.length === 0) return null;
  const ready = warmupReadyAt(signal);
  const earliest = sessions.map((w) => w.start).sort()[0];
  if (earliest >= ready) return null;
  const why =
    signal.mechanism === "momentum"
      ? `its trend line needs nine of today's ${signal.duration}-minute candles`
      : "its open-interest window needs 15 minutes of today's trading";
  return (
    `This signal first reads at about ${ready} — ${why}. Trading before then will only log ` +
    `"warming up".`
  );
}

/** The first rule this set of windows breaks, or null. Message text matches the backend's. */
export function validateSessions(
  sessions: SessionWindow[],
  hardSquareOff: string,
): string | null {
  if (sessions.length === 0) return "A bot needs at least one trading window.";
  if (sessions.length > MAX_SESSION_WINDOWS) {
    return `At most ${MAX_SESSION_WINDOWS} trading windows.`;
  }

  for (const w of sessions) {
    const span = `${w.start}–${w.end}`;
    if (!w.start || !w.end) return "Every window needs a start and an end.";
    if (w.start >= w.end) return `Window ${span} must end after it starts.`;
    if (w.start < EARLIEST_SESSION_START) {
      return (
        `Window ${span} starts before ${EARLIEST_SESSION_START}. The indicators are built ` +
        `from live ticks with no backfill, so nothing before that can do anything but warm up.`
      );
    }
    if (w.end > MARKET_CLOSE) return `Window ${span} runs past the ${MARKET_CLOSE} market close.`;
    if (w.end > hardSquareOff) {
      return (
        `Window ${span} ends after the ${hardSquareOff} square-off. The bot would open a ` +
        `position and flatten it on the next pass, paying a round trip of friction for nothing.`
      );
    }
  }

  const ordered = [...sessions].sort((a, b) => a.start.localeCompare(b.start));
  for (let i = 1; i < ordered.length; i += 1) {
    const prev = ordered[i - 1];
    const next = ordered[i];
    if (next.start < prev.end) {
      return `Windows ${prev.start}–${prev.end} and ${next.start}–${next.end} overlap.`;
    }
  }
  return null;
}

/** Shorter than this and a suggested window is not worth offering. */
const MIN_SUGGESTION_MINUTES = 15;

function minutesBetween(from: string, to: string): number {
  const [fh, fm] = from.split(":").map(Number);
  const [th, tm] = to.split(":").map(Number);
  return th * 60 + tm - (fh * 60 + fm);
}

/** A sensible next window to add: the first free gap in the day, an hour long at most.
 *
 *  Gaps between windows count, not just the time after the last one. The default schedule
 *  is 09:35-11:30 and 13:30-15:10 against a 15:15 square-off -- appending would find five
 *  unusable minutes at the end and report the day full, while two hours sit free over
 *  lunch. The suggestion abuts the previous window rather than leaving a minute's grace,
 *  because `in_window` is `start <= now < end` and adjacency is exactly legal.
 */
export function suggestWindow(
  sessions: SessionWindow[],
  hardSquareOff: string,
): SessionWindow | null {
  const ceiling = hardSquareOff < MARKET_CLOSE ? hardSquareOff : MARKET_CLOSE;
  const ordered = [...sessions].sort((a, b) => a.start.localeCompare(b.start));

  const gaps: Array<[string, string]> = [];
  let cursor = EARLIEST_SESSION_START;
  for (const w of ordered) {
    if (w.start > cursor) gaps.push([cursor, w.start]);
    if (w.end > cursor) cursor = w.end;
  }
  if (cursor < ceiling) gaps.push([cursor, ceiling]);

  for (const [from, to] of gaps) {
    if (minutesBetween(from, to) < MIN_SUGGESTION_MINUTES) continue;
    const end = addMinutes(from, 60);
    return { start: from, end: end > to ? to : end };
  }
  return null;
}
