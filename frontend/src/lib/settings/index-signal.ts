import { apiClient } from "@/lib/api-client";
import { getBackendBaseUrl } from "@/lib/config";

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

/** Keys of the per-horizon records are horizon seconds as strings ("60", "300", "900"). */
export type ShadowReport = {
  label: IndexLabel;
  days: number;
  min_move_bps: number;
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
  min_move_bps: number;
  indices: Record<IndexLabel, ShadowReport>;
};

export const INDEX_SIGNAL_PREFERENCES_QUERY_KEY = ["settings", "index-signal-preferences"] as const;
export const INDEX_SIGNAL_WEIGHTS_QUERY_KEY = ["settings", "index-signal-weights"] as const;
export const INDEX_SIGNAL_SHADOW_REPORT_QUERY_KEY = ["settings", "index-signal-shadow-report"] as const;

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

export function fetchIndexSignalShadowReport(
  days: number,
  minMoveBps: number,
): Promise<IndexSignalShadowReportResponse> {
  const params = new URLSearchParams({ days: String(days), min_move_bps: String(minMoveBps) });
  return apiClient.get<IndexSignalShadowReportResponse>(
    `/api/settings/index-signal/shadow-report?${params.toString()}`,
  );
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
export async function downloadIndexSignalReadings(label: IndexLabel, days: number): Promise<void> {
  const url = new URL("/api/settings/index-signal/readings/download", getBackendBaseUrl());
  url.searchParams.set("index", label);
  url.searchParams.set("days", String(days));
  const res = await fetch(url.toString(), { method: "GET", credentials: "include" });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || "Could not download the readings");
  }
  await triggerBlobDownload(res, `${label}-signal-readings-${days}d.csv`);
}
