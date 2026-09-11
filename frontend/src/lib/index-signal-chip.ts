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

  if (summary.state === "unavailable") {
    const why = REASON_TEXT[summary.reason ?? ""] ?? "no reading";
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
  if (summary.signal != null) {
    const thresholds = summary.thresholds
      ? ` (enter ±${summary.thresholds.enter.toFixed(2)}, exit ±${summary.thresholds.exit.toFixed(2)})`
      : "";
    parts.push(`order-book imbalance ${signed(summary.signal)}${thresholds}`);
  }
  if (summary.coverage != null) parts.push(`coverage ${Math.round(summary.coverage * 100)}%`);
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
