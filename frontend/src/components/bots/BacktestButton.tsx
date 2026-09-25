"use client";

import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Modal } from "@/components/ui/Modal";
import { AsyncLabelSpan } from "@/components/ui/AsyncLabelSpan";
import { BacktestPeriodPicker } from "@/components/bots/BacktestPeriodPicker";
import {
  BACKTEST_JOB_KEY as JOB_KEY,
  BACKTEST_SLUG,
  cancelBacktestJob,
  fetchBacktestJobStatus,
  startBotBacktest,
  type BacktestPeriod,
} from "@/lib/bots-backtest";
import { BOT_META, type BotType } from "@/lib/use-bots";

/** A clock winding backwards: this bot's rules, run over past sessions. */
function HistoryIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="size-4"
      aria-hidden
    >
      <path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" />
      <path d="M3 3v5h5" />
      <path d="M12 7v5l4 2" />
    </svg>
  );
}

/**
 * The card's backtest: a clock icon, and a dialog that asks for one thing — the period
 * (design-decisions #36). Settings, lot sizes, margin and prices are not asked: the replay uses
 * the bot's saved settings, today's lot sizes and margin, and real ICICI prices, fetching what
 * is missing within the day's call budget. The result lands in Activity like any live run.
 *
 * Renders nothing for a bot with no replay: an entry point that leads to "this bot cannot be
 * backtested" is worse than no entry point.
 */
export function BacktestButton({ botType, className }: { botType: BotType; className: string }) {
  const bot = BACKTEST_SLUG[botType];
  const [open, setOpen] = useState(false);
  if (!bot) return null;
  const title = BOT_META[botType]?.title ?? botType;
  return (
    <>
      <button
        type="button"
        aria-label={`Backtest ${title}`}
        title="Backtest"
        onClick={() => setOpen(true)}
        className={className}
      >
        <HistoryIcon />
      </button>
      {open ? <BacktestDialog botType={botType} onClose={() => setOpen(false)} /> : null}
    </>
  );
}

