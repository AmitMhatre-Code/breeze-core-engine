"use client";

import { useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";

import { AppShell } from "@/components/layout/AppShell";
import { AsyncLabelSpan } from "@/components/ui/AsyncLabelSpan";
import {
  downloadAllStrategyAuditLogs,
  fetchStrategyAuditLogIndex,
} from "@/lib/settings/strategy-audit-logs";

function formatTimestamp(value: string | null | undefined): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString();
}

export default function StrategyAuditLogsSettingsPage() {
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState<string | null>(null);

  const q = useQuery({
    queryKey: ["settings", "strategy-builder-audit-logs"],
    queryFn: fetchStrategyAuditLogIndex,
  });

  const logs = q.data?.logs ?? [];
  const maxLogs = q.data?.max_logs ?? 10;

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
            disabled={downloading || q.isLoading || logs.length === 0}
            aria-busy={downloading}
            onClick={async () => {
              setDownloadError(null);
              setDownloading(true);
              try {
                await downloadAllStrategyAuditLogs();
              } catch (e) {
                setDownloadError(
                  e instanceof Error ? e.message : "Failed to download audit logs",
                );
              } finally {
                setDownloading(false);
              }
            }}
          >
            <AsyncLabelSpan
              busy={downloading}
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
                  <th className="px-3 py-2 font-medium">Finished</th>
                  <th className="px-3 py-2 font-medium">Stock</th>
                  <th className="px-3 py-2 font-medium">Events</th>
                  <th className="px-3 py-2 font-medium">Session</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-200/80 dark:divide-zinc-800">
                {logs.map((row) => (
                  <tr key={row.session_id ?? row.filename}>
                    <td className="px-3 py-2 tabular-nums">
                      {formatTimestamp(row.finished_at ?? row.started_at)}
                    </td>
                    <td className="px-3 py-2">{row.stock_code ?? "—"}</td>
                    <td className="px-3 py-2 tabular-nums">{row.event_count ?? "—"}</td>
                    <td className="px-3 py-2 font-mono text-xs text-zinc-600 dark:text-zinc-400">
                      {row.session_id ? `${row.session_id.slice(0, 8)}…` : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </section>
    </AppShell>
  );
}
