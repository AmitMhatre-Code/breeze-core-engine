"use client";

import { Fragment, useCallback, useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";

import { AppShell } from "@/components/layout/AppShell";
import { AsyncLabelSpan } from "@/components/ui/AsyncLabelSpan";
import {
  ExplainabilityLevel1View,
  ExplainabilityLevel2View,
  ExplainabilityLevel3View,
} from "@/components/strategy-builder/StrategyExplainabilityPanel";
import type { AuditExplainabilityLevels } from "@/lib/strategy-builder/types";
import {
  downloadAllStrategyAuditLogs,
  downloadStrategyAuditLog,
  fetchStrategyAuditExplainability,
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

type ExpandedLevel = 1 | 2 | 3;

type ExpandedRow = {
  sessionId: string;
  level: ExpandedLevel;
};

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

function levelLinkClass(active: boolean, disabled?: boolean): string {
  const base =
    "rounded px-1.5 py-0.5 text-[11px] font-medium tabular-nums transition";
  if (disabled) {
    return `${base} cursor-not-allowed text-zinc-400 dark:text-zinc-600`;
  }
  if (active) {
    return `${base} bg-sky-600 text-white dark:bg-sky-500`;
  }
  return `${base} app-link hover:bg-zinc-100 dark:hover:bg-zinc-800`;
}

export default function StrategyAuditLogsSettingsPage() {
  const [downloadingAll, setDownloadingAll] = useState(false);
  const [downloadingSessionId, setDownloadingSessionId] = useState<string | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<ExpandedRow | null>(null);
  const [explainabilityCache, setExplainabilityCache] = useState<
    Record<string, AuditExplainabilityLevels>
  >({});
  const [explainabilityLoading, setExplainabilityLoading] = useState<string | null>(null);
  const [explainabilityError, setExplainabilityError] = useState<string | null>(null);

  const q = useQuery({
    queryKey: ["settings", "strategy-builder-audit-logs"],
    queryFn: fetchStrategyAuditLogIndex,
  });

  const logs = q.data?.logs ?? [];
  const maxLogs = q.data?.max_logs ?? 10;

  const loadExplainability = useCallback(
    async (sessionId: string) => {
      if (explainabilityCache[sessionId]) {
        return explainabilityCache[sessionId];
      }
      setExplainabilityError(null);
      setExplainabilityLoading(sessionId);
      try {
        const data = await fetchStrategyAuditExplainability(sessionId);
        setExplainabilityCache((prev) => ({ ...prev, [sessionId]: data }));
        return data;
      } catch (e) {
        const msg =
          e instanceof Error ? e.message : "Failed to load explainability";
        setExplainabilityError(msg);
        return null;
      } finally {
        setExplainabilityLoading(null);
      }
    },
    [explainabilityCache],
  );

  const handleLevelClick = async (
    row: StrategyAuditLogItem,
    level: ExpandedLevel,
  ) => {
    const sessionId = row.session_id;
    if (!sessionId || !row.explainability_available) return;

    if (expanded?.sessionId === sessionId && expanded.level === level) {
      setExpanded(null);
      return;
    }

    setExpanded({ sessionId, level });
    await loadExplainability(sessionId);
  };

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

  const renderExpandedContent = (sessionId: string, level: ExpandedLevel) => {
    if (explainabilityLoading === sessionId) {
      return <p className="text-sm app-text-muted">Loading explainability…</p>;
    }

    const data = explainabilityCache[sessionId];
    if (!data) {
      return (
        <p className="text-sm text-red-600 dark:text-red-400">
          {explainabilityError ?? "Explainability is not available for this log."}
        </p>
      );
    }

    if (level === 1) {
      return <ExplainabilityLevel1View executiveSummary={data.level_1} />;
    }
    if (level === 2) {
      return (
        <ExplainabilityLevel2View
          whyThis={data.level_2.why_this}
          whyNot={data.level_2.why_not}
        />
      );
    }
    return <ExplainabilityLevel3View insights={data.level_3} />;
  };

  const columnCount = 12;

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
              (persistent data volume). Levels 1–3 provide user-friendly explainability;
              Level 4 is the full technical audit JSON download.
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
                  <th className="px-3 py-2 font-medium whitespace-nowrap">Transparency</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-200/80 dark:divide-zinc-800">
                {logs.map((row) => {
                  const sessionId = row.session_id ?? row.filename;
                  const isDownloading = downloadingSessionId === row.session_id;
                  const isExpanded = expanded?.sessionId === row.session_id;
                  const canExplain = Boolean(row.explainability_available && row.session_id);

                  return (
                    <Fragment key={sessionId}>
                      <tr>
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
                          <div className="flex flex-wrap items-center gap-1">
                            <button
                              type="button"
                              title="Level 1: Executive summary"
                              disabled={!canExplain}
                              className={levelLinkClass(
                                isExpanded && expanded?.level === 1,
                                !canExplain,
                              )}
                              onClick={() => void handleLevelClick(row, 1)}
                            >
                              L1
                            </button>
                            <button
                              type="button"
                              title="Level 2: Why this / Why not"
                              disabled={!canExplain}
                              className={levelLinkClass(
                                isExpanded && expanded?.level === 2,
                                !canExplain,
                              )}
                              onClick={() => void handleLevelClick(row, 2)}
                            >
                              L2
                            </button>
                            <button
                              type="button"
                              title="Level 3: What if?"
                              disabled={!canExplain}
                              className={levelLinkClass(
                                isExpanded && expanded?.level === 3,
                                !canExplain,
                              )}
                              onClick={() => void handleLevelClick(row, 3)}
                            >
                              L3
                            </button>
                            <button
                              type="button"
                              title="Level 4: Download full technical audit JSON"
                              disabled={!row.session_id || isDownloading || downloadingAll}
                              className={levelLinkClass(false)}
                              onClick={() => void handleDownloadOne(row)}
                            >
                              {isDownloading ? "…" : "L4"}
                            </button>
                          </div>
                        </td>
                      </tr>
                      {isExpanded && row.session_id ? (
                        <tr>
                          <td
                            colSpan={columnCount}
                            className="bg-zinc-50/80 px-4 py-4 dark:bg-zinc-950/50"
                          >
                            <p className="mb-3 text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                              Level {expanded!.level}:{" "}
                              {expanded!.level === 1
                                ? "Executive summary"
                                : expanded!.level === 2
                                  ? "Why this strategy? / Why not?"
                                  : "What if?"}
                            </p>
                            {renderExpandedContent(row.session_id, expanded!.level)}
                          </td>
                        </tr>
                      ) : null}
                    </Fragment>
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
