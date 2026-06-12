import { apiClient } from "@/lib/api-client";
import { getBackendBaseUrl } from "@/lib/config";

export type StrategyAuditLogItem = {
  session_id?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  stock_code?: string | null;
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
  const blob = await res.blob();
  const disposition = res.headers.get("content-disposition") ?? "";
  const match = /filename="?([^";\n]+)"?/i.exec(disposition);
  const filename = match?.[1] ?? "strategy-builder-audits.zip";
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
