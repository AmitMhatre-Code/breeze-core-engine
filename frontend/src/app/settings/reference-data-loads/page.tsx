"use client";

import Link from "next/link";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AppShell } from "@/components/layout/AppShell";
import { AsyncLabelSpan } from "@/components/ui/AsyncLabelSpan";
import { apiClient } from "@/lib/api-client";

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

  const q = useQuery({
    queryKey: ["settings", "reference-data-loads"],
    queryFn: () =>
      apiClient.get<ReferenceDataState>("/api/settings/reference-data-loads/status"),
    refetchInterval: (query) => (query.state.data?.refresh_in_progress ? 750 : 5000),
  });

  const server = q.data;
  const sch = scheduleDraft ?? {
    hour_ist: server?.hour_ist ?? 18,
    minute_ist: server?.minute_ist ?? 0,
    enabled: server?.enabled ?? true,
  };
  const scheduleTime = `${String(sch.hour_ist).padStart(2, "0")}:${String(sch.minute_ist).padStart(2, "0")}`;

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

  const refreshing = Boolean(server?.refresh_in_progress);

  return (
    <AppShell>
      <section className="app-card space-y-6 p-4">
        <Link href="/settings" className="app-link text-xs inline-block">
          Back to Settings
        </Link>
        <header className="space-y-1">
          <h2 className="text-xl app-text-heading">Reference Data Loads</h2>
          <p className="text-sm app-text-muted">
            Schedule and load NSE/BSE FO bhavcopy, ICICI scrip master, and NSE SPAN margin baseline.
            Also manages data used by Place Order, Basket Order, and Strategy Builder.
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
            <div className="flex items-center justify-between gap-4 rounded-lg border border-zinc-200/80 px-4 py-3 dark:border-zinc-700">
              <div>
                <div className="text-sm font-semibold">Daily schedule (IST)</div>
                <p className="text-xs app-text-muted">Default 6:00 PM after market close.</p>
              </div>
              <ScheduleToggle
                enabled={sch.enabled}
                disabled={scheduleMut.isPending}
                onChange={(next) => setScheduleDraft({ ...sch, enabled: next })}
              />
            </div>

            <label className="block text-sm">
              <span className="app-text-muted">Schedule time</span>
              <input
                type="time"
                className="app-input mt-1 max-w-xs"
                value={scheduleTime}
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

            <div className="flex flex-wrap gap-3">
              <button
                type="button"
                className="app-btn-outline"
                disabled={scheduleMut.isPending}
                onClick={() =>
                  scheduleMut.mutate({
                    enabled: sch.enabled,
                    hour_ist: sch.hour_ist,
                    minute_ist: sch.minute_ist,
                  })
                }
              >
                <AsyncLabelSpan pending={scheduleMut.isPending} pendingLabel="Saving…" idleLabel="Save schedule" />
              </button>
              <button
                type="button"
                className="app-btn-primary"
                disabled={loadNowMut.isPending || refreshing}
                onClick={() => loadNowMut.mutate()}
              >
                <AsyncLabelSpan pending={loadNowMut.isPending || refreshing} pendingLabel="Loading…" idleLabel="Load now" />
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
                        ? `Data date: ${server[src.dateKey]}`
                        : server[src.msgKey] || "—"}
                    </span>
                  </li>
                ))}
              </ul>
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
                        <td className="py-2">{row.source_file_date ?? "—"}</td>
                        <td className="py-2">{row.row_count}</td>
                        <td className="py-2">{row.ok ? "OK" : "Failed"}</td>
                        <td className="py-2">{row.ingested_at}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            <p className="text-xs app-text-muted">
              Margin source and scrip master can also be managed from{" "}
              <Link href="/settings/margin-source" className="app-link">
                Margin Calculation Source
              </Link>{" "}
              and{" "}
              <Link href="/settings/scrip-master" className="app-link">
                Scrip Master
              </Link>
              .
            </p>
          </>
        )}
      </section>
    </AppShell>
  );
}
