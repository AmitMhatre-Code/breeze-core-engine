import { apiClient } from "@/lib/api-client";
import { getBackendBaseUrl } from "@/lib/config";
import type { BacktestJob, BacktestPeriod } from "@/lib/bots-backtest";

/** Settings → Index Signal (backend `app/services/index_signal/settings.py`, design-decisions #30). */

export type IndexLabel = "nifty" | "sensex";

export type IndexSignalNumericField =
  | "top_n"
  | "tau_seconds"
  | "enter_threshold"
  | "exit_threshold"
  | "min_coverage"
  | "book_stale_seconds"
  | "depth_levels"
  | "shadow_retention_days";

export type IndexSignalFieldBound = {
  min: number;
  max: number;
  recommended_min: number;
  recommended_max: number;
  default: number;
  integer: boolean;
};

export type IndexSignalPreferences = { enabled: boolean } & Record<IndexSignalNumericField, number> & {
    bounds: Record<IndexSignalNumericField, IndexSignalFieldBound>;
  };

export type IndexSignalPreferencesUpdate = Partial<
  { enabled: boolean } & Record<IndexSignalNumericField, number>
>;

export type IndexWeightsRow = {
  symbol: string;
  short_name: string;
  weight: number;
  basket_share: number | null;
};

export type IndexWeightsRefreshStatus = {
  ok: boolean;
  source: string | null;
  rows: number;
  errors: string[];
  at: number;
};

export type IndexWeightsOverview = {
  label: IndexLabel;
  source: string;
  as_of: string;
  fetched_at: number | null;
  universe_size: number;
  tracked: IndexWeightsRow[];
  unresolved: string[];
  last_refresh: IndexWeightsRefreshStatus | null;
};

export type IndexSignalWeightsResponse = {
  refreshing: boolean;
  top_n: number;
  indices: Record<IndexLabel, IndexWeightsOverview>;
};

/** One report cell (backend `shadow_log._cell_summary`). Hit-rate fields are null for neutral. */
export type ShadowBucket = {
  n: number;
  /** Readings at least a horizon apart — the count the 95% range is taken from. */
  n_independent: number;
  /** Of those, the ones whose move cleared the minimum either way — the count behind the range. */
  n_independent_decisive: number;
  ups: number;
  downs: number;
  /** Moves smaller than the minimum move: neither a hit nor a miss. */
  flats: number;
  mean_return_bps: number;
  hit_rate: number | null;
  hit_rate_low: number | null;
  hit_rate_high: number | null;
  /** Hit rate minus how often the index moved that way after any reading (baseline). */
  edge_hit: number | null;
  /** Mean move over the baseline's, in the called direction (positive = helped). */
  edge_bps: number | null;
  verdict: "better" | "worse" | "unclear" | null;
};

export type ShadowBaseline = {
  n: number;
  mean_return_bps: number | null;
  up_share: number | null;
  down_share: number | null;
};

export type ShadowExcluded = { no_level: number; day_end: number; gap: number };

/** The breakeven move (backend `index_signal/breakeven.py`): the Trading Costs round trip on one
 * at-the-money lot, at the last session's premium, as an index move. `premium` is null when the
 * bhavcopy has no price (the cost is then the flat charges alone); `points`/`bps` are null until
 * the lot size and an index level are known. Excludes the bid-ask spread. */
export type ShadowBreakeven = {
  cost_rupees: number | null;
  premium: number | null;
  lot_size: number | null;
  delta: number;
  index_level: number | null;
  points: number | null;
  bps: number | null;
};

/** Keys of the per-horizon records are horizon seconds as strings ("60", "300", "900"). */
export type ShadowReport = {
  label: SignalLabel;
  days: number;
  /** The minimum move actually used — the breakeven when none was asked for. */
  min_move_bps: number;
  breakeven: ShadowBreakeven;
  samples: number;
  transitions: number;
  flips: number;
  forward_returns: Record<string, Record<string, ShadowBucket>>;
  flip_returns: Record<string, Record<string, ShadowBucket>>;
  baseline: Record<string, ShadowBaseline>;
  excluded: Record<string, ShadowExcluded>;
};

export type IndexSignalShadowReportResponse = {
  days: number;
  /** Null when each index scored against its own breakeven. */
  min_move_bps: number | null;
  indices: Record<IndexLabel, ShadowReport>;
  /** Every non-incumbent mechanism's report, keyed by shadow-log label (`nifty:expansion`). */
  mechanisms?: Record<string, ShadowReport>;
};

/** One way of reading direction. Each gets its own tab on the signals page. */
export type MechanismKey = "wobi" | "flow" | "expansion";

