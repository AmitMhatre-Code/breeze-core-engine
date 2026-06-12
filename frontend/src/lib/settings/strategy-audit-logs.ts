import { apiClient } from "@/lib/api-client";
import { getBackendBaseUrl } from "@/lib/config";

export type StrategyAuditLogItem = {
  session_id?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  stock_code?: string | null;
  expiry_date?: string | null;
  min_pop_pct?: number | null;
  margin_lacs?: number | null;
  max_loss_lacs?: number | null;
  provision_elm?: boolean | null;
  risk_reward_profile?: string | null;
  strategy_category?: string | null;
  event_count?: number | null;
  filename: string;
};

export type StrategyAuditLogIndex = {
  user_id: string;
  max_logs: number;
  logs: StrategyAuditLogItem[];
};

export async function fetchStrategyAuditLogIndex(): Promise<StrategyAuditLogIndex> {
  return apiClient.get<StrategyAuditLogIndex>(
    "/api/settings/strategy-builder-audit-logs",
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

/** Download all retained Strategy Builder audit logs as a ZIP archive. */
export async function downloadAllStrategyAuditLogs(): Promise<void> {
  const url = new URL(
    "/api/settings/strategy-builder-audit-logs/download",
    getBackendBaseUrl(),
  );
  const res = await fetch(url.toString(), { method: "GET", credentials: "include" });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || "Failed to download audit logs");
  }
  await triggerBlobDownload(res, "strategy-builder-audits.zip");
}

/** Download one Strategy Builder audit log JSON by session id. */
export async function downloadStrategyAuditLog(sessionId: string): Promise<void> {
  const url = new URL(
    `/api/settings/strategy-builder-audit-logs/${encodeURIComponent(sessionId)}/download`,
    getBackendBaseUrl(),
  );
  const res = await fetch(url.toString(), { method: "GET", credentials: "include" });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || "Failed to download audit log");
  }
  await triggerBlobDownload(res, `strategy-builder-audit-${sessionId.slice(0, 8)}.json`);
}
