import type { IndexSignalSummary } from "@/lib/use-index-quotes";

/** What the navbar shows for one index's direction signal: the 15-minute reading of the mechanism
 * chosen on the Signals page (docs/signals-streamline-plan.md decision 11).
 *
 * "Unavailable" is rendered as a muted dash, never as neutral: neutral is a reading (the market
 * was looked at and nothing fired), unavailable means there is no reading at all. */
export type IndexSignalChip =
  | { visible: false }
  | {
      visible: true;
      state: IndexSignalSummary["state"];
      arrow: string;
      word: string;
      toneClass: string;
      title: string;
    };

const MECHANISM_NAME: Record<string, string> = {
  expansion: "Volume expansion",
  momentum: "Momentum",
};

const REASON_TEXT: Record<string, string> = {
  market_closed: "market closed",
  outside_session: "signals run 09:15–15:15",
  warming_up: "warming up — not enough of today's candles yet",
  no_bars: "no futures data has arrived yet today",
  stale: "the futures feed has paused",
  excluded_session: "futures rollover — open interest moves for mechanical reasons on these days",
  no_open_interest: "the futures feed is not carrying open interest",
  volume_unavailable: "a candle's traded volume is unknown",
  vwap_unavailable: "no average traded price yet",
  not_published: "the signal is not running",
};

const DIRECTIONAL = {
  bullish: { arrow: "▲", word: "BULL", toneClass: "bg-up-tint text-up-on-tint", name: "Bullish" },
  bearish: { arrow: "▼", word: "BEAR", toneClass: "bg-down-tint text-down-on-tint", name: "Bearish" },
  neutral: { arrow: "●", word: "NEUT", toneClass: "bg-panel2 text-muted", name: "Quiet" },
} as const;

function source(s: IndexSignalSummary): string {
  const name = MECHANISM_NAME[s.mechanism ?? ""] ?? "Signal";
  const minutes = s.duration_minutes ? ` ${s.duration_minutes}m` : "";
  return `${name}${minutes}`;
}

export function indexSignalChip(
  label: string,
  summary: IndexSignalSummary | null | undefined,
): IndexSignalChip {
  if (!summary) return { visible: false };

  if (summary.state === "unavailable") {
    const why = REASON_TEXT[summary.reason ?? ""] ?? "no reading";
    return {
      visible: true,
      state: "unavailable",
      arrow: "—",
      word: "",
      toneClass: "border border-border-soft text-faint",
      title: `${label} · ${source(summary)}: no reading — ${why}`,
    };
  }

  const d = DIRECTIONAL[summary.state];
  const parts = [`${label} · ${source(summary)}: ${d.name}`];
  if (summary.state !== "neutral" && summary.held_until) {
    const until = new Date(summary.held_until * 1000).toLocaleTimeString("en-IN", {
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
      timeZone: "Asia/Kolkata",
    });
    parts.push(`call stands until ${until}`);
  }
  if (summary.thin_data) parts.push("thin data: SENSEX futures trade lightly");
  return {
    visible: true,
    state: summary.state,
    arrow: d.arrow,
    word: d.word,
    toneClass: d.toneClass,
    title: parts.join(" · "),
  };
}
