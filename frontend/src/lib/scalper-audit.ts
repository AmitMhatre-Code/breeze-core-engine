/** Reads the audit detail a scalper writes onto its open run row (`runtime._audit_detail`).
 *
 *  The run log and the bot card both answer "why is nothing happening", one after the fact
 *  and one while it is happening, so the sentence is written once here and rendered twice.
 *
 *  The distinction this exists to make: `not_warm` reads as "give it twenty minutes"
 *  whether the feed is filling normally or was never subscribed at all. Those are the same
 *  reason code and completely different problems, and only `subscribed`/`ticks_seen`
 *  separate them.
 */

export type ScalperFeedDetail = {
  warm?: boolean;
  stale?: boolean;
  stale_seconds?: number | null;
  subscribed?: boolean;
  contract?: string | null;
  ticks_seen?: number | null;
  candles?: number | null;
  candles_required?: number | null;
  /** Candle history discarded at an IST day boundary — the one legitimate reset. */
  counter_resets?: number | null;
  /** Packets refused because their cumulative counters ran backwards. */
  stale_ticks?: number | null;
  last_error?: string | null;
};

export type ScalperRunDetail = {
  feed?: ScalperFeedDetail;
  gates?: Record<string, unknown>;
};

/** `bad` is "this will not fix itself"; `warn` is "working, not ready yet". */
export type FeedTone = "ok" | "warn" | "bad";

export type FeedSummary = { text: string; tone: FeedTone };

function count(value: number | null | undefined): string {
  return typeof value === "number" ? value.toLocaleString("en-IN") : "0";
}

/** The feed's state in one line, or null when the run carries no feed detail at all
 *  (every non-scalper bot, and any run recorded before this was added). */
export function describeFeed(detail: unknown): FeedSummary | null {
  const feed = (detail as ScalperRunDetail | null)?.feed;
  if (!feed || typeof feed !== "object") return null;

  if (feed.last_error) {
    return { text: `Futures feed: ${feed.last_error}`, tone: "bad" };
  }
  if (!feed.subscribed) {
    return {
      // The failure that produced no cycles for a whole session, named outright.
      text: "Futures feed not subscribed — no ticks are reaching the signal.",
      tone: "bad",
    };
  }
  if (feed.stale) {
    const age = typeof feed.stale_seconds === "number" ? ` for ${Math.round(feed.stale_seconds)}s` : "";
    return { text: `Futures feed quiet${age} — entries are frozen.`, tone: "bad" };
  }

  const ticks = `${count(feed.ticks_seen)} ticks`;
  // Stale packets are named outright rather than left to be inferred from a low candle
  // count. A session losing history to out-of-order ticks looks exactly like an ordinary
  // slow warm-up, and that ambiguity cost a full day of paper evidence before it was seen.
  const dropped = feed.stale_ticks ?? 0;
  const staleNote = dropped > 0 ? ` · ${count(dropped)} stale ticks dropped` : "";

  if (feed.warm) {
    const contract = feed.contract ? ` · ${feed.contract}` : "";
    return {
      text: `Futures feed live · ${ticks}${contract}${staleNote}`,
      tone: dropped > 0 ? "warn" : "ok",
    };
  }
  const required = feed.candles_required ?? 0;
  return {
    text: `Warming up · ${count(feed.candles)} of ${count(required)} candles · ${ticks}${staleNote}`,
    tone: "warn",
  };
}

/** Tailwind text colour for a tone, matching the palette the bot cards already use. */
export function feedToneClass(tone: FeedTone): string {
  if (tone === "bad") return "text-down";
  if (tone === "warn") return "text-amber-accent";
  return "text-faint";
}
