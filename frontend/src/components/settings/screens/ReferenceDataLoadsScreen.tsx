"use client";

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { HelpLink } from "@/components/help/HelpLink";
import { AsyncLabelSpan } from "@/components/ui/AsyncLabelSpan";
import { SettingsScreenHeader } from "@/components/settings/SettingsScreenHeader";
import { apiClient } from "@/lib/api-client";
import { formatApiDateTime, formatSourceFileDate } from "@/lib/format-iso-date";

type IngestHistoryItem = {
  id: string;
  kind: string;
  display_name: string;
  source_file_date: string | null;
  row_count: number;
  ingested_at: string;
  ok: boolean;
  notes: string | null;
  source_url: string | null;
};

type MarginSourceData = {
  margin_source: "breeze_api" | "exchange_baseline";
};

type ReferenceDataState = {
  enabled: boolean;
  hour_ist: number;
  minute_ist: number;
  running: boolean;
  refresh_in_progress: boolean;
  last_refresh_message: string | null;
  nse_fo_refresh_in_progress: boolean;
  nse_fo_progress_pct: number;
  nse_fo_message: string | null;
  nse_fo_source_date: string | null;
  bse_fo_refresh_in_progress: boolean;
  bse_fo_progress_pct: number;
  bse_fo_message: string | null;
  bse_fo_source_date: string | null;
  scrip_refresh_in_progress: boolean;
  scrip_progress_pct: number;
  scrip_message: string | null;
  span_refresh_in_progress: boolean;
  span_progress_pct: number;
  span_message: string | null;
  nse_span_source_file: string | null;
  nse_span_source_date: string | null;
  nse_span_refreshed_at: string | null;
  nse_span_row_count: number | null;
  bse_span_source_file: string | null;
  bse_span_source_date: string | null;
  bse_span_refreshed_at: string | null;
  bse_span_row_count: number | null;
  ingest_history: IngestHistoryItem[];
};

const SOURCES = [
  {
    label: "NSE FO BhavCopy",
    inKey: "nse_fo_refresh_in_progress" as const,
    pctKey: "nse_fo_progress_pct" as const,
    msgKey: "nse_fo_message" as const,
    dateKey: "nse_fo_source_date" as const,
  },
  {
    label: "BSE FO BhavCopy",
    inKey: "bse_fo_refresh_in_progress" as const,
    pctKey: "bse_fo_progress_pct" as const,
    msgKey: "bse_fo_message" as const,
    dateKey: "bse_fo_source_date" as const,
  },
  {
    label: "ICICI Scrip Master",
    inKey: "scrip_refresh_in_progress" as const,
    pctKey: "scrip_progress_pct" as const,
    msgKey: "scrip_message" as const,
    dateKey: null,
  },
];

const fileInputCls =
  "mt-1 w-full rounded-[9px] border border-border bg-panel2 px-3 py-2 text-xs text-text file:mr-3 file:rounded-md file:border-0 file:bg-panel file:px-2.5 file:py-1 file:text-xs file:font-semibold file:text-text";

function DatabaseIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <ellipse cx="12" cy="5" rx="8" ry="3" />
      <path d="M4 5v14c0 1.66 3.58 3 8 3s8-1.34 8-3V5" />
      <path d="M4 12c0 1.66 3.58 3 8 3s8-1.34 8-3" />
    </svg>
  );
}

function formatTime12h(hour: number, minute: number): string {
  const h = hour % 12 || 12;
  const ampm = hour < 12 ? "AM" : "PM";
  return `${String(h).padStart(2, "0")}:${String(minute).padStart(2, "0")} ${ampm}`;
}

function scheduleHelperText(hour: number, minute: number, enabled: boolean): string {
  if (!enabled) return "Daily schedule is off. Use Load now to refresh data manually.";
  const timeLabel = formatTime12h(hour, minute);
  if (hour === 18 && minute === 0) return "Default 6:00 PM after market close.";
  return `Runs daily at ${timeLabel} IST after market close.`;
}

