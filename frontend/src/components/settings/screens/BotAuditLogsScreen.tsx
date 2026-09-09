"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { AsyncLabelSpan } from "@/components/ui/AsyncLabelSpan";
import { SettingsScreenHeader } from "@/components/settings/SettingsScreenHeader";
import { BOT_META, type BotType } from "@/lib/use-bots";
import {
  downloadAllBotAuditLogs,
  downloadBotAuditLog,
  fetchBotAuditLogIndex,
  type BotAuditLogItem,
} from "@/lib/settings/bot-audit-logs";

function BotAuditIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M3 3v18h18" />
      <path d="M7 15l3-4 3 2 4-6" />
    </svg>
  );
}

const MONTH_SHORT = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"] as const;

/** `2026-09-08` → `08 Sep 2026`. Parsed by parts, not `new Date`, so the date is not
 *  shifted by the viewer's timezone — these are IST trading days, not instants. */
function formatTradingDate(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (!m) return iso;
  return `${m[3]} ${MONTH_SHORT[Number(m[2]) - 1] ?? m[2]} ${m[1]}`;
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function botTitle(botType: string): string {
  return BOT_META[botType as BotType]?.title ?? botType;
}

export function BotAuditLogsScreen() {
  const [downloadingAll, setDownloadingAll] = useState(false);
  const [downloadingName, setDownloadingName] = useState<string | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);

  const q = useQuery({
    queryKey: ["settings", "bot-audit-logs"],
    queryFn: fetchBotAuditLogIndex,
  });

  const logs = q.data?.logs ?? [];
  const retentionDays = q.data?.retention_days ?? 7;

  const handleDownloadOne = async (row: BotAuditLogItem) => {
    setDownloadError(null);
    setDownloadingName(row.name);
    try {
      await downloadBotAuditLog(row.name);
    } catch (e) {
      setDownloadError(e instanceof Error ? e.message : "Failed to download audit log");
    } finally {
      setDownloadingName(null);
    }
  };

  return (
    <div>
      <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
        <SettingsScreenHeader
          icon={<BotAuditIcon />}
          title="Bot Audit Logs"
          description={
            <>
              Every decision the scalping bots made, kept for {retentionDays} days on your
              data volume. One file per bot per trading day, recorded on each pass while a
              session window is open — this is where a day that produced no trades explains
              which quiet day it was. Files are JSONL: one JSON record per line.
            </>
          }
        />
        <button
          type="button"
          className="app-btn-primary shrink-0"
          disabled={downloadingAll || q.isLoading || logs.length === 0}
          aria-busy={downloadingAll}
          onClick={async () => {
            setDownloadError(null);
            setDownloadingAll(true);
            try {
              await downloadAllBotAuditLogs();
            } catch (e) {
              setDownloadError(e instanceof Error ? e.message : "Failed to download audit logs");
            } finally {
              setDownloadingAll(false);
            }
          }}
        >
          <AsyncLabelSpan busy={downloadingAll} idleLabel="Download all as ZIP" busyLabel="Downloading…" />
        </button>
      </div>

      <section className="app-card space-y-4 p-5">
        {q.isLoading ? <p className="text-sm text-muted">Loading audit logs…</p> : null}
        {q.error ? (
          <p className="app-alert-error text-xs">
            {q.error instanceof Error ? q.error.message : "Could not load audit logs"}
          </p>
        ) : null}
        {downloadError ? <p className="app-alert-error text-xs">{downloadError}</p> : null}

        {!q.isLoading && !q.error && logs.length === 0 ? (
          <p className="text-sm text-muted">
            No bot audit logs yet. They are written while a scalping bot is armed and its
            session window is open.
          </p>
        ) : null}

        {logs.length > 0 ? (
          <div className="app-table-wrap">
            <table className="min-w-[620px] w-full text-left text-table">
              <thead className="app-table-head">
                <tr>
                  <th className="px-2.5 py-2 font-semibold whitespace-nowrap">Trading day</th>
                  <th className="px-2.5 py-2 font-semibold whitespace-nowrap">Bot</th>
                  <th className="px-2.5 py-2 text-right font-semibold whitespace-nowrap">Records</th>
                  <th className="px-2.5 py-2 text-right font-semibold whitespace-nowrap">Size</th>
                  <th className="px-2.5 py-2 font-semibold whitespace-nowrap" />
                </tr>
              </thead>
              <tbody>
                {logs.map((row) => (
                  <tr key={row.name} className="app-table-row">
                    <td className="px-2.5 py-2 whitespace-nowrap tabular-nums">
                      {formatTradingDate(row.trading_date)}
                    </td>
                    <td className="px-2.5 py-2 whitespace-nowrap">{botTitle(row.bot_type)}</td>
                    <td className="px-2.5 py-2 text-right tabular-nums">
                      {row.records.toLocaleString("en-IN")}
                    </td>
                    <td className="px-2.5 py-2 text-right tabular-nums text-faint">
                      {formatSize(row.size_bytes)}
                    </td>
                    <td className="px-2.5 py-2 text-right whitespace-nowrap">
                      <button
                        type="button"
                        className="app-btn-secondary"
                        disabled={downloadingName === row.name}
                        aria-busy={downloadingName === row.name}
                        onClick={() => handleDownloadOne(row)}
                      >
                        <AsyncLabelSpan
                          busy={downloadingName === row.name}
                          idleLabel="Download"
                          busyLabel="…"
                        />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </section>
    </div>
  );
}