function BacktestDialog({ botType, onClose }: { botType: BotType; onClose: () => void }) {
  const bot = BACKTEST_SLUG[botType]!;
  const title = BOT_META[botType]?.title ?? botType;
  const qc = useQueryClient();
  const startRef = useRef<HTMLButtonElement>(null);
  const [period, setPeriod] = useState<BacktestPeriod>("last_week");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [startedRunId, setStartedRunId] = useState<string | null>(null);

  const status = useQuery({
    queryKey: JOB_KEY,
    queryFn: fetchBacktestJobStatus,
    // Poll only while this dialog is watching a job it started.
    refetchInterval: (q) => (q.state.data?.job?.running ? 2_000 : false),
  });
  const job = status.data?.job ?? null;
  const ours = job && startedRunId && job.run_id === startedRunId ? job : null;
  const running = Boolean(ours?.running);
  const otherRunning = Boolean(job?.running && !ours);

  const start = useMutation({
    mutationFn: () =>
      startBotBacktest({
        bot,
        period,
        ...(period === "custom" ? { from_date: from, to_date: to } : {}),
      }),
    onSuccess: (j) => {
      setStartedRunId(j.run_id ?? null);
      void qc.invalidateQueries({ queryKey: JOB_KEY });
      void qc.invalidateQueries({ queryKey: ["bots", "runs"] });
    },
  });
  const stop = useMutation({ mutationFn: cancelBacktestJob });

  // When our job finishes, the Activity table and the card's "last backtest" line change.
  const finished = ours && !ours.running;
  useEffect(() => {
    if (finished) void qc.invalidateQueries({ queryKey: ["bots", "runs"] });
  }, [finished, qc]);

  const customIncomplete = period === "custom" && (!from || !to);
  const budget = status.data?.budget;
  const lastLine = ours?.log?.length ? ours.log[ours.log.length - 1] : null;

  return (
    <Modal
      open
      onClose={onClose}
      pending={start.isPending}
      titleId="backtest-dialog-title"
      initialFocusRef={startRef}
      panelClassName="w-full max-w-md rounded-xl border border-border bg-panel p-5 shadow-pop"
    >
      <h2 id="backtest-dialog-title" className="app-text-heading">
        Backtest {title}
      </h2>
      <p className="mt-1 text-xs leading-relaxed text-muted">
        Replays this bot&rsquo;s saved settings on real ICICI prices, sized with today&rsquo;s lot sizes and margin.
        The result appears in Activity.
      </p>

      {!ours ? (
        <>
          <BacktestPeriodPicker
            period={period}
            onPeriod={setPeriod}
            from={from}
            onFrom={setFrom}
            to={to}
            onTo={setTo}
            disabled={start.isPending}
          />

          <p className="mt-3 text-hint leading-relaxed text-faint">
            Missing history is fetched from ICICI outside market hours
            {budget ? `, within today's backtest budget (${budget.remaining_today} of ${budget.daily_calls} calls left)` : ""}.
            Anything that can&rsquo;t be fetched is skipped and reported, never modelled.
          </p>
          {budget && budget.remaining_today === 0 ? (
            <p className="mt-2 text-xs leading-relaxed text-amber-accent">
              Today&rsquo;s backtest budget is spent, so this will replay on cached data only and
              any gap stays as it is. The budget resets at IST midnight, or raise it in{" "}
              <a href="/settings/api-usage" className="underline underline-offset-2">
                Settings &rarr; API Usage
              </a>
              .
            </p>
          ) : null}
          {otherRunning ? (
            <p className="mt-2 text-xs text-down">Another backtest is running. Wait for it to finish.</p>
          ) : null}
          {start.error ? (
            <p className="mt-2 text-xs text-down">
              {start.error instanceof Error ? start.error.message : "Could not start the backtest."}
            </p>
          ) : null}

          <div className="mt-4 flex justify-end gap-2">
            <button type="button" className="app-btn-secondary" onClick={onClose} disabled={start.isPending}>
              Cancel
            </button>
            <button
              ref={startRef}
              type="button"
              className="app-btn-primary"
              disabled={start.isPending || customIncomplete || otherRunning}
              onClick={() => start.mutate()}
            >
              <AsyncLabelSpan busy={start.isPending} idleLabel="Run backtest" busyLabel="Starting…" />
            </button>
          </div>
        </>
      ) : (
        <>
          <div className="mt-4 space-y-2 rounded-lg border border-border bg-panel2 p-3 text-xs">
            <div className="flex items-center justify-between gap-2">
              <span className="font-semibold text-foreground">
                {running ? "Running…" : ours.status === "completed" ? "Done" : ours.status === "failed" ? "Failed" : "Stopped"}
              </span>
              <span className="font-mono text-muted">
                {ours.from_date === ours.to_date ? ours.from_date : `${ours.from_date} → ${ours.to_date}`}
              </span>
            </div>
            {running && lastLine ? <p className="font-mono text-hint text-muted">{lastLine}</p> : null}
            {!running && (ours.message || ours.error) ? (
              <p className={ours.error ? "text-down" : "text-foreground"}>{ours.error ?? ours.message}</p>
            ) : null}
            {ours.calls ? <p className="text-hint text-faint">{ours.calls} ICICI call(s) spent fetching.</p> : null}
          </div>
          <div className="mt-4 flex justify-end gap-2">
            {running ? (
              <button type="button" className="app-btn-secondary" disabled={stop.isPending} onClick={() => stop.mutate()}>
                Stop
              </button>
            ) : null}
            <button type="button" className="app-btn-primary" onClick={onClose}>
              {running ? "Close — keeps running" : "Close"}
            </button>
          </div>
          {!running ? (
            <p className="mt-2 text-right text-hint text-faint">Its trades and audit trail are in Activity.</p>
          ) : null}
        </>
      )}
    </Modal>
  );
}