/** A shadow-log label: the index alone for W-OBI, else `<index>:<mechanism>`. */
export type SignalLabel = IndexLabel | `${IndexLabel}:flow` | `${IndexLabel}:expansion` | `${IndexLabel}:expansion:backtest`;

export function mechanismLabel(mechanism: MechanismKey, index: IndexLabel): SignalLabel {
  return mechanism === "wobi" ? index : (`${index}:${mechanism}` as SignalLabel);
}

export type MechanismSpec = {
  key: MechanismKey;
  name: string;
  summary: string;
  /** Replayable on `get_historical_data_v2` bars: needs no order book and no quotes. */
  backtestable: boolean;
};

export const MECHANISMS: readonly MechanismSpec[] = [
  {
    key: "expansion",
    name: "Volume expansion",
    summary:
      "Calls a side when a price move and the trading behind it are both unusually large for the day. On NIFTY, open interest must also show new positions being opened, not old ones closed — which separates a breakout from a blow-off. SENSEX has no open interest from ICICI, so its version cannot tell those two apart.",
    backtestable: true,
  },
  {
    key: "flow",
    name: "Order flow",
    summary:
      "Reads how orders and trades are changing rather than how much is waiting: NIFTY from the futures contract's own buying and selling pressure, SENSEX from how its big stocks' queues move. Needs live quotes, so it cannot be replayed on history.",
    backtestable: false,
  },
  {
    key: "wobi",
    name: "Order-book imbalance",
    summary:
      "Compares how much is queued to buy against how much is queued to sell across each index's heaviest stocks. Needs live order books, so it cannot be replayed on history.",
    backtestable: false,
  },
];

export type SignalFlip = {
  ts: number;
  time_ist: string;
  state: "bullish" | "bearish";
  level: number | null;
  move_5m_bps: number | null;
  move_15m_bps: number | null;
  /** Did the index go the called way by at least the bar? Null when there is no outcome yet. */
  right_5m: boolean | null;
  right_15m: boolean | null;
  missing_5m?: string;
  missing_15m?: string;
};

export type SignalFlipsResponse = {
  label: SignalLabel;
  days: number;
  min_move_bps: number;
  flips: SignalFlip[];
};

export type ExpansionBacktestSummary = {
  label: string;
  bars: number;
  readings: number;
  days: number;
  from?: string | null;
  to?: string | null;
  states?: Record<string, number>;
  directional_pct?: number;
  verdict?: "no_data";
  message?: string;
};

/** The last backtest of the expansion mechanism: its range, what stopped a fetch, and per index. */
export type ExpansionBacktestRun = {
  period: string;
  from: string;
  to: string;
  finished_at: string;
  calls: number;
  notes: string[];
  indices: Partial<Record<IndexLabel, { summary: ExpansionBacktestSummary; readiness?: IndexReadiness }>>;
  /** Days back from today that reach the start of the range, for the flip list. */
  flip_days: number;
};

export type ReadinessStatus = "ready" | "too_early" | "no_edge" | "worse";
export type CallStatus = "better" | "worse" | "no_edge" | "too_early" | "no_calls";

/** Flips into one side, judged at one horizon (backend `shadow_log._call_view`). */
export type ReadinessCall = {
  status: CallStatus;
  right: number;
  calls: number;
  /** Calls at least a horizon apart with a move past the breakeven — the evidence count. */
  separate_calls: number;
  hit_rate: number | null;
  hit_rate_low: number | null;
  hit_rate_high: number | null;
  /** How often the index went the called way after any reading: what the calls must beat. */
  trend_share: number | null;
};

/** The fixed scalping test (backend `shadow_log.readiness`): flips, at +5 min, against the breakeven. */
export type IndexReadiness = {
  /** The shadow-log label this verdict judged. */
  label: SignalLabel;
  status: ReadinessStatus;
  lookback_days: number;
  scalp_horizon_seconds: number;
  hold_horizon_seconds: number;
  min_move_bps: number;
  breakeven: ShadowBreakeven;
  directions: Record<"bullish" | "bearish", { scalp: ReadinessCall; hold: ReadinessCall }>;
  sessions: number;
  up_days: number;
  down_days: number;
  flips: number;
  /** Flips the signal let go of within the scalp horizon. */
  dropped_quickly: number;
  requirements: { separate_calls: number; sessions: number; up_days: number; down_days: number };
};

export type IndexSignalReadinessResponse = {
  indices: Record<IndexLabel, IndexReadiness>;
  /** Shadow-only order-flow challengers, judged by the same fixed test (backend `index_signal.flow`). */
  challengers?: Record<IndexLabel, IndexReadiness & { name: string }>;
  /** The price/volume/OI mechanism. `published` says whether the navbar shows it. */
  expansion?: Record<
    IndexLabel,
    IndexReadiness & { name: string; requires_oi: boolean; published: boolean }
  >;
};

