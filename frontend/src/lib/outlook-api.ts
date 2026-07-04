import { apiClient, ApiHttpError } from "@/lib/api-client";

export type OutlookSource = {
  title: string;
  url: string;
  publisher: string;
  published_at?: string | null;
};

export type OutlookInference = {
  volatility_view: string;
  movement_scenarios: string[];
  confidence: "low" | "medium" | "high";
  caveats: string[];
};

export type OutlookStrategyIdea = {
  tag: string;
  rationale: string;
  risk_note: string;
};

export type OutlookWarning = {
  error_code: string;
  message: string;
  stale_response_served: boolean;
  upstream_status?: number | null;
};

export type OutlookResponse = {
  outlook_type: "market";
  as_of: string;
  english_only: boolean;
  disclaimer: string;
  summary: string[];
  inference: OutlookInference;
  strategy_ideas: OutlookStrategyIdea[];
  sources: OutlookSource[];
  warning?: OutlookWarning;
};

export function getMarketOutlook(forceRefresh = false) {
  const suffix = forceRefresh ? "?force_refresh=true" : "";
  return apiClient.get<OutlookResponse>(`/api/outlook/market${suffix}`);
}

/** User-visible copy for outlook fetch failures. */
export function outlookFetchErrorMessage(err: unknown): string {
  if (!(err instanceof ApiHttpError)) {
    return err instanceof Error ? err.message : "Could not load market outlook.";
  }
  return err.message;
}
