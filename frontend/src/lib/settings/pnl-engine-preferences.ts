import { apiClient } from "@/lib/api-client";

export type PnlEnginePreferences = {
  quote_flush_interval_seconds: number;
  pnl_recompute_interval_seconds: number;
  quote_flush_min_seconds: number;
  quote_flush_max_seconds: number;
  quote_flush_recommended_min_seconds: number;
  quote_flush_recommended_max_seconds: number;
  pnl_recompute_min_seconds: number;
  pnl_recompute_max_seconds: number;
  pnl_recompute_recommended_min_seconds: number;
  pnl_recompute_recommended_max_seconds: number;
};

export const PNL_ENGINE_PREFERENCES_QUERY_KEY = ["settings", "pnl-engine-preferences"] as const;

export async function fetchPnlEnginePreferences(): Promise<PnlEnginePreferences> {
  return apiClient.get<PnlEnginePreferences>("/api/settings/pnl-engine/preferences");
}
