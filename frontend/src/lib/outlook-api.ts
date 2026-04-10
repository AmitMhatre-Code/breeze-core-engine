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

export type AiProviderHealthEntry = {
  ok: boolean;
  message?: string | null;
  checked_at?: string | null;
};

export type AiProviderSideState = {
  provider: string;
  configured: boolean;
  enabled: boolean;
  model?: string | null;
  fallback_models: string[];
  masked_api_key?: string | null;
  models_working: number;
  models_failing: number;
  last_model_health_at?: string | null;
  model_health: Record<string, AiProviderHealthEntry>;
};

export type AiProviderState = {
  user_id: string;
  gemini: AiProviderSideState;
  openai: AiProviderSideState;
  outlook_ai_provider?: "gemini" | "openai" | null;
  english_only: boolean;
  disclaimer: string;
  message?: string | null;
};

export const OPENAI_MODEL_OPTIONS = ["gpt-4o-mini", "gpt-4o"] as const;

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
  fallback_models?: string[];
  enabled?: boolean;
}) {
  return apiClient.put<AiProviderState, typeof body>("/api/settings/ai-provider", body);
}

export function patchAiProviderSettings(body: {
  provider: "gemini" | "openai";
  model?: string;
  fallback_models?: string[];
}) {
  return apiClient.patch<AiProviderState, typeof body>("/api/settings/ai-provider", body);
}

export function setOutlookAiProvider(body: { provider: "gemini" | "openai" }) {
  return apiClient.put<AiProviderState, typeof body>(
    "/api/settings/ai-provider/outlook-provider",
    body,
  );
}

export function testAiProviderSettings(body: {
  provider: "gemini" | "openai";
  api_key: string;
  model?: string;
  fallback_models?: string[];
}) {
  return apiClient.post<{
    ok: boolean;
    message: string;
    results: { model: string; ok: boolean; status_code?: number; message?: string | null }[];
  }>(
    "/api/settings/ai-provider/test",
    body,
  );
}

export function testAiProviderModel(body: { provider: "gemini" | "openai"; model: string }) {
  return apiClient.post<{
    ok: boolean;
    message: string;
    results: { model: string; ok: boolean; status_code?: number; message?: string | null }[];
  }>("/api/settings/ai-provider/test-model", body);
}

export function getGeminiModels() {
  return apiClient.get<{
    provider: "gemini";
    available_models: { model: string; status: string; message?: string | null }[];
    stale_models: { model: string; status: string; message?: string | null }[];
    last_refreshed_at?: string | null;
    last_health_check_at?: string | null;
  }>("/api/settings/ai-provider/models?provider=gemini");
}

export function deleteAiProvider(provider: "gemini" | "openai") {
  const q = new URLSearchParams({ provider });
  return apiClient.delete<{ ok: boolean; message: string }>(
    `/api/settings/ai-provider?${q.toString()}`,
  );
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

/** User-visible copy for outlook fetch failures (includes refresh errors). */
export function outlookFetchErrorMessage(err: unknown): string {
  if (!(err instanceof ApiHttpError)) {
    return err instanceof Error ? err.message : "Could not load market outlook.";
  }
  const base = err.message;
  const payload = err.payload as { detail?: { error_code?: string } } | undefined;
  if (
    payload?.detail?.error_code === "provider_error" &&
    !base.includes("Settings → AI provider")
  ) {
    return `${base} If this persists, pick a different model (e.g. gemini-2.0-flash) in AI provider settings.`;
  }
  return base;
}
