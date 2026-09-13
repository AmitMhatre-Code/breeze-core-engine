"use client";

import { useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { AppShell } from "@/components/layout/AppShell";
import { BacktestCompare } from "@/components/bots/backtest/BacktestCompare";
import { BacktestControls } from "@/components/bots/backtest/BacktestControls";
import { BacktestDataPanel } from "@/components/bots/backtest/BacktestDataPanel";
import { BacktestJobPanel } from "@/components/bots/backtest/BacktestJobPanel";
import { BacktestRunDetail } from "@/components/bots/backtest/BacktestRunDetail";
import { BacktestRunsTable } from "@/components/bots/backtest/BacktestRunsTable";
import { fetchBacktestOverview } from "@/lib/bots-backtest";

export default function BacktestPage() {
  const [selectedRun, setSelectedRun] = useState<string | null>(null);
  const q = useQuery({
    queryKey: ["bots-backtest-overview"],
    queryFn: fetchBacktestOverview,
    // Poll only while a job runs: a fetch can take many minutes of paced calls.
    refetchInterval: (query) => (query.state.data?.job?.running ? 3000 : false),
  });
  const overview = q.data;
  const shownRun = selectedRun ?? overview?.runs[0]?.id ?? null;

  return (
    <AppShell contentWidth="wide">
      <div className="space-y-4">
        <header>
          <Link href="/bots" className="text-xs font-medium text-accent-strong hover:underline">
            ← Bots
          </Link>
          <h1 className="app-text-heading mt-1 text-lg">Backtest</h1>
          <p className="app-text-muted mt-1 max-w-prose text-sm">
            Replays a bot&apos;s rules over past sessions on ICICI&apos;s real option prices. This tests
            the strategy&apos;s rules against real history. Simulation mode tests execution. The bid-ask
            spread isn&apos;t in the history, so it&apos;s modelled from what Simulation has observed.
          </p>
        </header>

        {q.isLoading && <p className="app-text-muted text-sm">Loading…</p>}
        {q.isError && (
          <p className="text-sm text-down">Could not load the backtest page: {(q.error as Error)?.message}</p>
        )}

        {overview ? (
          <>
            <div className="grid gap-4 lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
              <BacktestControls overview={overview} onStarted={(id) => id && setSelectedRun(id)} />
              <BacktestDataPanel overview={overview} />
            </div>
            {overview.job ? <BacktestJobPanel job={overview.job} /> : null}
            <BacktestRunsTable runs={overview.runs} selectedId={shownRun} onSelect={setSelectedRun} />
            {shownRun ? (
              <BacktestRunDetail key={shownRun} runId={shownRun} onDeleted={() => setSelectedRun(null)} />
            ) : null}
            <BacktestCompare defaultDate={overview.default_to} />
          </>
        ) : null}
      </div>
    </AppShell>
  );
}
