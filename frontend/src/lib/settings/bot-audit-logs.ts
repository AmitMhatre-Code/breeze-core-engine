import { apiClient } from "@/lib/api-client";
import { getBackendBaseUrl } from "@/lib/config";

export type BotAuditLogItem = {
  /** Server-side filename; also the id used to download this file. */
  name: string;
  bot_type: string;
  /** IST trading day this file describes, `YYYY-MM-DD`. */
  trading_date: string;
  size_bytes: number;
  /** Lines in the file — one per driver pass inside a session window. */
  records: number;
};

export type BotAuditLogIndex = {
  user_id: string;
  retention_days: number;
  logs: BotAuditLogItem[];
};

export async function fetchBotAuditLogIndex(): Promise<BotAuditLogIndex> {
  return apiClient.get<BotAuditLogIndex>("/api/settings/bot-audit-logs");
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

/** Download every retained bot audit file as one ZIP. */
export async function downloadAllBotAuditLogs(): Promise<void> {
  const url = new URL("/api/settings/bot-audit-logs/download", getBackendBaseUrl());
  const res = await fetch(url.toString(), { method: "GET", credentials: "include" });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || "Failed to download bot audit logs");
  }
  await triggerBlobDownload(res, "bot-audit.zip");
}

/** Download one bot/day audit file as JSONL. */
export async function downloadBotAuditLog(name: string): Promise<void> {
  const url = new URL(
    `/api/settings/bot-audit-logs/${encodeURIComponent(name)}/download`,
    getBackendBaseUrl(),
  );
  const res = await fetch(url.toString(), { method: "GET", credentials: "include" });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || "Failed to download bot audit log");
  }
  await triggerBlobDownload(res, name);
}
