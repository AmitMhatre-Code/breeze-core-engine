import type { BotRun, BotType } from "@/lib/use-bots";
import type { FeedSummary } from "@/lib/scalper-audit";

/** The bot cards no longer print a session's running verdict ("Warming up…", "Futures feed
 *  quiet…", CAS Bingo's per-index reasons). That sentence lives on the session's row in Activity,
 *  where it is also kept for tomorrow. A card only flags that there is something worth reading
 *  there: a warning triangle beside its Cycles / Positions line, which opens Activity filtered
 *  to that bot. */

export const ACTIVITY_SECTION_ID = "bot-activity";
export const ACTIVITY_FOCUS_EVENT = "bots:focus-activity";

export type ActivityFocusDetail = { botType: BotType };

/** Scroll to the Activity table and show today's rows for this bot. */
export function focusActivity(botType: BotType): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(
    new CustomEvent<ActivityFocusDetail>(ACTIVITY_FOCUS_EVENT, { detail: { botType } }),
  );
}

/** CAS Bingo verdicts that will not fix themselves. Everything else it writes (outside the
 *  window, waiting for the auction, the signal said no, a position managed to its exit) is a
 *  normal day, and a triangle on a normal day trains the user to ignore the triangle. */
const CAS_BINGO_BAD = new Set([
  "no_broker_session",
  "order_rejected",
  "entry_partial_unwound",
  "liquidation_insufficient",
  "margin_lookup_failed",
  "quote_unavailable",
  "internal_error",
]);

/** Working, but not able to trade right now: the user may want to know, nothing is broken. */
const CAS_BINGO_WARN = new Set([
  "chain_not_ready",
  "day_open_unavailable",
  "margin_insufficient",
  "margin_cap_too_small",
  "outlay_below_one_lot",
  "liquidity_thin",
  "entry_unfilled",
  "sg_rule_conflict",
  "trading_read_only",
]);

type IndexVerdict = { reason_code?: string | null; reason_text?: string | null };

/** The card's triangle for CAS Bingo's open session, or null on a normal day. The verdict can
 *  cover two indices (NIFTY and SENSEX); the worse of the two sets the tone. */
export function casBingoAttention(run: BotRun | null | undefined): FeedSummary | null {
  if (!run) return null;
  const indices = (run.detail as { indices?: Record<string, IndexVerdict> } | null)?.indices;
  const verdicts: IndexVerdict[] =
    indices && Object.keys(indices).length
      ? Object.values(indices)
      : [{ reason_code: run.reason_code, reason_text: run.reason_text }];
  const bad = verdicts.filter((v) => CAS_BINGO_BAD.has(v.reason_code ?? ""));
  const warn = verdicts.filter((v) => CAS_BINGO_WARN.has(v.reason_code ?? ""));
  const flagged = bad.length ? bad : warn;
  if (!flagged.length) return null;
  return {
    text: flagged.map((v) => v.reason_text ?? v.reason_code).join(" · "),
    tone: bad.length ? "bad" : "warn",
  };
}
