"use client";

import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BACKTEST_JOB_KEY,
  cancelBacktestJob,
  describePhase,
  fetchBacktestJobStatus,
  formatDuration,
  quietVerdict,
} from "@/lib/bots-backtest";

/** Seconds since the last poll landed, ticking once a second so the ages below move between
 *  polls. The ages themselves come from the server's own clock; only this delta is local. */
function useSecondsSince(at: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(id);
  }, []);
  return at ? Math.max(0, Math.floor((now - at) / 1_000)) : 0;
}

/**
 * A running backtest's live state, opened from its Activity row: what it is doing, how long
 * since it last showed any sign of progress, the memory it is running against, and its log.
 *
 * The question it answers is "is this stuck?", so the silence is the headline number, not the
 * elapsed time: a month's replay is slow and fine, while a job that has said nothing for
 * minutes is the one to look at. Polls only while open and while the job runs.
 */
export function BacktestProgress({ runId }: { runId: string }) {
  const qc = useQueryClient();
  const status = useQuery({
    queryKey: BACKTEST_JOB_KEY,
    queryFn: fetchBacktestJobStatus,
    refetchInterval: (q) => (q.state.data?.job?.running ? 2_000 : false),
  });
  const stop = useMutation({
    mutationFn: cancelBacktestJob,
    onSuccess: () => void qc.invalidateQueries({ queryKey: BACKTEST_JOB_KEY }),
  });
  const since = useSecondsSince(status.dataUpdatedAt);

  const job = status.data?.job ?? null;
  const live = Boolean(job && job.running && job.run_id === runId);

  // The row says running but the job does not: it finished since the table was fetched, or a
  // restart killed it (the job poll has just closed that row as interrupted). Either way the
  // table is what is stale, so refresh it and let the row show the real outcome.
  const settled = status.isSuccess && !live;
  useEffect(() => {
    if (settled) void qc.invalidateQueries({ queryKey: ["bots", "runs"] });
  }, [settled, qc]);

  const logRef = useRef<HTMLOListElement>(null);
  const lines = live ? (job?.log ?? []) : [];
  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [lines.length]);

  if (status.isLoading) return <p className="app-text-muted px-3 py-2 text-xs">Loading progress…</p>;
  if (status.isError) {
    return (
      <p className="px-3 py-2 text-xs text-down">
        Could not load progress: {(status.error as Error)?.message ?? "unknown error"}
      </p>
    );
  }
  if (!live || !job) {
    return <p className="app-text-muted px-3 py-2 text-xs">No longer running. Updating the row…</p>;
  }

  const elapsed = (job.elapsed_seconds ?? 0) + since;
  const quiet = (job.quiet_seconds ?? 0) + since;
  const verdict = quietVerdict(quiet, job.phase);
  const mem = status.data?.memory;
  const memShare = mem ? mem.held_bytes / mem.cap_bytes : null;
  const stopping = stop.isSuccess || stop.isPending;

  return (
    <div className="space-y-2 px-3 py-2.5 text-xs">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <span className="font-semibold text-foreground">{describePhase(job)}</span>
        <button
          type="button"
          className="app-btn-secondary px-2.5 py-1 text-xs"
          disabled={stopping}
          onClick={() => stop.mutate()}
        >
          {stopping ? "Stopping…" : "Stop backtest"}
        </button>
      </div>

      <dl className="grid grid-cols-2 gap-x-4 gap-y-1 font-mono text-[11px] sm:grid-cols-4">
        <div>
          <dt className="text-faint">Running for</dt>
          <dd className="tabular-nums">{formatDuration(elapsed)}</dd>
        </div>
        <div>
          <dt className="text-faint">Last update</dt>
          <dd className={`tabular-nums ${verdict ? "text-amber-accent" : ""}`}>
            {formatDuration(quiet)} ago
          </dd>
        </div>
        <div>
          <dt className="text-faint">ICICI calls</dt>
          <dd className="tabular-nums">{job.calls}</dd>
        </div>
        <div>
          <dt className="text-faint">Memory</dt>
          <dd
            className={`tabular-nums ${memShare !== null && mem && memShare >= mem.stop_at - 0.1 ? "text-amber-accent" : ""}`}
          >
            {mem && memShare !== null
              ? `${(mem.held_bytes / 1e9).toFixed(2)} / ${(mem.cap_bytes / 1e9).toFixed(2)} GB (${Math.round(memShare * 100)}%)`
              : "—"}
          </dd>
        </div>
      </dl>

      {verdict ? <p className="leading-relaxed text-amber-accent">{verdict}</p> : null}
      {mem ? (
        <p className="text-hint text-faint">
          The backtest stops itself at {Math.round(mem.stop_at * 100)}% memory rather than be killed part-way.
        </p>
      ) : null}
      {stop.error ? (
        <p className="text-down">
          {stop.error instanceof Error ? stop.error.message : "Could not stop the backtest."}
        </p>
      ) : null}

      {lines.length > 0 ? (
        <ol
          ref={logRef}
          aria-label="Backtest log"
          className="max-h-48 overflow-y-auto rounded border border-border bg-panel px-2 py-1.5 font-mono text-[11px] leading-relaxed text-muted"
        >
          {lines.map((line, i) => (
            <li key={i} className="whitespace-pre-wrap break-words">
              {line}
            </li>
          ))}
        </ol>
      ) : null}
    </div>
  );
}
