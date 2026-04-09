import { apiClient } from "@/lib/api-client";

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

export type AiProviderState = {
  user_id: string;
  configured: boolean;
  enabled: boolean;
  provider?: "gemini" | "openai" | null;
  model?: string | null;
  masked_api_key?: string | null;
  english_only: boolean;
  disclaimer: string;
  message?: string | null;
};

export type OutlookFeedConfig = {
  name: string;
  url: string;
};

export type OutlookConfigState = {
  user_id: string;
  feeds: OutlookFeedConfig[];
  prompt_template: string;
  system_prompt: string;
  using_default_feeds: boolean;
  using_default_prompt: boolean;
  using_default_system_prompt: boolean;
  message?: string | null;
};

export function getAiProviderState() {
  return apiClient.get<AiProviderState>("/api/settings/ai-provider");
}

export function saveAiProviderSettings(body: {
  provider: "gemini" | "openai";
  api_key: string;
  model?: string;
  enabled?: boolean;
}) {
  return apiClient.put<AiProviderState, typeof body>("/api/settings/ai-provider", body);
}

export function testAiProviderSettings(body: {
  provider: "gemini" | "openai";
  api_key: string;
  model?: string;
}) {
  return apiClient.post<{ ok: boolean; message: string }>(
    "/api/settings/ai-provider/test",
    body,
  );
}

export function revokeAiProviderSettings() {
  return apiClient.delete<{ ok: boolean; message: string }>("/api/settings/ai-provider");
}

export function getOutlookConfig() {
  return apiClient.get<OutlookConfigState>("/api/settings/outlook-config");
}

export function saveOutlookConfig(body: {
  feeds: OutlookFeedConfig[];
  prompt_template: string;
  system_prompt: string;
}) {
  return apiClient.put<OutlookConfigState, typeof body>("/api/settings/outlook-config", body);
}

export function resetOutlookConfig(body: {
  reset_feeds: boolean;
  reset_prompt: boolean;
  reset_system_prompt?: boolean;
}) {
  return apiClient.post<OutlookConfigState, typeof body>("/api/settings/outlook-config/reset", body);
}

export function getMarketOutlook(forceRefresh = false) {
  const suffix = forceRefresh ? "?force_refresh=true" : "";
  return apiClient.get<OutlookResponse>(`/api/outlook/market${suffix}`);
}

export type OutlookStreamError = {
  message: string;
  error_code?: string;
  status_code?: number;
};

export function subscribeMarketOutlookStream(handlers: {
  onOutlook: (payload: OutlookResponse) => void;
  onStreamError: (payload: OutlookStreamError) => void;
  onTransportError?: () => void;
}) {
  const stream = new EventSource("/api/outlook/market/stream");
  stream.addEventListener("outlook", (event) => {
    try {
      handlers.onOutlook(JSON.parse((event as MessageEvent).data) as OutlookResponse);
    } catch {
      handlers.onStreamError({ message: "Invalid market outlook stream payload." });
    }
  });
  stream.addEventListener("error", (event) => {
    // Backend semantic error event with JSON payload.
    const msgEvent = event as MessageEvent;
    if (typeof msgEvent.data === "string" && msgEvent.data) {
      try {
        handlers.onStreamError(JSON.parse(msgEvent.data) as OutlookStreamError);
        return;
      } catch {
        // Fall through to transport handler.
      }
    }
    handlers.onTransportError?.();
  });
  stream.onerror = () => {
    handlers.onTransportError?.();
  };
  return () => stream.close();
}
