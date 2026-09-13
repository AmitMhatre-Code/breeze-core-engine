"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AsyncLabelSpan } from "@/components/ui/AsyncLabelSpan";
import { DatePicker } from "@/components/ui/DatePicker";
import { Select } from "@/components/ui/Select";
import {
  BACKTEST_BOTS,
  startBacktestFetch,
  startBacktestProbe,
  startBacktestReplay,
  type BacktestBot,
  type BacktestOverview,
} from "@/lib/bots-backtest";

type Prices = "real" | "model";

const PRICE_OPTIONS = [
  { value: "real" as const, label: "Real ICICI prices" },
  { value: "model" as const, label: "Model (Black-Scholes)" },
];

/**
 * One selection drives both actions: Fetch downloads what that bot's backtest over that range
 * needs, and Run replays it. Settings are always the bot's saved ones (decided 2026-09-13).
 */
export function BacktestControls({
  overview,
  onStarted,
}: {
  overview: BacktestOverview;
  onStarted: (runId?: string) => void;
}) {
  const queryClient = useQueryClient();
  const [bot, setBot] = useState<BacktestBot>("momentum");
  const [from, setFrom] = useState(overview.history_start);
  const [to, setTo] = useState(overview.default_to);
  const [prices, setPrices] = useState<Prices>("real");
  const [error, setError] = useState<string | null>(null);

  const busy = Boolean(overview.job?.running);
  const saved = overview.saved[bot];
  const body = { bot, from_date: from, to_date: to };

  const refresh = () => void queryClient.invalidateQueries({ queryKey: ["bots-backtest-overview"] });
  const onError = (e: unknown) => setError(e instanceof Error ? e.message : "Could not start");

  const probe = useMutation({ mutationFn: startBacktestProbe, onSuccess: refresh, onError });
  const fetchData = useMutation({ mutationFn: () => startBacktestFetch(body), onSuccess: refresh, onError });
  const run = useMutation({
    mutationFn: () => startBacktestReplay({ ...body, model: prices === "model" }),
    onSuccess: (job) => {
      refresh();
      onStarted(job.run_id);
    },
    onError,
  });

  // Why the broker actions are off, in the order a user can do something about it.
  const brokerBlock = !overview.live
    ? `The broker is in ${overview.broker_mode} mode. Fetching works only on the production instance.`
    : overview.market_hours_block
      ? "Fetching is paused 09:00–15:45 IST on trading days, so it never delays a live order."
      : null;
  const sizedByMargin = bot === "fly" || bot === "expiry";
  const runBlock =
    bot === "expiry" && saved?.enabled === false
      ? "Enable an index in Bot 2's settings first."
      : sizedByMargin && !overview.live
        ? "This bot is sized from today's margin, which needs the live broker."
        : null;
  const pending = probe.isPending || fetchData.isPending || run.isPending;

  return (
    <section className="space-y-3 rounded-[10px] border border-border px-4 py-3.5">
      <div>
        <h2 className="text-heading font-bold text-foreground">Backtest a bot</h2>
        <p className="mt-1 text-table leading-relaxed text-muted">
          Replays the bot&apos;s live rules over past sessions, using its saved settings. To try
          different numbers, change them on the Bots page and run again. Every run is kept below.
        </p>
      </div>

      {error ? <div className="app-alert-error text-xs">{error}</div> : null}

      <div className="grid gap-3 sm:grid-cols-2">
        <label className="space-y-1 text-xs text-muted sm:col-span-2">
          <span>Bot</span>
          <Select value={bot} options={BACKTEST_BOTS} onChange={setBot} ariaLabel="Bot to backtest" disabled={busy} />
        </label>
        <label className="space-y-1 text-xs text-muted">
          <span>From</span>
          <DatePicker value={from} onChange={setFrom} disabled={busy} />
        </label>
        <label className="space-y-1 text-xs text-muted">
          <span>To</span>
          <DatePicker value={to} onChange={setTo} disabled={busy} />
        </label>
        <label className="space-y-1 text-xs text-muted sm:col-span-2">
          <span>Option prices</span>
          <Select value={prices} options={PRICE_OPTIONS} onChange={setPrices} ariaLabel="Option prices" disabled={busy} />
        </label>
      </div>

      {saved ? (
        <div className="app-card-muted space-y-0.5 p-3 text-xs">
          <div className="font-semibold text-foreground">Saved settings · {saved.label}</div>
          {saved.lines.map((line) => (
            <div key={line} className="text-muted">
              {line}
            </div>
          ))}
        </div>
      ) : null}

      <p className="text-xs text-faint">
        History starts {overview.history_start}, the start of the lot size the bots trade today.
        {prices === "model"
          ? " Model prices need only the futures, index and VIX history."
          : " A run that finds uncached option prices says so. Fetch data, then run again."}
      </p>

      <div className="flex flex-wrap items-center gap-2 border-t border-border-soft pt-3">
        <button
          type="button"
          className="app-btn-primary"
          disabled={busy || pending || Boolean(runBlock)}
          onClick={() => {
            setError(null);
            run.mutate();
          }}
        >
          <AsyncLabelSpan busy={run.isPending} busyLabel="Starting…" idleLabel="Run backtest" />
        </button>
        <button
          type="button"
          className="app-btn-secondary"
          disabled={busy || pending || Boolean(brokerBlock)}
          onClick={() => {
            setError(null);
            fetchData.mutate();
          }}
        >
          <AsyncLabelSpan busy={fetchData.isPending} busyLabel="Starting…" idleLabel="Fetch data" />
        </button>
        <button
          type="button"
          className="app-btn-outline"
          disabled={busy || pending || Boolean(brokerBlock)}
          onClick={() => {
            setError(null);
            probe.mutate();
          }}
        >
          <AsyncLabelSpan busy={probe.isPending} busyLabel="Starting…" idleLabel="Probe ICICI" />
        </button>
      </div>
      {runBlock ? <p className="text-xs text-down">{runBlock}</p> : null}
      {brokerBlock ? <p className="text-xs text-muted">{brokerBlock}</p> : null}
    </section>
  );
}
