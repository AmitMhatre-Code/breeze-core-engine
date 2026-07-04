"use client";

import { Fragment, useCallback, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { HelpLink } from "@/components/help/HelpLink";
import { AsyncLabelSpan } from "@/components/ui/AsyncLabelSpan";
import { SettingsScreenHeader } from "@/components/settings/SettingsScreenHeader";
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

function AuditLogIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <path d="M14 2v6h6" />
      <path d="M9 15h6M9 11h2" />
    </svg>
  );
}

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

function levelLinkClass(active: boolean, disabled?: boolean, isDownload?: boolean): string {
  const base =
    "rounded-[5px] border px-1.5 py-0.5 text-[10.5px] font-semibold tabular-nums transition";
  if (disabled) {
    return `${base} cursor-not-allowed border-border-soft text-faint`;
  }
  if (active) {
    return `${base} border-accent-strong bg-accent-tint text-accent-strong`;
  }
  if (isDownload) {
    return `${base} border-border text-foreground hover:bg-panel2`;
  }
  return `${base} border-border text-accent-strong hover:bg-accent-tint`;
}

export function AuditLogsScreen() {
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
        const msg = e instanceof Error ? e.message : "Failed to load explainability";
        setExplainabilityError(msg);
        return null;
      } finally {
        setExplainabilityLoading(null);
      }
    },
    [explainabilityCache],
  );

  const handleLevelClick = async (row: StrategyAuditLogItem, level: ExpandedLevel) => {
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
      setDownloadError(e instanceof Error ? e.message : "Failed to download audit log");
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
        <p className="text-sm text-down">
          {explainabilityError ?? "Explainability is not available for this log."}
        </p>
      );
    }

    if (level === 1) {
      return <ExplainabilityLevel1View executiveSummary={data.level_1} />;
    }
    if (level === 2) {
      return <ExplainabilityLevel2View whyThis={data.level_2.why_this} whyNot={data.level_2.why_not} />;
    }
    return <ExplainabilityLevel3View insights={data.level_3} />;
  };

  const columnCount = 8;

  return (
    <div>
      <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
        <SettingsScreenHeader
          icon={<AuditLogIcon />}
          title="Strategy Builder Audit Logs"
          description={
            <>
              Up to {maxLogs} recent propose-trades audit logs are stored on your server
              (persistent data volume). Levels 1–3 provide user-friendly explainability;
              Level 4 is the full technical audit JSON download.{" "}
              <HelpLink topicId="audit-logs" className="text-xs">
                Help
              </HelpLink>
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
              await downloadAllStrategyAuditLogs();
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
          <p className="text-sm text-muted">No audit logs yet. Run Strategy Builder propose-trades to generate logs.</p>
        ) : null}

        {logs.length > 0 ? (
          <div className="app-table-wrap">
            <table className="min-w-[760px] w-full text-left text-[11.5px]">
              <thead className="app-table-head">
                <tr>
                  <th className="px-2.5 py-2 font-semibold whitespace-nowrap">Finished</th>
                  <th className="px-2.5 py-2 font-semibold whitespace-nowrap">Scrip</th>
                  <th className="px-2.5 py-2 font-semibold whitespace-nowrap">Strategy</th>
                  <th className="px-2.5 py-2 font-semibold whitespace-nowrap">PoP</th>
                  <th className="px-2.5 py-2 font-semibold whitespace-nowrap">Margin</th>
                  <th className="px-2.5 py-2 font-semibold whitespace-nowrap">Loss</th>
                  <th className="px-2.5 py-2 font-semibold whitespace-nowrap">Session</th>
                  <th className="px-2.5 py-2 font-semibold whitespace-nowrap">Transparency</th>
                </tr>
              </thead>
              <tbody>
                {logs.map((row) => {
                  const sessionId = row.session_id ?? row.filename;
                  const isDownloading = downloadingSessionId === row.session_id;
                  const isExpanded = expanded?.sessionId === row.session_id;
                  const canExplain = Boolean(row.explainability_available && row.session_id);

                  return (
                    <Fragment key={sessionId}>
                      <tr className="app-table-row">
                        <td className="px-2.5 py-2 font-mono tabular-nums whitespace-nowrap text-foreground">
                          {formatFinishedAt(row.finished_at ?? row.started_at)}
                        </td>
                        <td className="px-2.5 py-2 whitespace-nowrap text-foreground">{row.stock_code ?? "—"}</td>
                        <td className="px-2.5 py-2 whitespace-nowrap text-foreground">
                          {formatStrategyCategory(row.strategy_category)}
                        </td>
                        <td className="px-2.5 py-2 font-mono tabular-nums whitespace-nowrap text-foreground">
                          {formatPop(row.min_pop_pct)}
                        </td>
                        <td className="px-2.5 py-2 font-mono tabular-nums whitespace-nowrap text-foreground">
                          {formatLacs(row.margin_lacs)}
                        </td>
                        <td className="px-2.5 py-2 font-mono tabular-nums whitespace-nowrap text-foreground">
                          {formatLacs(row.max_loss_lacs)}
                        </td>
                        <td className="px-2.5 py-2 font-mono text-[13px] whitespace-nowrap text-muted">
                          {row.session_id ? `${row.session_id.slice(0, 8)}…` : "—"}
                        </td>
                        <td className="px-2.5 py-2 whitespace-nowrap">
                          <div className="flex flex-wrap items-center gap-1">
                            <button
                              type="button"
                              title="Level 1: Executive summary"
                              disabled={!canExplain}
                              className={levelLinkClass(isExpanded && expanded?.level === 1, !canExplain)}
                              onClick={() => void handleLevelClick(row, 1)}
                            >
                              L1
                            </button>
                            <button
                              type="button"
                              title="Level 2: Why this / Why not"
                              disabled={!canExplain}
                              className={levelLinkClass(isExpanded && expanded?.level === 2, !canExplain)}
                              onClick={() => void handleLevelClick(row, 2)}
                            >
                              L2
                            </button>
                            <button
                              type="button"
                              title="Level 3: What if?"
                              disabled={!canExplain}
                              className={levelLinkClass(isExpanded && expanded?.level === 3, !canExplain)}
                              onClick={() => void handleLevelClick(row, 3)}
                            >
                              L3
                            </button>
                            <button
                              type="button"
                              title="Level 4: Download full technical audit JSON"
                              disabled={!row.session_id || isDownloading || downloadingAll}
                              className={levelLinkClass(false, false, true)}
                              onClick={() => void handleDownloadOne(row)}
                            >
                              {isDownloading ? "…" : "L4"}
                            </button>
                          </div>
                        </td>
                      </tr>
                      {isExpanded && row.session_id ? (
                        <tr>
                          <td colSpan={columnCount} className="bg-panel2 px-4 py-4">
                            <p className="mb-3 text-xs font-semibold uppercase tracking-wide text-faint">
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
    </div>
  );
}
