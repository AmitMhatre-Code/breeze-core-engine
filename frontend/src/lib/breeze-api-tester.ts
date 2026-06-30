import { apiClient } from "@/lib/api-client";

export type BreezeApiParamDef = {
  name: string;
  label: string;
  type: "string" | "json";
  required: boolean;
  placeholder: string;
  help: string;
};

export type BreezeApiCatalogEntry = {
  method: string;
  title: string;
  risk_level: "read" | "trade" | "funds" | "gtt";
  description: string;
  notes: string;
  params: BreezeApiParamDef[];
};

export type BreezeApiCatalogResponse = {
  entries: BreezeApiCatalogEntry[];
};

export type BreezeApiRiskStatus = {
  accepted: boolean;
  accepted_at?: string | null;
};

export type BreezeApiInvokeResponse = {
  ok: boolean;
  method: string;
  duration_ms: number;
  response: unknown;
  error?: string | null;
};

const BASE = "/api/settings/breeze-api-tester";

export function getBreezeApiTesterCatalog() {
  return apiClient.get<BreezeApiCatalogResponse>(`${BASE}/catalog`);
}

export function getBreezeApiTesterRiskStatus() {
  return apiClient.get<BreezeApiRiskStatus>(`${BASE}/risk-status`);
}

export function acknowledgeBreezeApiTesterRisk() {
  return apiClient.post<BreezeApiRiskStatus>(`${BASE}/acknowledge-risk`, {});
}

export function invokeBreezeApiTester(method: string, params: Record<string, string>) {
  return apiClient.post<BreezeApiInvokeResponse, { method: string; params: Record<string, string> }>(
    `${BASE}/invoke`,
    { method, params },
  );
}

export const RISK_GROUP_LABEL: Record<BreezeApiCatalogEntry["risk_level"], string> = {
  read: "Read-only",
  trade: "Trade (orders)",
  funds: "Funds",
  gtt: "GTT",
};

export type BreezeIciciCommand = {
  sdk_method: string;
  sdk_args: Record<string, unknown>;
  side_effects?: string[];
};

export type BreezeApiWsEventLogEntry = {
  id: number;
  ts: number;
  step: string;
  icici_command: BreezeIciciCommand;
  icici_response: unknown;
  ok: boolean;
  note?: string;
};

export type BreezeApiWsStatus = {
  ok?: boolean;
  response?: unknown;
  connected?: boolean;
  user_id?: string | null;
  active_subscriptions?: number;
  subscription_keys?: string[];
  last_error?: string | null;
  icici_command?: BreezeIciciCommand;
  event_id?: number;
  ts?: number;
};

export type BreezeApiWsEventLogResponse = {
  events: BreezeApiWsEventLogEntry[];
};

export function wsConnectPlayground() {
  return apiClient.post<BreezeApiWsStatus>(`${BASE}/ws/connect`, {});
}

export function wsDisconnectPlayground() {
  return apiClient.post<BreezeApiWsStatus>(`${BASE}/ws/disconnect`, {});
}

export function wsReleasePlayground(holderId: string) {
  return apiClient.post<BreezeApiWsStatus, { holder_id: string }>(`${BASE}/ws/release`, {
    holder_id: holderId,
  });
}

export function wsGetPlaygroundStatus() {
  return apiClient.get<BreezeApiWsStatus>(`${BASE}/ws/status`);
}

export function wsGetPlaygroundEventLog() {
  return apiClient.get<BreezeApiWsEventLogResponse>(`${BASE}/ws/event-log`);
}

export function wsSubscribePlayground(params: Record<string, string | boolean | undefined>) {
  return apiClient.post<BreezeApiWsStatus, typeof params>(`${BASE}/ws/subscribe`, params);
}

export function wsStreamUrl(): string {
  const base = process.env.NEXT_PUBLIC_BACKEND_URL || "";
  if (base) return `${base.replace(/\/$/, "")}${BASE}/ws/stream`;
  return `${BASE}/ws/stream`;
}
