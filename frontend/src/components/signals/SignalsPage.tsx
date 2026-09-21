"use client";

import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { BacktestPeriodPicker } from "@/components/bots/BacktestPeriodPicker";
import { AsyncLabelSpan } from "@/components/ui/AsyncLabelSpan";
import type { BacktestPeriod } from "@/lib/bots-backtest";
import {
  backtestSentence,
  horizonDetail,
  DURATIONS,
  INDEX_LABEL,
  reasonText,
  SIGNAL_RUNS_QUERY_KEY,
  signalZipHref,
  stateLabel,
  useCancelSignalBacktest,
  useSetNavbarMechanism,
  useSignalBacktestRuns,
  useSetCostLots,
  useSignals,
  useStartSignalBacktest,
  verdictTone,
  type MechanismSection,
  type SignalBacktestRun,
  type SignalIndex,
  type SignalJob,
  type SignalReading,
  type SignalSeries,
  type SignalsOverview,
} from "@/lib/signals";

/**
 * Signals (docs/signals-streamline-plan.md section 6): one section per mechanism, written for
 * someone who is not a quant — what it watches, what it says now, what its last backtest says,
 * whether bots may use it — then one backtest button and the log of every backtest run.
 */
export function SignalsPage() {
  const q = useSignals();
  const qc = useQueryClient();
  const data = q.data;
  const running = Boolean(data?.job && (data.job.running ?? data.job.status === "running") && data.job.kind === "signal");
  // A short run can start and finish between two polls of the log: refresh it the moment the
  // job reports finished, rather than on the log's slow idle cadence.
  const finishedAt = data?.job?.kind === "signal" ? data.job.finished_at : null;
  useEffect(() => {
    if (finishedAt) void qc.invalidateQueries({ queryKey: SIGNAL_RUNS_QUERY_KEY });
  }, [finishedAt, qc]);

  return (
    <div className="space-y-4">
      <header>
        <h1 className="app-text-heading text-lg">Signals</h1>
        <p className="app-text-muted mt-3 max-w-prose text-sm">
          Each signal reads only the one-minute futures data ICICI keeps, so any day&rsquo;s readings can be
          replayed exactly. A signal becomes available to bots once a backtest covering at least{" "}
          {data?.gate_days ?? 30} days has run.
        </p>
      </header>

      {q.isLoading && <p className="app-text-muted text-sm">Loading signals…</p>}
      {q.isError && (
        <p className="text-sm text-down">
          Could not load signals: {(q.error as Error)?.message ?? "unknown error"}
        </p>
      )}

      {data ? (
        <>
          <BacktestPanel data={data} running={running} />
          {data.mechanisms.map((m) => (
            <MechanismCard key={m.id} mechanism={m} navbar={data.navbar_mechanism === m.id} data={data} />
          ))}
          <ActivityLog running={running} gateDays={data.gate_days} />
        </>
      ) : null}
    </div>
  );
}

// --------------------------------------------------------------------------------------
// Backtest
// --------------------------------------------------------------------------------------

