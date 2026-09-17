import type { IndexSignalSummary } from "@/lib/use-index-quotes";

/** What the navbar shows for one index's direction signal (backend design-decisions #30).
 *
 * "Unavailable" is rendered as a muted dash, never as neutral: neutral is a reading (balanced
 * books), unavailable means there is no reading at all. A signal switched off in Settings is
 * hidden outright rather than shown as unavailable. */
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

const REASON_TEXT: Record<string, string> = {
  warming_up: "warming up — the smoothing needs a few seconds of live order books",
  no_bars: "no futures bars have arrived yet",
  no_open_interest: "the futures feed is not carrying open interest",
  excluded_session: "futures rollover — open interest moves for mechanical reasons on these days",
  low_coverage: "too little of the index's weight has a live order book",
  market_closed: "market closed",
  stale: "the signal feed has stalled",
  not_published: "not published yet",
  no_constituents: "no constituents resolved yet",
};

const DIRECTIONAL = {
  bullish: { arrow: "▲", word: "BULL", toneClass: "bg-up-tint text-up-on-tint", name: "Bullish" },
  bearish: { arrow: "▼", word: "BEAR", toneClass: "bg-down-tint text-down-on-tint", name: "Bearish" },
  neutral: { arrow: "●", word: "NEUT", toneClass: "bg-panel2 text-muted", name: "Neutral" },
} as const;

function signed(value: number): string {
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}`;
}

const WEIGHTS_SOURCE_SHORT: Record<string, string> = {
  nse_api: "NSE",
  bse_api: "BSE",
  niftyindices_factsheet: "niftyindices factsheet",
  seed: "built-in seed",
};

function weightsNote(s: IndexSignalSummary): string | null {
  if (!s.weights_source) return null;
  const source = WEIGHTS_SOURCE_SHORT[s.weights_source] ?? s.weights_source.replace(/_/g, " ");
  return `weights: ${source}${s.weights_as_of ? `, ${s.weights_as_of}` : ""}`;
}

export function indexSignalChip(
  label: string,
  summary: IndexSignalSummary | null | undefined,
): IndexSignalChip {
  if (!summary || summary.reason === "disabled") return { visible: false };

  const expansion = summary.mechanism === "expansion";

  if (summary.state === "unavailable") {
    const why =
      expansion && summary.reason === "warming_up"
        ? "warming up — needs enough one-minute bars to judge what is unusual today"
        : (REASON_TEXT[summary.reason ?? ""] ?? "no reading");
    return {
      visible: true,
      state: "unavailable",
      arrow: "—",
      word: "",
      toneClass: "border border-border-soft text-faint",
      title: `${label} direction signal unavailable: ${why}`,
    };
  }

  const d = DIRECTIONAL[summary.state];
  const parts = [`${label} direction signal: ${d.name}`];
  const t = summary.thresholds;
  if (summary.signal != null) {
    if (expansion) {
      const bar = t && "price_percentile" in t ? ` (fires above the ${Math.round(t.price_percentile * 100)}th percentile)` : "";
      parts.push(`volume-confirmed expansion ${signed(summary.signal)}${bar}`);
    } else {
      const thresholds =
        t && "enter" in t ? ` (enter ±${t.enter.toFixed(2)}, exit ±${t.exit.toFixed(2)})` : "";
      parts.push(`order-book imbalance ${signed(summary.signal)}${thresholds}`);
    }
  }
  // Expansion's coverage is not a share of index weight, so it is not shown as one.
  if (summary.coverage != null && !expansion) parts.push(`coverage ${Math.round(summary.coverage * 100)}%`);
  const note = weightsNote(summary);
  if (note) parts.push(note);
  return {
    visible: true,
    state: summary.state,
    arrow: d.arrow,
    word: d.word,
    toneClass: d.toneClass,
    title: parts.join(" · "),
  };
}
