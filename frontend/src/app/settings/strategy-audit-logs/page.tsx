"use client";

import { useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";

import { AppShell } from "@/components/layout/AppShell";
import { AsyncLabelSpan } from "@/components/ui/AsyncLabelSpan";
import {
  downloadAllStrategyAuditLogs,
  downloadStrategyAuditLog,
  fetchStrategyAuditLogIndex,
  type StrategyAuditLogItem,
} from "@/lib/settings/strategy-audit-logs";

const MONTH_SHORT = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
] as const;

function formatFinishedAt(value: string | null | undefined): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  const day = String(d.getDate()).padStart(2, "0");
  const month = MONTH_SHORT[d.getMonth()];
  const year = d.getFullYear();
  const time = d.toLocaleTimeString(undefined, {
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
  });
  return `${day}-${month}-${year}, ${time}`;
}

function formatLacs(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  return `${value} L`;
}

function formatPop(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  return `${value}%`;
}

function formatYesNo(value: boolean | null | undefined): string {
  if (value == null) return "—";
  return value ? "Yes" : "No";
}

function formatLabel(value: string | null | undefined): string {
  if (!value) return "—";
  return value.charAt(0).toUpperCase() + value.slice(1);
}

function formatStrategyCategory(value: string | null | undefined): string {
  if (!value) return "—";
  const labels: Record<string, string> = {
    income: "Income",
    bullish: "Bullish",
    bearish: "Bearish",
  };
  return labels[value] ?? formatLabel(value);
}

