"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AppShell } from "@/components/layout/AppShell";
import { HelpLink } from "@/components/help/HelpLink";
import { AsyncLabelSpan } from "@/components/ui/AsyncLabelSpan";
import { apiClient } from "@/lib/api-client";
import { formatSourceFileDate } from "@/lib/format-iso-date";

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
  {
    label: "NSE SPAN Baseline",
    inKey: "span_refresh_in_progress" as const,
    pctKey: "span_progress_pct" as const,
    msgKey: "span_message" as const,
    dateKey: null,
  },
];

const fileInputCls =
  "mt-1 w-full rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-900 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100";

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
      className={`relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border transition disabled:cursor-not-allowed disabled:opacity-45 ${
        enabled
          ? "border-blue-400/50 bg-blue-600"
          : "border-zinc-300/90 bg-zinc-200/80 dark:border-white/15 dark:bg-zinc-800"
      }`}
    >
      <span
        className={`pointer-events-none absolute top-0.5 left-0.5 size-5 rounded-full bg-white shadow-md transition-transform ${
          enabled ? "translate-x-5" : "translate-x-0"
        }`}
      />
    </button>
  );
}

export default function ReferenceDataLoadsPage() {
  const qc = useQueryClient();
  const [scheduleDraft, setScheduleDraft] = useState<{
    hour_ist: number;
    minute_ist: number;
    enabled: boolean;
  } | null>(null);
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [uploadError, setUploadError] = useState<string | null>(null);

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
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["settings", "margin-source"] }),
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

  const useExchangeBaseline = marginQ.data?.margin_source === "exchange_baseline";
  const marginToggleDisabled =
    marginSaveMut.isPending || marginQ.isLoading || !marginQ.data;

  const bseSpanStatusText = server?.bse_span_source_date
    ? `Data date: ${formatSourceFileDate(server.bse_span_source_date)}`
    : "Not loaded — upload required";

  return (
    <AppShell>
      <section className="app-card space-y-6 p-4">
        <Link href="/settings" className="app-link text-xs inline-block">
          Back to Settings
        </Link>
        <header className="space-y-1">
          <h2 className="text-xl app-text-heading">Reference Data Loads</h2>
          <p className="text-sm app-text-muted">
            Schedule and load NSE/BSE FO bhavcopy, ICICI scrip master, NSE SPAN margin baseline (auto), and BSE SPAN
            baseline (manual upload). Choose SPAN vs Breeze API for Strategy Builder margin calculations.{" "}
            <HelpLink topicId="reference-data-loads" className="text-sm">
              Help
            </HelpLink>
          </p>
        </header>

        {q.isLoading && <p className="text-sm app-text-muted">Loading…</p>}
        {q.error && (
          <div className="app-alert-error text-xs">
            {q.error instanceof Error ? q.error.message : "Unable to load status"}
          </div>
        )}

        {server && (
          <>
            <div className="space-y-3 rounded-lg border border-zinc-200/80 px-4 py-3 dark:border-zinc-700">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div className="min-w-0 flex-1 space-y-1">
                  <div className="text-sm font-semibold">Daily schedule (IST)</div>
                  <p className="text-xs app-text-muted">
                    {scheduleHelperText(sch.hour_ist, sch.minute_ist, sch.enabled)}
                  </p>
                </div>
                <ScheduleToggle
                  enabled={sch.enabled}
                  disabled={scheduleMut.isPending}
                  onChange={(next) => setScheduleDraft({ ...sch, enabled: next })}
                />
              </div>
              <div className="flex flex-wrap items-end gap-3 border-t border-zinc-100 pt-3 dark:border-zinc-800">
                <label className="block max-w-xs text-sm">
                  <span className="app-text-muted">Schedule time (IST)</span>
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
                  <span className="text-xs text-amber-600 dark:text-amber-400">Unsaved schedule changes</span>
                )}
              </div>
            </div>

            <div className="space-y-3 rounded-lg border border-zinc-200/80 px-4 py-3 dark:border-zinc-700">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div className="min-w-0 flex-1 space-y-1">
                  <div className="text-sm font-semibold">Use SPAN files for margin calculation</div>
                  <p className="text-xs app-text-muted">
                    Off: Breeze API (default). On: Exchange Risk Baseline (SPAN) for Strategy Builder. ICICI margins may
                    differ; contracts missing from the baseline fall back to Breeze API.
                  </p>
                </div>
                <ScheduleToggle
                  enabled={useExchangeBaseline}
                  disabled={marginToggleDisabled}
                  onChange={(next) => {
                    const nextSource = next ? "exchange_baseline" : "breeze_api";
                    if (marginQ.data?.margin_source === nextSource) return;
                    marginSaveMut.mutate(nextSource);
                  }}
                />
              </div>
              {marginSaveMut.isPending && (
                <p className="text-xs app-text-muted border-t border-zinc-100 pt-2 dark:border-zinc-800">
                  Saving margin source…
                </p>
              )}
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
              <p className="text-xs app-text-muted">{server.last_refresh_message}</p>
            )}

            {refreshing && (
              <div className="space-y-3 rounded-lg border border-blue-200/60 bg-blue-50/40 p-4 dark:border-blue-900/40 dark:bg-blue-950/20">
                <div className="text-sm font-semibold">Active load</div>
                {SOURCES.map((src) => {
                  const inProg = Boolean(server[src.inKey]);
                  const pct = server[src.pctKey] ?? 0;
                  const msg = server[src.msgKey];
                  if (!inProg && pct === 0 && !msg) return null;
                  return (
                    <div key={src.label} className="space-y-1">
                      <div className="flex justify-between text-xs">
                        <span>{src.label}</span>
                        <span>{pct}%</span>
                      </div>
                      <div className="h-1.5 w-full overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-800">
                        <div
                          className="h-full rounded-full bg-blue-500 transition-all"
                          style={{ width: `${Math.min(100, pct)}%` }}
                        />
                      </div>
                      {msg && <p className="text-xs app-text-muted">{msg}</p>}
                    </div>
                  );
                })}
              </div>
            )}

            <div className="space-y-2">
              <h3 className="text-sm font-semibold">Source status</h3>
              <ul className="space-y-2 text-xs">
                {SOURCES.map((src) => (
                  <li key={src.label} className="flex justify-between gap-4 border-b border-zinc-100 py-2 dark:border-zinc-800">
                    <span>{src.label}</span>
                    <span className="app-text-muted">
                      {src.dateKey && server[src.dateKey]
                        ? `Data date: ${formatSourceFileDate(server[src.dateKey])}`
                        : server[src.msgKey] || "—"}
                    </span>
                  </li>
                ))}
                <li className="flex justify-between gap-4 border-b border-zinc-100 py-2 dark:border-zinc-800">
                  <span>BSE SPAN Baseline</span>
                  <span className="app-text-muted">{bseSpanStatusText}</span>
                </li>
              </ul>
            </div>

            <div className="space-y-3 rounded-lg border border-amber-200/70 bg-amber-50/40 p-4 dark:border-amber-900/40 dark:bg-amber-950/20">
              <h3 className="text-sm font-semibold">BSE SPAN Baseline</h3>
              <p className="text-xs app-text-muted">
                BSE does not publish a direct archive URL like NSE. Download the SPAN XML (or ZIP containing it) from the{" "}
                <a
                  href="https://www.bseindia.com/markets/Derivatives/DeriReports/Riskparameternew.aspx"
                  target="_blank"
                  rel="noreferrer"
                  className="app-link"
                >
                  BSE Risk Parameter report
                </a>{" "}
                and upload it here. Only <strong>BSXOPT</strong> and <strong>BKXOPT</strong> portfolios (Sensex / BANKEX
                on BFO) are ingested.
              </p>
              {server.bse_span_source_file && (
                <p className="text-xs app-text-muted">
                  Loaded: {server.bse_span_source_file}
                  {server.bse_span_row_count != null ? ` (${server.bse_span_row_count} rows)` : ""}
                  {server.bse_span_refreshed_at ? ` · ${server.bse_span_refreshed_at}` : ""}
                </p>
              )}
              <label className="block text-xs app-text-muted">
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
                  <AsyncLabelSpan
                    busy={uploadMut.isPending}
                    idleLabel="Upload BSE SPAN file"
                    busyLabel="Uploading…"
                  />
                </button>
              </div>
              {uploadProgress > 0 ? (
                <div className="space-y-1 pt-1">
                  <div className="h-2 w-full overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-800">
                    <div
                      className="h-full bg-sky-600 transition-all duration-300 dark:bg-sky-500"
                      style={{ width: `${uploadProgress}%` }}
                    />
                  </div>
                  <div className="text-xs app-text-muted">
                    {uploadStatusText} {uploadMut.isPending ? `${Math.round(uploadProgress)}%` : ""}
                  </div>
                </div>
              ) : null}
            </div>

            <div className="space-y-2">
              <h3 className="text-sm font-semibold">Ingest history</h3>
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="border-b border-zinc-200 dark:border-zinc-700">
                      <th className="py-2 text-left font-medium">Source</th>
                      <th className="py-2 text-left font-medium">File date</th>
                      <th className="py-2 text-left font-medium">Rows</th>
                      <th className="py-2 text-left font-medium">Status</th>
                      <th className="py-2 text-left font-medium">Ingested</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(server.ingest_history ?? []).map((row) => (
                      <tr key={row.id} className="border-b border-zinc-100 dark:border-zinc-800">
                        <td className="py-2">{row.display_name}</td>
                        <td className="py-2">{formatSourceFileDate(row.source_file_date)}</td>
                        <td className="py-2">{row.row_count}</td>
                        <td className="py-2">{row.ok ? "OK" : "Failed"}</td>
                        <td className="py-2">{row.ingested_at}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </>
        )}
      </section>
    </AppShell>
  );
}
