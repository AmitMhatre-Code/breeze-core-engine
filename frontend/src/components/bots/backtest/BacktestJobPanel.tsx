"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { formatApiDateTime } from "@/lib/format-iso-date";
import { cancelBacktestJob, type BacktestJob } from "@/lib/bots-backtest";

const KIND_LABELS: Record<string, string> = {
  probe: "ICICI probe",
  fetch: "Price history fetch",
  replay: "Backtest run",
};

export function BacktestJobPanel({ job }: { job: BacktestJob }) {
  const queryClient = useQueryClient();
  const stop = useMutation({
    mutationFn: cancelBacktestJob,
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["bots-backtest-overview"] }),
  });
  const tone =
    job.status === "completed" ? "text-up" : job.status === "running" ? "text-accent-strong" : "text-down";

  return (
    <section className="space-y-2 rounded-[10px] border border-border px-4 py-3.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-heading font-bold text-foreground">{KIND_LABELS[job.kind] ?? job.kind}</h2>
          <p className="text-xs text-muted">
            <span className={tone}>{job.running ? "running" : job.status}</span>
            {" · started "}
            {formatApiDateTime(job.started_at)}
            {job.kind !== "replay" ? ` · ${job.calls} ICICI calls` : ""}
            {job.from_date ? ` · ${job.from_date} → ${job.to_date}` : ""}
          </p>
        </div>
        {job.running && job.kind !== "replay" ? (
          <button type="button" className="app-btn-danger" disabled={stop.isPending} onClick={() => stop.mutate()}>
            {stop.isPending ? "Stopping…" : "Stop"}
          </button>
        ) : null}
      </div>
      {job.message ? <p className="text-sm text-foreground">{job.message}</p> : null}
      {job.error ? <div className="app-alert-error text-xs">{job.error}</div> : null}
      {job.log.length ? (
        <pre className="max-h-56 overflow-auto rounded-md border border-border-soft bg-panel2 p-2 font-mono text-[11px] leading-relaxed text-muted">
          {job.log.slice(-60).join("\n")}
        </pre>
      ) : null}
    </section>
  );
}