export default function StrategyAuditLogsSettingsPage() {
  const [downloadingAll, setDownloadingAll] = useState(false);
  const [downloadingSessionId, setDownloadingSessionId] = useState<string | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);

  const q = useQuery({
    queryKey: ["settings", "strategy-builder-audit-logs"],
    queryFn: fetchStrategyAuditLogIndex,
  });

  const logs = q.data?.logs ?? [];
  const maxLogs = q.data?.max_logs ?? 10;

  const handleDownloadOne = async (row: StrategyAuditLogItem) => {
    if (!row.session_id) return;
    setDownloadError(null);
    setDownloadingSessionId(row.session_id);
    try {
      await downloadStrategyAuditLog(row.session_id);
    } catch (e) {
      setDownloadError(
        e instanceof Error ? e.message : "Failed to download audit log",
      );
    } finally {
      setDownloadingSessionId(null);
    }
  };

  return (
    <AppShell>
      <section className="app-card space-y-4 p-4">
        <Link href="/settings" className="app-link inline-block text-xs">
          Back to Settings
        </Link>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="text-xl app-text-heading">Strategy Builder Audit Logs</h2>
            <p className="mt-1 text-sm app-text-muted">
              Up to {maxLogs} recent propose-trades audit logs are stored on your server
              (persistent data volume). Older logs are removed automatically when a new log is saved.
            </p>
          </div>
          <button
            type="button"
            className="app-btn-primary inline-flex min-h-[2.25rem] items-center rounded-lg px-4 py-2 text-sm font-medium shadow-sm transition-shadow hover:shadow-md disabled:shadow-none"
            disabled={downloadingAll || q.isLoading || logs.length === 0}
            aria-busy={downloadingAll}
            onClick={async () => {
              setDownloadError(null);
              setDownloadingAll(true);
              try {
                await downloadAllStrategyAuditLogs();
              } catch (e) {
                setDownloadError(
                  e instanceof Error ? e.message : "Failed to download audit logs",
                );
              } finally {
                setDownloadingAll(false);
              }
            }}
          >
            <AsyncLabelSpan
              busy={downloadingAll}
              idleLabel="Download all as ZIP"
              busyLabel="Downloading…"
            />
          </button>
        </div>

        {q.isLoading ? (
          <p className="text-sm app-text-muted">Loading audit logs…</p>
        ) : null}
        {q.error ? (
          <p className="text-sm text-red-600 dark:text-red-400">
            {q.error instanceof Error ? q.error.message : "Could not load audit logs"}
          </p>
        ) : null}
        {downloadError ? (
          <p className="text-sm text-red-600 dark:text-red-400">{downloadError}</p>
        ) : null}

        {!q.isLoading && !q.error && logs.length === 0 ? (
          <p className="text-sm app-text-muted">
            No audit logs yet. Run Strategy Builder (New) propose-trades to generate logs.
          </p>
        ) : null}

        {logs.length > 0 ? (
          <div className="overflow-x-auto rounded-lg border border-zinc-200/90 dark:border-zinc-800">
            <table className="min-w-full text-left text-sm">
              <thead className="bg-zinc-50/90 text-xs uppercase tracking-wide text-zinc-600 dark:bg-zinc-900/60 dark:text-zinc-400">
                <tr>
                  <th className="px-3 py-2 font-medium whitespace-nowrap">Finished</th>
                  <th className="px-3 py-2 font-medium whitespace-nowrap">Scrip</th>
                  <th className="px-3 py-2 font-medium whitespace-nowrap">Expiry</th>
                  <th className="px-3 py-2 font-medium whitespace-nowrap">Strategy</th>
                  <th className="px-3 py-2 font-medium whitespace-nowrap">PoP</th>
                  <th className="px-3 py-2 font-medium whitespace-nowrap">Margin</th>
                  <th className="px-3 py-2 font-medium whitespace-nowrap">Loss</th>
                  <th className="px-3 py-2 font-medium whitespace-nowrap">ELM</th>
                  <th className="px-3 py-2 font-medium whitespace-nowrap">RR Profile</th>
                  <th className="px-3 py-2 font-medium whitespace-nowrap">Events</th>
                  <th className="px-3 py-2 font-medium whitespace-nowrap">Session</th>
                  <th className="px-3 py-2 font-medium whitespace-nowrap">Download</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-200/80 dark:divide-zinc-800">
                {logs.map((row) => {
                  const sessionId = row.session_id ?? row.filename;
                  const isDownloading = downloadingSessionId === row.session_id;
                  return (
                    <tr key={sessionId}>
                      <td className="px-3 py-2 tabular-nums whitespace-nowrap">
                        {formatFinishedAt(row.finished_at ?? row.started_at)}
                      </td>
                      <td className="px-3 py-2 whitespace-nowrap">{row.stock_code ?? "—"}</td>
                      <td className="px-3 py-2 whitespace-nowrap">{row.expiry_date ?? "—"}</td>
                      <td className="px-3 py-2 whitespace-nowrap">
                        {formatStrategyCategory(row.strategy_category)}
                      </td>
                      <td className="px-3 py-2 tabular-nums whitespace-nowrap">
                        {formatPop(row.min_pop_pct)}
                      </td>
                      <td className="px-3 py-2 tabular-nums whitespace-nowrap">
                        {formatLacs(row.margin_lacs)}
                      </td>
                      <td className="px-3 py-2 tabular-nums whitespace-nowrap">
                        {formatLacs(row.max_loss_lacs)}
                      </td>
                      <td className="px-3 py-2 whitespace-nowrap">
                        {formatYesNo(row.provision_elm)}
                      </td>
                      <td className="px-3 py-2 whitespace-nowrap">
                        {formatLabel(row.risk_reward_profile)}
                      </td>
                      <td className="px-3 py-2 tabular-nums whitespace-nowrap">
                        {row.event_count ?? "—"}
                      </td>
                      <td className="px-3 py-2 font-mono text-xs text-zinc-600 dark:text-zinc-400 whitespace-nowrap">
                        {row.session_id ? `${row.session_id.slice(0, 8)}…` : "—"}
                      </td>
                      <td className="px-3 py-2 whitespace-nowrap">
                        <button
                          type="button"
                          className="app-link text-xs font-medium disabled:cursor-wait disabled:opacity-60"
                          disabled={!row.session_id || isDownloading || downloadingAll}
                          aria-busy={isDownloading}
                          onClick={() => void handleDownloadOne(row)}
                        >
                          {isDownloading ? "Downloading…" : "JSON"}
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : null}
      </section>
    </AppShell>
  );
}
