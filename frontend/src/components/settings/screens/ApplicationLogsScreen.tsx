"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { SettingsScreenHeader } from "@/components/settings/SettingsScreenHeader";
import { apiClient } from "@/lib/api-client";
import { getBackendBaseUrl } from "@/lib/config";

type LogFileSummary = {
  name: string;
  size_bytes: number;
  modified_at: number;
};

type LogsStatus = {
  enabled: boolean;
  retention_days: number;
  level: string;
  files: LogFileSummary[];
  total_bytes: number;
};

const DAY_OPTIONS = [1, 3, 7, 14, 30];

function DocIcon() {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.9"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <path d="M14 2v6h6" />
      <path d="M8 13h8M8 17h5" />
    </svg>
  );
}

function formatBytes(bytes: number): string {
  if (bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB"];
  const exponent = Math.min(
    units.length - 1,
    Math.floor(Math.log(bytes) / Math.log(1024)),
  );
  const value = bytes / Math.pow(1024, exponent);
  return `${value >= 10 || exponent === 0 ? Math.round(value) : value.toFixed(1)} ${units[exponent]}`;
}

function formatTimestamp(epochSeconds: number): string {
  return new Date(epochSeconds * 1000).toLocaleString();
}

export function ApplicationLogsScreen() {
  const [days, setDays] = useState(7);

  const status = useQuery({
    queryKey: ["settings", "application-logs", days],
    queryFn: ({ signal }) =>
      apiClient.get<LogsStatus>(`/diagnostics/logs/status?days=${days}`, signal),
  });

  const data = status.data;
  const nothingToDownload = !data || data.files.length === 0;

  // A plain link, not a fetch: the browser handles the Content-Disposition attachment
  // and streams to disk without the bundle passing through JS memory first.
  const downloadHref = `${getBackendBaseUrl()}/diagnostics/logs/download?days=${days}`;

  return (
    <div>
      <SettingsScreenHeader
        icon={<DocIcon />}
        title="Application Logs"
        description="Download this deployment's application logs to inspect performance or share when reporting a problem."
      />

      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs font-medium text-muted">Period</span>
          {DAY_OPTIONS.map((option) => (
            <button
              key={option}
              type="button"
              onClick={() => setDays(option)}
              className={[
                "rounded-full border px-3 py-1.5 text-xs font-medium transition",
                option === days
                  ? "border-accent-strong bg-accent-tint text-accent-strong"
                  : "border-border bg-panel text-muted hover:text-foreground",
              ].join(" ")}
            >
              {option === 1 ? "Last day" : `Last ${option} days`}
            </button>
          ))}
        </div>

        {status.isError ? (
          <p className="text-xs text-down">
            Could not read log status. {(status.error as Error).message}
          </p>
        ) : null}

        {data && !data.enabled ? (
          <p className="rounded-[8px] border border-border bg-panel2 px-3 py-2 text-xs text-muted">
            Log recording is disabled on this deployment (<code>LOG_SINK</code>), so
            there is nothing to download.
          </p>
        ) : null}

        <div className="rounded-[10px] border border-border bg-panel">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-4 py-3">
            <div className="min-w-0">
              <p className="text-sm font-semibold text-foreground">
                {status.isPending
                  ? "Checking…"
                  : `${data?.files.length ?? 0} file${data?.files.length === 1 ? "" : "s"} · ${formatBytes(data?.total_bytes ?? 0)}`}
              </p>
              <p className="mt-0.5 text-xs text-muted">
                {data
                  ? `Recorded at ${data.level} and kept for ${data.retention_days} days.`
                  : null}
              </p>
            </div>
            <a
              href={downloadHref}
              // Not `download`: the filename comes from the server's
              // Content-Disposition, which carries the timestamp and window.
              className={[
                "inline-flex items-center gap-2 rounded-[8px] px-3 py-2 text-xs font-semibold transition",
                nothingToDownload
                  ? "pointer-events-none border border-border bg-panel2 text-muted"
                  : "bg-accent-strong text-accent-ink hover:opacity-90",
              ].join(" ")}
              aria-disabled={nothingToDownload}
            >
              Download .zip
            </a>
          </div>

          {data && data.files.length > 0 ? (
            <ul className="divide-y divide-border">
              {data.files.map((file) => (
                <li
                  key={file.name}
                  className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5"
                >
                  <span className="font-mono text-xs text-foreground">{file.name}</span>
                  <span className="text-xs text-muted">
                    {formatBytes(file.size_bytes)} · {formatTimestamp(file.modified_at)}
                  </span>
                </li>
              ))}
            </ul>
          ) : status.isPending ? null : (
            <p className="px-4 py-3 text-xs text-muted">No log files in this period.</p>
          )}
        </div>

        <p className="text-xs leading-relaxed text-muted">
          The bundle covers the whole deployment, not just your own activity, and
          contains account identifiers and client IP addresses. Treat it as sensitive
          when sharing.
        </p>
      </div>
    </div>
  );
}
