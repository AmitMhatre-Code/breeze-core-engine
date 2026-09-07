import { apiClient } from "@/lib/api-client";
import { getBackendBaseUrl } from "@/lib/config";

export type MarginHarnessRanking = {
  span_method: string;
  elm_method: string;
  cases: number;
  mean_abs_diff: number;
  mean_abs_pct: number;
  max_abs_diff: number;
  closest_on_cases: number;
};

export type MarginHarnessSummary = {
  ranking?: MarginHarnessRanking[];
  best_combination?: MarginHarnessRanking | null;
  icici_non_span_seen_non_zero?: boolean;
  icici_non_span_sample_count?: number;
};

export type MarginHarnessRun = {
  id: string;
  started_at: string;
  finished_at: string | null;
  status: string;
  case_count: number;
  priced_count: number;
  failed_count: number;
  broker_calls: number;
  error: string | null;
  summary: MarginHarnessSummary;
};

export type MarginHarnessState = {
  running: boolean;
  broker_mode: string;
  runs: MarginHarnessRun[];
};

export async function fetchMarginHarnessRuns(): Promise<MarginHarnessState> {
  return apiClient.get<MarginHarnessState>("/api/settings/margin-harness/runs");
}

export async function startMarginHarnessRun(
  includeOpenPositions: boolean,
): Promise<{ ok: boolean; message: string }> {
  return apiClient.post<{ ok: boolean; message: string }>(
    `/api/settings/margin-harness/run?include_open_positions=${includeOpenPositions}`,
    {},
  );
}

export async function downloadMarginHarnessRun(runId: string): Promise<void> {
  const url = new URL(
    `/api/settings/margin-harness/runs/${encodeURIComponent(runId)}/download`,
    getBackendBaseUrl(),
  );
  const res = await fetch(url.toString(), { method: "GET", credentials: "include" });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || "Failed to download comparison run");
  }
  const blob = await res.blob();
  const disposition = res.headers.get("content-disposition") ?? "";
  const match = /filename="?([^";\n]+)"?/i.exec(disposition);
  const filename = match?.[1] ?? `margin-harness-${runId.slice(0, 8)}.json`;
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