export const INDEX_SIGNAL_PREFERENCES_QUERY_KEY = ["settings", "index-signal-preferences"] as const;
export const INDEX_SIGNAL_WEIGHTS_QUERY_KEY = ["settings", "index-signal-weights"] as const;
export const INDEX_SIGNAL_SHADOW_REPORT_QUERY_KEY = ["settings", "index-signal-shadow-report"] as const;
/** Under the report's key, so invalidating the report refreshes the verdict too. */
export const INDEX_SIGNAL_READINESS_QUERY_KEY = [...INDEX_SIGNAL_SHADOW_REPORT_QUERY_KEY, "readiness"] as const;

export const WEIGHTS_SOURCE_LABEL: Record<string, string> = {
  nse_api: "NSE · live free-float",
  bse_api: "BSE · live free-float",
  niftyindices_factsheet: "niftyindices factsheet (monthly)",
  seed: "Built-in seed",
};

export function fetchIndexSignalPreferences(): Promise<IndexSignalPreferences> {
  return apiClient.get<IndexSignalPreferences>("/api/settings/index-signal/preferences");
}

export function saveIndexSignalPreferences(
  body: IndexSignalPreferencesUpdate,
): Promise<IndexSignalPreferences> {
  return apiClient.put<IndexSignalPreferences>("/api/settings/index-signal/preferences", body);
}

export function fetchIndexSignalWeights(): Promise<IndexSignalWeightsResponse> {
  return apiClient.get<IndexSignalWeightsResponse>("/api/settings/index-signal/weights");
}

export function refreshIndexSignalWeights(): Promise<{ started: boolean; refreshing: boolean }> {
  return apiClient.post<{ started: boolean; refreshing: boolean }>(
    "/api/settings/index-signal/weights/refresh",
    {},
  );
}

/** `minMoveBps` null scores each index against its breakeven move. */
export function fetchIndexSignalShadowReport(
  days: number,
  minMoveBps: number | null,
): Promise<IndexSignalShadowReportResponse> {
  const params = new URLSearchParams({ days: String(days) });
  if (minMoveBps != null) params.set("min_move_bps", String(minMoveBps));
  return apiClient.get<IndexSignalShadowReportResponse>(
    `/api/settings/index-signal/shadow-report?${params.toString()}`,
  );
}

export const INDEX_SIGNAL_FLIPS_QUERY_KEY = [...INDEX_SIGNAL_SHADOW_REPORT_QUERY_KEY, "flips"] as const;

export function fetchIndexSignalFlips(label: SignalLabel, days: number): Promise<SignalFlipsResponse> {
  const params = new URLSearchParams({ label, days: String(days) });
  return apiClient.get<SignalFlipsResponse>(`/api/settings/index-signal/flips?${params.toString()}`);
}

export const EXPANSION_BACKTEST_QUERY_KEY = [...INDEX_SIGNAL_SHADOW_REPORT_QUERY_KEY, "expansion-backtest"] as const;

/** Starts the shared backtest job: fetches missing ICICI history, then replays both indices. */
export function startExpansionBacktest(body: { period: BacktestPeriod; from_date?: string; to_date?: string }): Promise<BacktestJob> {
  return apiClient.post<BacktestJob>("/api/settings/index-signal/expansion/backtest", body);
}

export function fetchExpansionLastBacktest(): Promise<{ run: ExpansionBacktestRun | null }> {
  return apiClient.get<{ run: ExpansionBacktestRun | null }>("/api/settings/index-signal/expansion/backtest");
}

export function fetchIndexSignalReadiness(): Promise<IndexSignalReadinessResponse> {
  return apiClient.get<IndexSignalReadinessResponse>("/api/settings/index-signal/readiness");
}

async function triggerBlobDownload(res: Response, fallbackFilename: string): Promise<void> {
  const blob = await res.blob();
  const disposition = res.headers.get("content-disposition") ?? "";
  const match = /filename="?([^";\n]+)"?/i.exec(disposition);
  const filename = match?.[1] ?? fallbackFilename;
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = filename;
  anchor.rel = "noopener";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(objectUrl);
}

/** The minute readings behind the shadow report, as CSV for Excel or a charting tool. */
export async function downloadIndexSignalReadings(label: SignalLabel, days: number): Promise<void> {
  const url = new URL("/api/settings/index-signal/readings/download", getBackendBaseUrl());
  url.searchParams.set("index", label);
  url.searchParams.set("days", String(days));
  const res = await fetch(url.toString(), { method: "GET", credentials: "include" });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || "Could not download the readings");
  }
  await triggerBlobDownload(res, `${label.replaceAll(":", "-")}-signal-readings-${days}d.csv`);
}
