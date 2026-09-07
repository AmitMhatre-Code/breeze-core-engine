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
import type { SessionWindow } from "@/lib/use-bots";

/** NSE's session. A window outside it can only ever log `outside_session_window`. */
export const MARKET_OPEN = "09:15";
export const MARKET_CLOSE = "15:30";

/** The earliest a scalper can usefully start, at the default signal settings: the volume MA
 *  needs 20 one-minute bars built from live ticks and there is no historical backfill. A
 *  slower signal pushes the real figure later -- see `warmupReadyAt`. */
export const EARLIEST_SESSION_START = "09:35";

export const MAX_SESSION_WINDOWS = 4;

export type SignalPeriods = {
  ema_period: number;
  volume_ma_period: number;
  candle_seconds: number;
};

function addMinutes(hhmm: string, minutes: number): string {
  const [h, m] = hhmm.split(":").map(Number);
  const total = h * 60 + m + minutes;
  if (total >= 24 * 60) return "23:59";
  const hh = String(Math.floor(total / 60)).padStart(2, "0");
  const mm = String(total % 60).padStart(2, "0");
  return `${hh}:${mm}`;
}

/** When the indicators can first be computable, given the signal settings.
 *
 *  Warm-up is `max(ema, volumeMA)` bars of `candle_seconds` each, counted from the first
 *  tick rather than from a wall-clock time -- so this assumes the app was running before
 *  the open, which is the normal case and the one worth warning about. A restart mid-session
 *  re-pays it, and no static warning can predict that.
 */
export function warmupReadyAt(signal: SignalPeriods): string {
  const bars = Math.max(signal.ema_period, signal.volume_ma_period);
  return addMinutes(MARKET_OPEN, Math.ceil((bars * signal.candle_seconds) / 60));
}

/** A non-blocking note when the signal settings push warm-up past the first window.
 *
 *  Deliberately not an error: blocking here would let an edit on the Signal tab invalidate
 *  a window saved on the Schedule tab, and an extreme-but-legal signal (200 bars of 5
 *  minutes) would make every window unsaveable.
 */
export function warmupWarning(
  sessions: SessionWindow[],
  signal: SignalPeriods | null,
): string | null {
  if (!signal || sessions.length === 0) return null;
  const ready = warmupReadyAt(signal);
  if (ready <= EARLIEST_SESSION_START) return null;
  const earliest = sessions.map((w) => w.start).sort()[0];
  if (earliest >= ready) return null;
  const bars = Math.max(signal.ema_period, signal.volume_ma_period);
  return (
    `These signal settings need ${bars} bars of ${signal.candle_seconds}s, so the ` +
    `indicators are not ready until about ${ready}. Trading before then will only log ` +
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
