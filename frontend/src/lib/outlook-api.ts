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

export type OutlookSummaryCategory = "macro_global" | "macro_local" | "positioning";

export type OutlookSummaryItem = {
  category: OutlookSummaryCategory;
  text: string;
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
  summary: OutlookSummaryItem[];
  inference: OutlookInference;
  strategy_ideas: OutlookStrategyIdea[];
  sources: OutlookSource[];
  warning?: OutlookWarning;
};

/**
 * The portal's summary schema was previously a flat string[]. Deployments upgrade
 * independently of the portal, so this repo's frontend may briefly receive either
 * shape from a not-yet-upgraded portal cache -- normalize defensively rather than
 * assume both sides are always in lockstep.
 */
function normalizeOutlookSummary(raw: unknown): OutlookSummaryItem[] {
  if (!Array.isArray(raw)) return [];
  return raw.map((item) =>
    typeof item === "string" ? { category: "macro_global" as const, text: item } : (item as OutlookSummaryItem),
  );
}

export function getMarketOutlook(forceRefresh = false) {
  const suffix = forceRefresh ? "?force_refresh=true" : "";
  return apiClient.get<OutlookResponse>(`/api/outlook/market${suffix}`).then((data) => ({
    ...data,
    summary: normalizeOutlookSummary(data.summary),
  }));
}

/** User-visible copy for outlook fetch failures. */
export function outlookFetchErrorMessage(err: unknown): string {
  if (!(err instanceof ApiHttpError)) {
    return err instanceof Error ? err.message : "Could not load market outlook.";
  }
  return err.message;
}
