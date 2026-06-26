import { apiClient } from "@/lib/api-client";
import { getBackendBaseUrl } from "@/lib/config";
import type {
  ChainApiResponse,
  ProposeTradesApiResponse,
  ProposeTradesJobStartResponse,
  ProposeTradesJobStatusResponse,
  RiskRewardProfile,
  StrategyCategory,
} from "@/lib/strategy-builder/types";

export async function fetchStrategyBuilderChain(
  params: {
    stock_code: string;
    expiry_date: string;
    exchange_code: string;
  },
  signal?: AbortSignal,
): Promise<ChainApiResponse> {
  const q = new URLSearchParams({
    stock_code: params.stock_code,
    expiry_date: params.expiry_date,
    exchange_code: params.exchange_code,
  });
  return apiClient.get<ChainApiResponse>(
    `/strategy-builder/chain?${q.toString()}`,
    signal,
  );
}

export type ProposeTradesParams = {
  exchange_code: string;
  stock_code: string;
  expiry_date: string;
  margin_lacs: number;
  max_loss_lacs?: number;
  allow_infinite_loss?: boolean;
  min_pop_pct: number;
  min_ann_return_pct?: number;
  provision_elm: boolean;
  strategy_category: StrategyCategory;
  risk_reward_profile?: RiskRewardProfile;
  audit_detail_level?: "summary" | "debug";
};

export async function startProposeTradesJob(
  params: ProposeTradesParams,
): Promise<ProposeTradesJobStartResponse> {
  return apiClient.post<ProposeTradesJobStartResponse>(
    "/strategy-builder/propose-trades",
    params,
  );
}

export async function getProposeTradesJobStatus(
  jobId: string,
  signal?: AbortSignal,
): Promise<ProposeTradesJobStatusResponse> {
  return apiClient.get<ProposeTradesJobStatusResponse>(
    `/strategy-builder/propose-trades/jobs/${encodeURIComponent(jobId)}`,
    signal,
  );
}

/** @deprecated Use startProposeTradesJob + getProposeTradesJobStatus polling instead. */
export async function proposeTrades(
  params: ProposeTradesParams,
): Promise<ProposeTradesApiResponse> {
  return apiClient.post<ProposeTradesApiResponse>(
    "/strategy-builder/propose-trades",
    params,
  );
}

/** Download the audit JSON for a completed Strategy Builder session. */
export async function downloadStrategyBuilderAudit(
  sessionId: string,
): Promise<void> {
  const url = new URL(
    `/strategy-builder/audit/${encodeURIComponent(sessionId)}`,
    getBackendBaseUrl(),
  );
  const res = await fetch(url.toString(), { method: "GET", credentials: "include" });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || "Failed to download audit log");
  }
  const blob = await res.blob();
  const disposition = res.headers.get("content-disposition") ?? "";
  const match = /filename="?([^";\n]+)"?/i.exec(disposition);
  const filename = match?.[1] ?? `strategy-builder-audit-${sessionId.slice(0, 8)}.json`;
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