function BacktestPanel({ data, running }: { data: SignalsOverview; running: boolean }) {
  const [period, setPeriod] = useState<BacktestPeriod>("last_month");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const start = useStartSignalBacktest();
  const stop = useCancelSignalBacktest();
  const job: SignalJob | null = data.job && data.job.kind === "signal" ? data.job : null;
  const otherRunning = Boolean(data.job && (data.job.running ?? false) && data.job.kind !== "signal");
  const customIncomplete = period === "custom" && (!from || !to);
  const lastLine = job?.log?.length ? job.log[job.log.length - 1] : null;

  return (
    <section className="app-card p-4" aria-labelledby="signals-backtest-title">
      <h2 id="signals-backtest-title" className="app-text-heading">
        Backtest every signal
      </h2>
      <p className="mt-1 max-w-prose text-xs leading-relaxed text-muted">
        One run replays both signals at 1, 5 and 15 minutes on NIFTY and SENSEX and saves every reading, every
        call and what followed it in one zip. Missing history is fetched from ICICI outside market hours.
      </p>
      <CostLotsField data={data} />
      {running && job ? (
        <div className="mt-3 space-y-2 rounded-lg border border-border bg-panel2 p-3 text-xs">
          <div className="flex items-center justify-between gap-2">
            <span className="font-semibold text-foreground">Running…</span>
            <span className="font-mono text-muted">
              {job.from_date === job.to_date ? job.from_date : `${job.from_date} → ${job.to_date}`}
            </span>
          </div>
          {lastLine ? <p className="font-mono text-hint text-muted">{lastLine}</p> : null}
          <button type="button" className="app-btn-secondary" disabled={stop.isPending} onClick={() => stop.mutate()}>
            Stop
          </button>
        </div>
      ) : (
        <>
          <div className="max-w-md">
            <BacktestPeriodPicker
              period={period}
              onPeriod={setPeriod}
              from={from}
              onFrom={setFrom}
              to={to}
              onTo={setTo}
              disabled={start.isPending}
            />
          </div>
          {job && !running && (job.message || job.error) ? (
            <p className={`mt-3 text-xs ${job.error ? "text-down" : "text-foreground"}`}>{job.error ?? job.message}</p>
          ) : null}
          {otherRunning ? <p className="mt-2 text-xs text-down">A bot backtest is running. Wait for it to finish.</p> : null}
          {start.error ? (
            <p className="mt-2 text-xs text-down">
              {start.error instanceof Error ? start.error.message : "Could not start the backtest."}
            </p>
          ) : null}
          <div className="mt-3">
            <button
              type="button"
              className="app-btn-primary"
              disabled={start.isPending || customIncomplete || otherRunning}
              onClick={() =>
                start.mutate({ period, ...(period === "custom" ? { from_date: from, to_date: to } : {}) })
              }
            >
              <AsyncLabelSpan busy={start.isPending} idleLabel="Run backtest" busyLabel="Starting…" />
            </button>
          </div>
        </>
      )}
    </section>
  );
}

/**
 * How big a trade the cost bar is priced on.
 *
 * Most of a one-lot round trip is the flat per-order brokerage, which does not grow when you
 * trade more — so the bar at ten lots is about a third of the bar at one, and scoring everything
 * at one lot quietly fails signals that a real position would clear. This is a fact about the
 * trader, not a tuning knob on the signal: it changes what the numbers are compared against,
 * never what the signals say.
 */
function CostLotsField({ data }: { data: SignalsOverview }) {
  const save = useSetCostLots();
  const [draft, setDraft] = useState(String(data.cost_lots));
  const [seen, setSeen] = useState(data.cost_lots);
  // Follow the server when the stored size changes underneath us, but never mid-save. Adjusted
  // during render rather than in an effect, so the field never flashes the old value first.
  if (seen !== data.cost_lots && !save.isPending) {
    setSeen(data.cost_lots);
    setDraft(String(data.cost_lots));
  }

  const commit = () => {
    const lots = Number(draft);
    if (!Number.isInteger(lots) || lots < 1 || lots > data.max_cost_lots) {
      setDraft(String(data.cost_lots));
      return;
    }
    if (lots !== data.cost_lots) save.mutate(lots);
  };

  return (
    <div className="mt-3 max-w-prose">
      <label htmlFor="signals-cost-lots" className="app-text-label">
        Trade size for costs
      </label>
      <div className="mt-1 flex items-center gap-2">
        <input
          id="signals-cost-lots"
          type="number"
          min={1}
          max={data.max_cost_lots}
          step={1}
          inputMode="numeric"
          className="app-input w-24"
          value={draft}
          disabled={save.isPending}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === "Enter") e.currentTarget.blur();
          }}
        />
        <span className="text-xs text-muted">lot{data.cost_lots === 1 ? "" : "s"}</span>
      </div>
      <p className="mt-1 text-hint text-faint">
        A call has to move the index far enough to pay for one round trip. Most of that cost is the flat
        per-order fee, which does not grow with size, so a bigger trade clears a smaller move. Takes effect on
        the next backtest.
        {save.error ? <span className="text-down"> Could not save that size.</span> : null}
      </p>
    </div>
  );
}

// --------------------------------------------------------------------------------------
// One mechanism
// --------------------------------------------------------------------------------------