function ScheduleToggle({
  enabled,
  disabled,
  onChange,
}: {
  enabled: boolean;
  disabled?: boolean;
  onChange: (next: boolean) => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={enabled}
      disabled={disabled}
      onClick={() => onChange(!enabled)}
      className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition disabled:cursor-not-allowed disabled:opacity-45 ${
        enabled ? "bg-accent-strong" : "bg-border"
      }`}
    >
      <span
        className={`inline-block size-5 transform rounded-full bg-white shadow transition ${
          enabled ? "translate-x-[22px]" : "translate-x-0.5"
        }`}
      />
    </button>
  );
}

export function ReferenceDataLoadsScreen() {
  const qc = useQueryClient();
  const [scheduleDraft, setScheduleDraft] = useState<{
    hour_ist: number;
    minute_ist: number;
    enabled: boolean;
  } | null>(null);
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [marginDraft, setMarginDraft] = useState<"breeze_api" | "exchange_baseline" | null>(null);
  const [showFullHistory, setShowFullHistory] = useState(false);

  const q = useQuery({
    queryKey: ["settings", "reference-data-loads"],
    queryFn: () =>
      apiClient.get<ReferenceDataState>("/api/settings/reference-data-loads/status"),
    refetchInterval: (query) => (query.state.data?.refresh_in_progress ? 750 : 5000),
  });

  const marginQ = useQuery({
    queryKey: ["settings", "margin-source"],
    queryFn: () => apiClient.get<MarginSourceData>("/api/settings/margin-source/data"),
  });

  const marginSaveMut = useMutation({
    mutationFn: (margin_source: "breeze_api" | "exchange_baseline") =>
      apiClient.post("/api/settings/margin-source", { margin_source }),
    onSuccess: (_data, margin_source) => {
      setMarginDraft(null);
      qc.setQueryData<MarginSourceData>(["settings", "margin-source"], (old) =>
        old ? { ...old, margin_source } : { margin_source },
      );
    },
    onError: () => {
      setMarginDraft(null);
    },
  });

  const server = q.data;
  const sch = scheduleDraft ?? {
    hour_ist: server?.hour_ist ?? 18,
    minute_ist: server?.minute_ist ?? 0,
    enabled: server?.enabled ?? true,
  };
  const scheduleTime = `${String(sch.hour_ist).padStart(2, "0")}:${String(sch.minute_ist).padStart(2, "0")}`;

  const scheduleDirty =
    scheduleDraft !== null &&
    server !== undefined &&
    (scheduleDraft.enabled !== server.enabled ||
      scheduleDraft.hour_ist !== server.hour_ist ||
      scheduleDraft.minute_ist !== server.minute_ist);

  const loadNowMut = useMutation({
    mutationFn: () =>
      apiClient.post<ReferenceDataState, Record<string, never>>(
        "/api/settings/reference-data-loads/load-now",
        {},
      ),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["settings", "reference-data-loads"] }),
  });

  const scheduleMut = useMutation({
    mutationFn: (payload: { enabled: boolean; hour_ist: number; minute_ist: number }) =>
      apiClient.put<ReferenceDataState, typeof payload>(
        "/api/settings/reference-data-loads/schedule",
        payload,
      ),
    onSuccess: () => {
      setScheduleDraft(null);
      void qc.invalidateQueries({ queryKey: ["settings", "reference-data-loads"] });
    },
  });

  const uploadMut = useMutation({
    mutationFn: async (file: File) => {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("market", "bse");
      return apiClient.postForm<{ ok?: boolean; message?: string; result?: Record<string, unknown> }>(
        "/api/settings/margin-source/upload-baseline",
        fd,
      );
    },
    onMutate: () => {
      setUploadError(null);
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["settings", "reference-data-loads"] });
      setUploadFile(null);
    },
    onError: (e) => {
      setUploadError(e instanceof Error ? e.message : "Upload failed");
    },
  });

  useEffect(() => {
    let tickTimer: ReturnType<typeof setInterval> | undefined;
    let doneTimer: ReturnType<typeof setTimeout> | undefined;

    if (uploadMut.isPending) {
      tickTimer = setInterval(() => {
        setUploadProgress((prev) => {
          const step = Math.max(1, Math.round((95 - prev) / 9));
          return Math.min(95, prev + step);
        });
      }, 350);
    } else if (uploadMut.isSuccess) {
      doneTimer = setTimeout(() => {
        setUploadProgress(100);
        setTimeout(() => {
          setUploadProgress(0);
          uploadMut.reset();
        }, 900);
      }, 0);
    } else if (uploadMut.isError) {
      doneTimer = setTimeout(() => {
        setUploadProgress(0);
      }, 0);
    }

    return () => {
      if (tickTimer) clearInterval(tickTimer);
      if (doneTimer) clearTimeout(doneTimer);
    };
  }, [uploadMut.isPending, uploadMut.isSuccess, uploadMut.isError, uploadMut]);

  const uploadStatusText = useMemo(() => {
    if (uploadMut.isPending) return "Uploading BSE SPAN file…";
    if (uploadMut.isSuccess && uploadProgress > 0) {
      const r = uploadMut.data?.result as { inserted_rows?: number } | undefined;
      const n = r?.inserted_rows;
      return typeof n === "number" ? `Upload complete (${n} rows)` : "Upload complete";
    }
    return "";
  }, [uploadMut.isPending, uploadMut.isSuccess, uploadMut.data, uploadProgress]);

  const refreshing = Boolean(server?.refresh_in_progress);

  const marginSource = marginDraft ?? marginQ.data?.margin_source ?? "breeze_api";
  const useExchangeBaseline = marginSource === "exchange_baseline";
  const marginToggleDisabled = marginQ.isLoading && !marginQ.data;

  const nseSpanStatusText = server?.nse_span_refreshed_at
    ? `Refreshed ${formatApiDateTime(server.nse_span_refreshed_at)}`
    : server?.nse_span_source_date
      ? `Data date: ${formatSourceFileDate(server.nse_span_source_date)}`
      : server?.span_message || "Not yet loaded";

  const bseSpanStatusText = server?.bse_span_source_date
    ? `Data date: ${formatSourceFileDate(server.bse_span_source_date)}`
    : "Not loaded — upload required";

  const ingestHistory = server?.ingest_history ?? [];
  const displayedIngestHistory = useMemo(() => {
    if (showFullHistory) return ingestHistory;
    const seen = new Set<string>();
    return ingestHistory.filter((row) => {
      if (seen.has(row.kind)) return false;
      seen.add(row.kind);
      return true;
    });
  }, [ingestHistory, showFullHistory]);

  return (
    <div>
      <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
        <SettingsScreenHeader
          icon={<DatabaseIcon />}
          title="Reference Data Loads"
          description="Schedule daily loads for bhavcopy, scrip master, and SPAN baseline; choose SPAN vs Breeze API for Strategy Builder margins."
        />
        <HelpLink topicId="reference-data-loads" className="shrink-0 text-xs">
          Help
        </HelpLink>
      </div>

      {q.isLoading && <p className="text-sm text-muted">Loading…</p>}
      {q.error && (
        <div className="app-alert-error text-xs">
          {q.error instanceof Error ? q.error.message : "Unable to load status"}
        </div>
      )}

      {server && (
        <section className="app-card max-w-[760px] space-y-5 p-5">
          <div className="space-y-3 rounded-[10px] border border-border px-4 py-3.5">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div className="min-w-0 flex-1 space-y-1">
                <div className="text-heading font-semibold text-foreground">Daily schedule (IST)</div>
                <p className="text-heading text-muted">
                  {scheduleHelperText(sch.hour_ist, sch.minute_ist, sch.enabled)}
                </p>
              </div>
              <ScheduleToggle
                enabled={sch.enabled}
                disabled={scheduleMut.isPending}
                onChange={(next) => setScheduleDraft({ ...sch, enabled: next })}
              />
            </div>
            <div className="flex flex-wrap items-end gap-3 border-t border-border-soft pt-3">
              <label className="block max-w-xs text-xs">
                <span className="text-muted">Schedule time (IST)</span>
                <input
                  type="time"
                  step={60}
                  className="app-input mt-1 max-w-xs"
                  value={scheduleTime}
                  disabled={scheduleMut.isPending}
                  onChange={(e) => {
                    const [h, m] = e.target.value.split(":");
                    setScheduleDraft({
                      ...sch,
                      hour_ist: Number(h || 18),
                      minute_ist: Number(m || 0),
                    });
                  }}
                />
              </label>
              {scheduleDirty && (
                <span className="text-xs text-amber-accent">Unsaved schedule changes</span>
              )}
            </div>
          </div>

          <div className="space-y-3 rounded-[10px] border border-border px-4 py-3.5">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div className="min-w-0 flex-1 space-y-1">
                <div className="text-heading font-semibold text-foreground">
                  Use SPAN files for margin calculation
                </div>
                <p className="max-w-[480px] text-heading text-muted">
                  Off: Breeze API (default). On: Exchange Risk Baseline (SPAN) for Strategy Builder. ICICI
                  margins may differ; contracts missing from the baseline fall back to Breeze API.
                </p>
              </div>
              <ScheduleToggle
                enabled={useExchangeBaseline}
                disabled={marginToggleDisabled}
                onChange={(next) => {
                  const nextSource = next ? "exchange_baseline" : "breeze_api";
                  if (marginSource === nextSource || marginSaveMut.isPending) return;
                  setMarginDraft(nextSource);
                  marginSaveMut.mutate(nextSource);
                }}
              />
            </div>
          </div>

          <div className="flex flex-wrap gap-3">
            <button
              type="button"
              className="app-btn-outline"
              disabled={scheduleMut.isPending || !scheduleDirty}
              onClick={() =>
                scheduleMut.mutate({
                  enabled: sch.enabled,
                  hour_ist: sch.hour_ist,
                  minute_ist: sch.minute_ist,
                })
              }
            >
              <AsyncLabelSpan busy={scheduleMut.isPending} busyLabel="Saving…" idleLabel="Save schedule" />
            </button>
            <button
              type="button"
              className="app-btn-primary"
              disabled={loadNowMut.isPending || refreshing}
              onClick={() => loadNowMut.mutate()}
            >
              <AsyncLabelSpan busy={loadNowMut.isPending || refreshing} busyLabel="Loading…" idleLabel="Load now" />
            </button>
          </div>

          {server.last_refresh_message && (
            <p className="text-xs text-muted">{server.last_refresh_message}</p>
          )}

          {refreshing && (
            <div className="space-y-3 rounded-[10px] border border-accent/30 bg-accent-tint p-4">
              <div className="text-heading font-semibold text-foreground">Active load</div>
              {[
                ...SOURCES,
                {
                  label: "NSE SPAN Baseline",
                  inKey: "span_refresh_in_progress" as const,
                  pctKey: "span_progress_pct" as const,
                  msgKey: "span_message" as const,
                  dateKey: null,
                },
              ].map((src) => {
                const inProg = Boolean(server[src.inKey]);
                const pct = server[src.pctKey] ?? 0;
                const msg = server[src.msgKey];
                if (!inProg && pct === 0 && !msg) return null;
                return (
                  <div key={src.label} className="space-y-1">
                    <div className="flex justify-between text-xs text-foreground">
                      <span>{src.label}</span>
                      <span className="font-mono tabular-nums">{pct}%</span>
                    </div>
                    <div className="h-1.5 w-full overflow-hidden rounded-full bg-track">
                      <div
                        className="h-full rounded-full bg-accent-strong transition-all"
                        style={{ width: `${Math.min(100, pct)}%` }}
                      />
                    </div>
                    {msg && <p className="text-xs text-muted">{msg}</p>}
                  </div>
                );
              })}
            </div>
          )}

          <div className="space-y-2">
            <h3 className="text-heading font-bold text-foreground">Source status</h3>
            <div className="flex flex-col">
              {SOURCES.map((src) => (
                <div
                  key={src.label}
                  className="datarow flex justify-between gap-4 border-b border-border-soft px-1.5 py-2 text-xs"
                >
                  <span className="text-foreground">{src.label}</span>
                  <span className="text-muted">
                    {src.dateKey && server[src.dateKey]
                      ? `Data date: ${formatSourceFileDate(server[src.dateKey])}`
                      : server[src.msgKey] || "—"}
                  </span>
                </div>
              ))}
              <div className="datarow flex justify-between gap-4 border-b border-border-soft px-1.5 py-2 text-xs">
                <span className="text-foreground">NSE SPAN Baseline</span>
                <span className="text-muted">{nseSpanStatusText}</span>
              </div>
              <div className="datarow flex justify-between gap-4 px-1.5 py-2 text-xs">
                <span className="text-foreground">BSE SPAN Baseline</span>
                <span className="text-muted">{bseSpanStatusText}</span>
              </div>
            </div>
          </div>

          <div className="space-y-2 rounded-[10px] border border-amber-accent/40 bg-amber-tint p-4">
            <h3 className="text-heading font-bold text-foreground">BSE SPAN Baseline</h3>
            <p className="text-table leading-relaxed text-muted">
              BSE does not publish a direct archive URL like NSE. Download the SPAN XML (or ZIP containing it)
              from the{" "}
              <a
                href="https://www.bseindia.com/markets/Derivatives/DeriReports/Riskparameternew.aspx"
                target="_blank"
                rel="noreferrer"
                className="app-link"
              >
                BSE Risk Parameter report
              </a>{" "}
              and upload it here. Only <strong className="text-foreground">BSXOPT</strong> and{" "}
              <strong className="text-foreground">BKXOPT</strong> portfolios (Sensex / BANKEX on BFO) are
              ingested.
            </p>
            {server.bse_span_source_file && (
              <p className="text-table text-muted">
                Loaded: {server.bse_span_source_file}
                {server.bse_span_row_count != null ? ` (${server.bse_span_row_count} rows)` : ""}
                {server.bse_span_refreshed_at
                  ? ` · ${formatApiDateTime(server.bse_span_refreshed_at)}`
                  : ""}
              </p>
            )}
            <label className="block text-xs text-muted">
              Choose file
              <input
                type="file"
                accept=".xml,.spn,.zip,application/xml,text/xml"
                disabled={uploadMut.isPending}
                className={fileInputCls}
                onChange={(e) => setUploadFile(e.target.files?.[0] ?? null)}
              />
            </label>
            {uploadError ? <div className="app-alert-error text-xs">{uploadError}</div> : null}
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                className="app-btn-outline"
                disabled={uploadMut.isPending || !uploadFile}
                aria-busy={uploadMut.isPending}
                onClick={() => {
                  if (!uploadFile) return;
                  uploadMut.mutate(uploadFile);
                }}
              >
                <AsyncLabelSpan busy={uploadMut.isPending} idleLabel="Upload BSE SPAN file" busyLabel="Uploading…" />
              </button>
            </div>
            {uploadProgress > 0 ? (
              <div className="space-y-1 pt-1">
                <div className="h-2 w-full overflow-hidden rounded-full bg-track">
                  <div
                    className="h-full bg-accent-strong transition-all duration-300"
                    style={{ width: `${uploadProgress}%` }}
                  />
                </div>
                <div className="text-xs text-muted">
                  {uploadStatusText} {uploadMut.isPending ? `${Math.round(uploadProgress)}%` : ""}
                </div>
              </div>
            ) : null}
          </div>

          <div className="space-y-2">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h3 className="text-heading font-bold text-foreground">Ingest history</h3>
              {ingestHistory.length > 0 ? (
                <button
                  type="button"
                  className="text-xs font-medium text-accent-strong hover:underline"
                  onClick={() => setShowFullHistory((prev) => !prev)}
                >
                  {showFullHistory ? "Show latest only" : `Show all history (${ingestHistory.length})`}
                </button>
              ) : null}
            </div>
            {ingestHistory.length === 0 ? (
              <p className="text-xs text-muted">No ingest history yet.</p>
            ) : (
              <div className="app-table-wrap">
                <table className="min-w-full text-left text-table">
                  <thead className="app-table-head">
                    <tr>
                      <th className="px-2.5 py-2 font-semibold whitespace-nowrap">Source</th>
                      <th className="px-2.5 py-2 font-semibold whitespace-nowrap">File date</th>
                      <th className="px-2.5 py-2 text-right font-semibold whitespace-nowrap">Rows</th>
                      <th className="px-2.5 py-2 font-semibold whitespace-nowrap">Status</th>
                      <th className="px-2.5 py-2 font-semibold whitespace-nowrap">Ingested</th>
                    </tr>
                  </thead>
                  <tbody>
                    {displayedIngestHistory.map((row) => (
                      <tr key={row.id} className="app-table-row">
                        <td className="px-2.5 py-2 whitespace-nowrap text-foreground">{row.display_name}</td>
                        <td className="px-2.5 py-2 whitespace-nowrap text-foreground">
                          {formatSourceFileDate(row.source_file_date)}
                        </td>
                        <td className="px-2.5 py-2 text-right font-mono tabular-nums whitespace-nowrap text-foreground">
                          {row.row_count.toLocaleString("en-IN")}
                        </td>
                        <td className="px-2.5 py-2 whitespace-nowrap">
                          <div className="space-y-1">
                            <span
                              className={`inline-flex rounded-full px-2 py-0.5 text-micro font-semibold ${
                                row.ok ? "bg-up-tint text-up" : "bg-down-tint text-down"
                              }`}
                            >
                              {row.ok ? "OK" : "Failed"}
                            </span>
                            {!row.ok && row.notes ? (
                              <p className="max-w-xs text-xs text-muted">{row.notes}</p>
                            ) : null}
                          </div>
                        </td>
                        <td
                          className="px-2.5 py-2 font-mono tabular-nums whitespace-nowrap text-muted"
                          title={row.ingested_at}
                        >
                          {formatApiDateTime(row.ingested_at)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
          <p className="text-xs text-muted">
            <HelpLink topicId="reference-data-loads" className="text-xs">
              Help
            </HelpLink>
          </p>
        </section>
      )}
    </div>
  );
}
