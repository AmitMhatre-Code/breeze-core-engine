"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AppShell } from "@/components/layout/AppShell";
import { AsyncLabelSpan } from "@/components/ui/AsyncLabelSpan";
import { apiClient } from "@/lib/api-client";

type BaselineExchangeMeta = {
  source_file?: string;
  source_date?: string;
  source_version?: number;
  refreshed_at?: string;
  rows?: number;
};

type MarginSourceData = {
  user_id: string;
  margin_source: "breeze_api" | "exchange_baseline";
  latest_baseline?: {
    source_file?: string;
    source_date?: string;
    source_version?: number;
    refreshed_at?: string;
    rows?: number;
    exchanges?: Record<string, BaselineExchangeMeta>;
  };
};

const fileInputCls =
  "mt-1 w-full rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-900 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100";

export default function MarginSourceSettingsPage() {
  const qc = useQueryClient();
  const [refreshProgress, setRefreshProgress] = useState(0);
  const [uploadPanel, setUploadPanel] = useState<null | "nse" | "bse">(null);
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const q = useQuery({
    queryKey: ["settings", "margin-source"],
    queryFn: () => apiClient.get<MarginSourceData>("/api/settings/margin-source/data"),
  });

  const saveMut = useMutation({
    mutationFn: (margin_source: "breeze_api" | "exchange_baseline") =>
      apiClient.post("/api/settings/margin-source", { margin_source }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["settings", "margin-source"] }),
  });

  const refreshMut = useMutation({
    mutationFn: () => apiClient.post("/api/settings/margin-source/refresh-baseline", {}),
    onMutate: () => {
      setRefreshProgress(8);
    },
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["settings", "margin-source"] }),
  });

  const uploadMut = useMutation({
    mutationFn: async (args: { file: File; market: "nse" | "bse" }) => {
      const fd = new FormData();
      fd.append("file", args.file);
      fd.append("market", args.market);
      return apiClient.postForm<{ ok?: boolean; message?: string; result?: Record<string, unknown> }>(
        "/api/settings/margin-source/upload-baseline",
        fd,
      );
    },
    onMutate: () => {
      setUploadError(null);
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["settings", "margin-source"] });
      setUploadFile(null);
    },
    onError: (e) => {
      setUploadError(e instanceof Error ? e.message : "Upload failed");
    },
  });

  useEffect(() => {
    let tickTimer: ReturnType<typeof setInterval> | undefined;
    let doneTimer: ReturnType<typeof setTimeout> | undefined;

    if (refreshMut.isPending) {
      tickTimer = setInterval(() => {
        setRefreshProgress((prev) => {
          const step = Math.max(1, Math.round((95 - prev) / 9));
          return Math.min(95, prev + step);
        });
      }, 350);
    } else if (refreshMut.isSuccess) {
      doneTimer = setTimeout(() => {
        setRefreshProgress(100);
        setTimeout(() => {
          setRefreshProgress(0);
          refreshMut.reset();
        }, 900);
      }, 0);
    } else if (refreshMut.isError) {
      doneTimer = setTimeout(() => {
        setRefreshProgress(0);
      }, 0);
    }

    return () => {
      if (tickTimer) clearInterval(tickTimer);
      if (doneTimer) clearTimeout(doneTimer);
    };
  }, [refreshMut.isPending, refreshMut.isSuccess, refreshMut.isError, refreshMut]);

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
          setUploadPanel(null);
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

  const useExchangeBaseline = q.data?.margin_source === "exchange_baseline";
  const toggleDisabled = saveMut.isPending || q.isLoading || !q.data;
  const refreshStatusText = useMemo(() => {
    if (refreshMut.isPending) return "Refreshing Exchange Risk Baseline...";
    if (refreshMut.isSuccess && refreshProgress > 0) return "Refresh complete";
    return "";
  }, [refreshMut.isPending, refreshMut.isSuccess, refreshProgress]);

  const uploadStatusText = useMemo(() => {
    if (uploadMut.isPending) return "Uploading margin file…";
    if (uploadMut.isSuccess && uploadProgress > 0) {
      const r = uploadMut.data?.result as { inserted_rows?: number } | undefined;
      const n = r?.inserted_rows;
      return typeof n === "number" ? `Upload complete (${n} rows)` : "Upload complete";
    }
    return "";
  }, [uploadMut.isPending, uploadMut.isSuccess, uploadMut.data, uploadProgress]);

  const toggleUploadPanel = (market: "nse" | "bse") => {
    setUploadPanel((p) => (p === market ? null : market));
    setUploadFile(null);
    setUploadError(null);
    uploadMut.reset();
  };

  const closeUploadPanel = () => {
    setUploadPanel(null);
    setUploadFile(null);
    setUploadError(null);
    uploadMut.reset();
  };

  const handleToggle = (checked: boolean) => {
    const nextSource = checked ? "exchange_baseline" : "breeze_api";
    if (q.data?.margin_source === nextSource) return;
    saveMut.mutate(nextSource);
  };

  return (
    <AppShell>
      <section className="app-card space-y-4 p-4">
        <Link href="/settings" className="app-link text-xs inline-block">
          Back to Settings
        </Link>
        <h2 className="text-xl app-text-heading">Margin Calculation Source</h2>

        {q.isLoading && <div className="text-sm text-zinc-500 dark:text-zinc-400">Loading…</div>}
        {q.error && (
          <div className="app-alert-error text-xs">
            {q.error instanceof Error ? q.error.message : "Unable to load settings"}
          </div>
        )}

        {q.data && (
          <div className="space-y-4">

            <div className="space-y-2 text-xs text-zinc-700 dark:text-zinc-300">
              <p>
                NSE publishes SPAN margin (exchange risk baseline) files for all contracts multiple times a day {" "}
                <a
                  href="https://www.nseindia.com/all-reports-derivatives"
                  target="_blank"
                  rel="noreferrer"
                  className="app-link"
                >
                  here
                </a>
                . To avoid consuming a lot of Breeze API calls and to speed up strategy building, you can override use of
                Breeze APIs for margin calculations and instead rely on the approximation provided by the Exchange Risk
                Baseline.
              </p>
              <p>
                BSE does not offer the same direct archive URL. For BSE index options (Strategy Builder segment BFO), you
                can upload the SPAN XML (or ZIP containing it) from BSE. Only Sensex (
                <strong>BSESEN</strong>) and <strong>BANKEX</strong> margins are loaded (SPAN{" "}
                <strong>BSXOPT</strong> / <strong>BKXOPT</strong>); NSE
                refresh continues to cover all NFO
                contracts. The BSE Margin XML can be downloaded{" "} 
                <a
                  href="https://www.bseindia.com/markets/Derivatives/DeriReports/Riskparameternew.aspx"
                  target="_blank"
                  rel="noreferrer"
                  className="app-link"
                >
                  here
                </a>
                .
              </p>
              <p>
                Note that ICICI&apos;s calculation of SPAN margins will differ from the Exchange Risk Baseline and
                will likely be higher.
              </p>
            </div>
            <div className="rounded-md border border-zinc-200 p-3 dark:border-zinc-800">
              <div className="flex min-w-0 items-start justify-between gap-3">
                <div className="min-w-0 flex-1 space-y-1 pe-1">
                  <div className="text-sm font-medium text-zinc-900 dark:text-zinc-100">
                    Margin source override
                  </div>
                  <div className="text-xs text-zinc-500 dark:text-zinc-400">
                    Off: Breeze API (default). On: Use Exchange Risk Baseline for margin calculations in strategies.
                  </div>
                </div>
                <button
                  type="button"
                  role="switch"
                  aria-checked={useExchangeBaseline}
                  aria-label="Toggle exchange baseline for margin calculations"
                  disabled={toggleDisabled}
                  onClick={() => handleToggle(!useExchangeBaseline)}
                  className={`relative h-6 w-11 shrink-0 rounded-full transition ${
                    toggleDisabled
                      ? useExchangeBaseline
                        ? "cursor-not-allowed bg-sky-800"
                        : "cursor-not-allowed bg-zinc-400 dark:bg-zinc-600"
                      : useExchangeBaseline
                        ? "bg-sky-600"
                        : "bg-zinc-300 dark:bg-zinc-700"
                  }`}
                >
                  <span
                    aria-hidden
                    className={`pointer-events-none absolute top-1/2 size-5 -translate-y-1/2 rounded-full bg-white shadow transition-[inset-inline-start,inset-inline-end] duration-200 ease-out ${
                      useExchangeBaseline
                        ? "start-auto end-0.5"
                        : "start-0.5 end-auto"
                    }`}
                  />
                </button>
              </div>
              {saveMut.isPending && (
                <div className="mt-2 text-xs text-zinc-500 dark:text-zinc-400">Saving setting...</div>
              )}
            </div>
            <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap">
              <button
                type="button"
                className="app-btn-outline"
                onClick={() => {
                  closeUploadPanel();
                  refreshMut.mutate();
                }}
                disabled={refreshMut.isPending || uploadMut.isPending}
                aria-busy={refreshMut.isPending}
              >
                <AsyncLabelSpan
                  busy={refreshMut.isPending}
                  idleLabel="Refresh Exchange Risk Baseline (NSE)"
                  busyLabel="Refreshing…"
                />
              </button>
              <button
                type="button"
                className="app-btn-outline"
                onClick={() => {
                  refreshMut.reset();
                  toggleUploadPanel("nse");
                }}
                disabled={refreshMut.isPending || uploadMut.isPending}
              >
                Upload NSE Margin XML
              </button>
              <button
                type="button"
                className="app-btn-outline"
                onClick={() => {
                  refreshMut.reset();
                  toggleUploadPanel("bse");
                }}
                disabled={refreshMut.isPending || uploadMut.isPending}
              >
                Upload BSE Margin XML
              </button>
            </div>

            {uploadPanel ? (
              <div className="rounded-md border border-zinc-200 p-3 dark:border-zinc-800 space-y-3">
                <div className="flex items-start justify-between gap-2">
                  <div className="text-sm font-medium text-zinc-900 dark:text-zinc-100">
                    {uploadPanel === "nse" ? "NSE margin file" : "BSE margin file"}
                  </div>
                  <button
                    type="button"
                    className="app-link text-xs shrink-0"
                    disabled={uploadMut.isPending}
                    onClick={closeUploadPanel}
                  >
                    Close
                  </button>
                </div>
                <p className="text-xs text-zinc-600 dark:text-zinc-400">
                  {uploadPanel === "nse"
                    ? "SPAN XML, .spn, or ZIP (same as the NSE archive). All option series in the file are loaded into the NFO baseline."
                    : "SPAN XML or ZIP from BSE. Only BSXOPT and BKXOPT portfolios (BSESEN / BANKEX on BFO) are loaded."}
                </p>
                <label className="block text-xs text-zinc-600 dark:text-zinc-400">
                  Choose file
                  <input
                    type="file"
                    accept=".xml,.spn,.zip,application/xml,text/xml"
                    disabled={uploadMut.isPending}
                    className={fileInputCls}
                    onChange={(e) => setUploadFile(e.target.files?.[0] ?? null)}
                  />
                </label>
                {uploadError ? (
                  <div className="app-alert-error text-xs">{uploadError}</div>
                ) : null}
                <div className="flex flex-wrap gap-2">
                  <button
                    type="button"
                    className="app-btn-outline"
                    disabled={uploadMut.isPending || !uploadFile}
                    aria-busy={uploadMut.isPending}
                    onClick={() => {
                      if (!uploadFile || !uploadPanel) return;
                      uploadMut.mutate({ file: uploadFile, market: uploadPanel });
                    }}
                  >
                    <AsyncLabelSpan
                      busy={uploadMut.isPending}
                      idleLabel="Upload file"
                      busyLabel="Uploading…"
                    />
                  </button>
                  <button
                    type="button"
                    className="app-btn-outline"
                    disabled={uploadMut.isPending}
                    onClick={closeUploadPanel}
                  >
                    Cancel
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
                    <div className="text-xs text-zinc-500 dark:text-zinc-400">
                      {uploadStatusText} {uploadMut.isPending ? `${Math.round(uploadProgress)}%` : ""}
                    </div>
                  </div>
                ) : null}
              </div>
            ) : null}

            {refreshProgress > 0 ? (
              <div className="space-y-1">
                <div className="h-2 w-full overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-800">
                  <div
                    className="h-full bg-sky-600 transition-all duration-300 dark:bg-sky-500"
                    style={{ width: `${refreshProgress}%` }}
                  />
                </div>
                <div className="text-xs text-zinc-500 dark:text-zinc-400">
                  {refreshStatusText} {refreshMut.isPending ? `${Math.round(refreshProgress)}%` : ""}
                </div>
              </div>
            ) : null}

            {(() => {
              const lb = q.data.latest_baseline;
              const ex = lb?.exchanges ?? {};
              const nfo = ex.NFO;
              const bfo = ex.BFO;
              const hasAny = Boolean(nfo?.source_file || bfo?.source_file || lb?.source_file);
              if (!hasAny) {
                return (
                  <div className="text-xs text-zinc-500 dark:text-zinc-400">No baseline loaded yet.</div>
                );
              }
              const card = (label: string, m?: BaselineExchangeMeta) =>
                m?.source_file ? (
                  <div key={label} className="rounded-lg border border-zinc-200 p-3 text-xs dark:border-zinc-800">
                    <div className="font-medium text-zinc-800 dark:text-zinc-200">{label}</div>
                    <div>File: {m.source_file}</div>
                    <div>Rows: {m.rows ?? "—"}</div>
                    <div>Refreshed: {m.refreshed_at ?? "—"}</div>
                  </div>
                ) : null;
              return (
                <div className="space-y-2">
                  {card("NSE (NFO)", nfo)}
                  {card("BSE (BFO)", bfo)}
                  {!nfo && !bfo && lb?.source_file ? (
                    <div className="rounded-lg border border-zinc-200 p-3 text-xs dark:border-zinc-800">
                      <div>File: {lb.source_file}</div>
                      <div>Rows: {lb.rows ?? "—"}</div>
                      <div>Refreshed: {lb.refreshed_at ?? "—"}</div>
                    </div>
                  ) : null}
                </div>
              );
            })()}
          </div>
        )}
      </section>
    </AppShell>
  );
}