const CHIP_TONE: Record<SignalReading["state"], string> = {
  bullish: "bg-up-tint text-up-on-tint",
  bearish: "bg-down-tint text-down-on-tint",
  neutral: "bg-panel2 text-muted",
  unavailable: "border border-border-soft text-faint",
};

function ReadingChip({ reading }: { reading: SignalReading }) {
  const arrow = { bullish: "▲", bearish: "▼", neutral: "●", unavailable: "—" }[reading.state];
  const why = reasonText(reading.reason);
  return (
    <span className="inline-flex flex-col items-start gap-0.5">
      <span className={`inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs font-semibold ${CHIP_TONE[reading.state]}`}>
        <span aria-hidden>{arrow}</span>
        {stateLabel(reading.state)}
      </span>
      {why && reading.state !== "bullish" && reading.state !== "bearish" ? (
        <span className="text-hint text-faint">{why}</span>
      ) : null}
    </span>
  );
}

function seriesFor(m: MechanismSection, duration: number, index: SignalIndex): SignalSeries | undefined {
  return m.series.find((s) => s.duration === duration && s.index === index);
}

function MechanismCard({
  mechanism: m,
  navbar,
  data,
}: {
  mechanism: MechanismSection;
  navbar: boolean;
  data: SignalsOverview;
}) {
  const setNavbar = useSetNavbarMechanism();
  const titleId = `signal-${m.id}-title`;
  const a = m.availability;
  const indices: SignalIndex[] = ["nifty", "sensex"];
  return (
    <section className="app-card p-4" aria-labelledby={titleId}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 id={titleId} className="app-text-heading">
            {m.name}
          </h2>
          <p className="mt-1 max-w-prose text-xs leading-relaxed text-muted">{m.summary}</p>
        </div>
        <div className="flex flex-col items-end gap-2">
          <span
            className={`rounded-md px-2 py-0.5 text-xs font-semibold ${
              a.available ? "bg-up-tint text-up-on-tint" : "border border-border-soft text-muted"
            }`}
            title={a.available ? `Backtested ${a.from} to ${a.to}` : (a.reason ?? undefined)}
          >
            {a.available ? "Available to bots" : `Needs a ${data.gate_days}-day backtest`}
          </span>
          <label className="flex cursor-pointer items-center gap-2 text-xs text-foreground">
            <input
              type="radio"
              name="navbar-signal"
              checked={navbar}
              disabled={setNavbar.isPending}
              onChange={() => setNavbar.mutate(m.id)}
              className="accent-[var(--accent)]"
            />
            Show in navbar ({data.navbar_duration}m)
          </label>
        </div>
      </div>

      <div className="mt-4 overflow-x-auto">
        <table className="w-full min-w-[28rem] text-left text-xs">
          <caption className="sr-only">{m.name}: current readings and last backtest</caption>
          <thead>
            <tr className="text-micro uppercase tracking-[0.06em] text-faint">
              <th className="py-1 pr-3 font-semibold">Duration</th>
              {indices.map((i) => (
                <th key={i} className="py-1 pr-3 font-semibold">
                  {INDEX_LABEL[i]}
                  {i === "sensex" ? <span className="ml-1 normal-case tracking-normal text-faint">(thin data)</span> : null}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {DURATIONS.map((d) => (
              <tr key={d} className="border-t border-border-soft align-top">
                <th scope="row" className="py-2 pr-3 font-semibold text-foreground">
                  {d} min
                </th>
                {indices.map((i) => {
                  const s = seriesFor(m, d, i);
                  if (!s) return <td key={i} />;
                  const tone = verdictTone(s.last_backtest?.best);
                  const detail = horizonDetail(s.last_backtest ?? null);
                  return (
                    <td key={i} className="py-2 pr-3">
                      <ReadingChip reading={s.reading} />
                      <p
                        className={`mt-1 max-w-xs leading-snug ${
                          tone === "up" ? "text-up" : tone === "down" ? "text-down" : "text-muted"
                        }`}
                      >
                        {backtestSentence(s.last_backtest, d)}
                      </p>
                      {detail ? (
                        <p className="mt-0.5 max-w-xs leading-snug text-hint text-faint">{detail}</p>
                      ) : null}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {data.last_backtest ? (
        <p className="mt-2 text-hint text-faint">
          Backtest figures are from the run covering {data.last_backtest.from} to {data.last_backtest.to}. Each
          series is scored 1, 5, 15 and 30 minutes after a call, both with the signal and against it, and the
          best of those is what is shown &mdash; a call need not say most about the length of window that made
          it. &ldquo;After costs&rdquo; means after one round trip of charges and the bid-ask spread, at the size
          set above.
        </p>
      ) : null}
    </section>
  );
}

// --------------------------------------------------------------------------------------
// Activity
// --------------------------------------------------------------------------------------

function runResult(run: SignalBacktestRun): string {
  if (run.status === "failed") return run.error ?? "Failed.";
  if (run.status === "stopped") return "Stopped.";
  if (run.status === "running") return "Running…";
  const series = Object.values(run.series);
  const calls = series.reduce((n, s) => n + (s.calls ?? 0), 0);
  // Counted in both directions: a series that is reliably wrong is one to trade backwards, and
  // counting only "edge" printed "none showed an edge" over runs whose findings were all fades.
  const follow = series.filter((s) => s.tradeable && s.best_direction === "follow").length;
  const fade = series.filter((s) => s.tradeable && s.best_direction === "fade").length;
  const parts: string[] = [];
  if (follow) parts.push(`${follow} worth following`);
  if (fade) parts.push(`${fade} worth going against`);
  const tail = parts.length ? parts.join(" and ") : "none stood out either way";
  return `${calls} calls across ${series.length} series; ${tail}.`;
}

function ActivityLog({ running, gateDays }: { running: boolean; gateDays: number }) {
  const q = useSignalBacktestRuns(running);
  const runs = q.data?.runs ?? [];
  return (
    <section className="app-card p-4" aria-labelledby="signals-activity-title">
      <h2 id="signals-activity-title" className="app-text-heading">
        Activity
      </h2>
      <p className="mt-1 text-xs text-muted">
        Every signal backtest, newest first. Each zip holds a summary, and for NIFTY and SENSEX every bar replayed,
        every minute&rsquo;s reading and every call with what the index did next.
      </p>
      {runs.length === 0 ? (
        <p className="mt-3 text-xs text-faint">No signal backtests yet.</p>
      ) : (
        <div className="mt-3 overflow-x-auto">
          <table className="w-full min-w-[36rem] text-left text-xs">
            <thead>
              <tr className="text-micro uppercase tracking-[0.06em] text-faint">
                <th className="py-1 pr-3 font-semibold">Run</th>
                <th className="py-1 pr-3 font-semibold">Period</th>
                <th className="py-1 pr-3 font-semibold">Result</th>
                <th className="py-1 pr-3 font-semibold">For bots</th>
                <th className="py-1 font-semibold">Files</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.id} className="border-t border-border-soft align-top">
                  <td className="py-2 pr-3 font-mono text-muted">{r.triggered_at.replace("T", " ").slice(0, 16)}</td>
                  <td className="py-2 pr-3">
                    {r.from === r.to ? r.from : `${r.from} → ${r.to}`}
                    <span className="block text-hint text-faint">{r.range_days ?? "—"} days</span>
                  </td>
                  <td className={`py-2 pr-3 ${r.status === "failed" ? "text-down" : "text-foreground"}`}>
                    {runResult(r)}
                    {r.notes.length ? (
                      <span className="mt-0.5 block text-hint text-faint">{r.notes.join(" ")}</span>
                    ) : null}
                  </td>
                  <td className="py-2 pr-3">
                    {r.counts_for_gate ? (
                      <span className="text-up">Counts</span>
                    ) : (
                      <span className="text-faint" title={`A run must cover at least ${gateDays} days`}>
                        {r.status === "completed" ? `Under ${gateDays} days` : "—"}
                      </span>
                    )}
                  </td>
                  <td className="py-2">
                    {r.has_zip ? (
                      <a className="app-link" href={signalZipHref(r.id)}>
                        Download (.zip)
                      </a>
                    ) : (
                      <span className="text-faint">—</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
