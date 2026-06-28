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

export type BreezeApiWsStatus = {
  ok?: boolean;
  response?: unknown;
  connected?: boolean;
  user_id?: string | null;
  active_subscriptions?: number;
  subscription_keys?: string[];
  ts?: number;
};

export function wsConnectPlayground() {
  return apiClient.post<BreezeApiWsStatus>(`${BASE}/ws/connect`, {});
}

export function wsDisconnectPlayground() {
  return apiClient.post<BreezeApiWsStatus>(`${BASE}/ws/disconnect`, {});
}

export function wsGetPlaygroundStatus() {
  return apiClient.get<BreezeApiWsStatus>(`${BASE}/ws/status`);
}

export function wsSubscribePlayground(params: {
  exchange_code: string;
  stock_code: string;
  expiry_date: string;
  strike_price: string;
  right: string;
}) {
  return apiClient.post<BreezeApiWsStatus, typeof params>(`${BASE}/ws/subscribe`, params);
}

export function wsStreamUrl(): string {
  const base = process.env.NEXT_PUBLIC_BACKEND_URL || "";
  if (base) return `${base.replace(/\/$/, "")}${BASE}/ws/stream`;
  return `${BASE}/ws/stream`;
}
