"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CampaignCard } from "@/components/condor/CondorCampaignPanel";
import { CondorSettingsForm } from "@/components/condor/CondorSettingsForm";
import { Checkbox } from "@/components/ui/Checkbox";
import { BACKTEST_JOB_KEY, cancelBacktestJob, fetchBacktestJobStatus } from "@/lib/bots-backtest";
import {
  backtestCashPnl,
  formatInr,
  useCondorBacktestDefaults,
  useCondorBacktestRun,
  useCondorBacktestRuns,
  useCondorCampaigns,
  useCreateCampaign,
  useStartCondorBacktest,
  type CondorBacktestRun,
  type CondorSettings,
} from "@/lib/condor";

/** Iron Condors (docs/dynamic-iron-condor-plan.md): the campaigns, a new one, and backtests of
 *  the rules on ICICI's traded prices. A campaign's own card lives on its Portfolio group. */
export function CondorsPage() {
  return (
    <div className="space-y-4">
      <header>
        <h1 className="app-text-heading text-lg">Iron Condors</h1>
        <p className="app-text-muted mt-3 max-w-prose text-sm">
          A campaign manages one NIFTY condor across rolls and expiries: it is checked at the start and end of
          each day, the rules suggest tranches, rolls and exits, and a ledger keeps every rupee. Adopt an existing
          group from its Portfolio row, or start an empty campaign here and enter it as tranches come due.
        </p>
      </header>
      <CampaignsSection />
      <BacktestSection />
    </div>
  );
}

function CampaignsSection() {
  const [showClosed, setShowClosed] = useState(false);
  const list = useCondorCampaigns(showClosed);
  const defaults = useCondorBacktestDefaults();
  const [draft, setDraft] = useState<CondorSettings | null>(null);
  const [creating, setCreating] = useState(false);
  const create = useCreateCampaign();
  const value = draft ?? defaults.data?.settings ?? null;

  return (
    <section className="app-card p-4" aria-labelledby="condor-campaigns">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="condor-campaigns" className="app-text-heading">
          Campaigns
        </h2>
        <label className="flex items-center gap-2 text-xs text-muted">
          <Checkbox checked={showClosed} onChange={setShowClosed} aria-label="Show closed campaigns" />
          Show closed
        </label>
      </div>
      <ul className="mt-3 divide-y divide-border-soft text-sm">
        {(list.data?.campaigns ?? []).map((c) => (
          <li key={c.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
            <span>
              {c.origin === "bot" ? "Bot · " : ""}{c.mode === "paper" ? "Paper · " : ""}NIFTY · cycle <span className="font-mono">{c.cycle?.expiry ?? c.cycles.at(-1)?.expiry ?? "—"}</span> ·{" "}
              {c.status === "active" ? "active" : `closed (${c.close_reason ?? "—"})`} · ceiling{" "}
              {formatInr(c.settings.margin_ceiling_inr)}
            </span>
            {c.status === "active" && c.mode !== "paper" ? (
              <Link className="app-link text-xs" href="/portfolio">
                Open its card on Portfolio
              </Link>
            ) : null}
            {c.status === "active" && c.mode === "paper" ? (
              // A paper campaign has no Portfolio row, so its card lives here.
              <div className="mt-2 w-full max-w-4xl rounded-lg border border-border bg-panel2 p-3">
                <CampaignCard campaign={c} request={null} />
              </div>
            ) : null}
          </li>
        ))}
        {list.data && !list.data.campaigns.length ? <li className="py-2 text-muted">No campaigns.</li> : null}
      </ul>
      {!creating ? (
        <button type="button" className="app-btn-secondary mt-3" onClick={() => setCreating(true)}>
          New empty campaign…
        </button>
      ) : value ? (
        <div className="mt-3 max-w-4xl space-y-3 rounded-lg border border-border bg-panel2 p-3">
          <p className="text-xs text-muted">
            Picks the earliest listed expiry still at or beyond the tranche cut-off. Tranches are suggested at the
            entry check once they fall due; until the campaign holds legs, its card appears once you place the first
            tranche and its group shows on Portfolio.
          </p>
          <CondorSettingsForm value={value} onChange={setDraft} disabled={create.isPending} />
          {create.isError ? <p className="text-sm text-down">{(create.error as Error).message}</p> : null}
          <div className="flex gap-2">
            <button type="button" className="app-btn-primary" disabled={create.isPending} onClick={() => create.mutate({ settings: value }, { onSuccess: () => setCreating(false) })}>
              Start campaign
            </button>
            <button type="button" className="app-btn-secondary" onClick={() => setCreating(false)}>
              Cancel
            </button>
          </div>
        </div>
      ) : null}
    </section>
  );
}

function BacktestSection() {
  const qc = useQueryClient();
  const defaults = useCondorBacktestDefaults();
  const [draft, setDraft] = useState<CondorSettings | null>(null);
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [exitAction, setExitAction] = useState<"time_roll" | "close">("time_roll");
  const [lots, setLots] = useState("");
  const [openRun, setOpenRun] = useState<string | null>(null);
  const start = useStartCondorBacktest();
  const status = useQuery({
    queryKey: BACKTEST_JOB_KEY,
    queryFn: fetchBacktestJobStatus,
    refetchInterval: (q) => (q.state.data?.job?.running ? 2_000 : 30_000),
  });
  const stop = useMutation({
    mutationFn: cancelBacktestJob,
    onSuccess: () => void qc.invalidateQueries({ queryKey: BACKTEST_JOB_KEY }),
  });
  const job = status.data?.job ?? null;
  const running = Boolean(job?.running);
  const runs = useCondorBacktestRuns(running);
  const finishedAt = job?.finished_at ?? null;
  useEffect(() => {
    if (finishedAt) void qc.invalidateQueries({ queryKey: ["condor", "backtest", "runs"] });
  }, [finishedAt, qc]);

  const value = draft ?? defaults.data?.settings ?? null;
  const live = defaults.data?.live ?? false;
  const lotsNeeded = !live && !(Number(lots) >= 1);
  const lastLine = job?.log?.length ? job.log[job.log.length - 1] : null;

  return (
    <section className="app-card p-4" aria-labelledby="condor-backtest">
      <h2 id="condor-backtest" className="app-text-heading">
        Backtest the rules
      </h2>
      <p className="mt-1 max-w-prose text-xs leading-relaxed text-muted">
        Replays the same engine a campaign runs, at the two daily checks, on ICICI&rsquo;s traded 5-minute option
        prices (history from {defaults.data?.history_start ?? "2026-01-01"}). Spreads are modelled and fills take a
        further slippage; charges are the calibrated contract-note model. Missing prices are fetched outside market
        hours within today&rsquo;s backtest budget; a run that cannot fetch what it needs stops and says where.
      </p>
      {running && job ? (
        <div className="mt-3 space-y-2 rounded-lg border border-border bg-panel2 p-3 text-xs">
          <div className="flex items-center justify-between gap-2">
            <span className="font-semibold">Running {job.kind === "condor" ? "condor backtest" : `a ${job.kind} job`}…</span>
            <span className="font-mono text-muted">{job.day ?? ""}</span>
          </div>
          {lastLine ? <p className="font-mono text-muted">{lastLine}</p> : null}
          <button type="button" className="app-btn-secondary" disabled={stop.isPending} onClick={() => stop.mutate()}>
            Stop
          </button>
        </div>
      ) : value ? (
        <div className="mt-3 max-w-4xl space-y-3">
          <CondorSettingsForm value={value} onChange={setDraft} disabled={start.isPending} />
          <div className="flex flex-wrap items-end gap-3 text-sm">
            <label className="flex flex-col gap-1">
              From
              <input type="date" className="app-input" value={from} min={defaults.data?.history_start} onChange={(e) => setFrom(e.target.value)} />
            </label>
            <label className="flex flex-col gap-1">
              To
              <input type="date" className="app-input" value={to} onChange={(e) => setTo(e.target.value)} />
            </label>
            <label className="flex flex-col gap-1">
              At exit DTE
              <select className="app-input" value={exitAction} onChange={(e) => setExitAction(e.target.value as "time_roll" | "close")}>
                <option value="time_roll">Time-roll to the next cycle</option>
                <option value="close">Close; start fresh by schedule</option>
              </select>
            </label>
            <label className="flex flex-col gap-1" title={live ? "Blank sizes from today's margin, as the other backtests do." : "This instance has no margin calculator: enter the lots."}>
              Lots per tranche{live ? " (blank = from today's margin)" : ""}
              <input type="number" min={1} className="app-input w-28 text-right font-mono" value={lots} onChange={(e) => setLots(e.target.value)} />
            </label>
            <button
              type="button"
              className="app-btn-primary"
              disabled={start.isPending || !from || !to || lotsNeeded}
              onClick={() =>
                start.mutate({
                  settings: value,
                  from_date: from,
                  to_date: to,
                  exit_action: exitAction,
                  lots_per_tranche: Number(lots) >= 1 ? Number(lots) : null,
                })
              }
            >
              Run backtest
            </button>
          </div>
          {start.isError ? <p className="text-sm text-down">{(start.error as Error).message}</p> : null}
        </div>
      ) : null}

      <h3 className="mt-5 text-sm font-semibold">Runs</h3>
      <ul className="mt-2 divide-y divide-border-soft text-sm">
        {(runs.data?.runs ?? []).map((r) => (
          <li key={r.id} className="py-2">
            <button type="button" className="w-full text-left" onClick={() => setOpenRun(openRun === r.id ? null : r.id)}>
              <RunHeadline run={r} />
            </button>
            {openRun === r.id ? <RunDetail id={r.id} /> : null}
          </li>
        ))}
        {runs.data && !runs.data.runs.length ? <li className="py-2 text-muted">No runs yet.</li> : null}
      </ul>
    </section>
  );
}

function RunHeadline({ run }: { run: CondorBacktestRun }) {
  const s = run.summary;
  const pnl = backtestCashPnl(s);
  return (
    <span className="flex flex-wrap items-baseline justify-between gap-2">
      <span>
        <span className="font-mono">{run.params.from} → {run.params.to}</span> · {run.status}
        {s?.lots_per_tranche ? ` · ${s.lots_per_tranche} lot(s)/tranche` : ""}
      </span>
      <span className="font-mono text-xs">
        <span className={pnl != null && pnl < 0 ? "text-down" : "text-up"}>{formatInr(pnl)}</span>
        {s?.max_drawdown != null ? ` · max DD ${formatInr(-s.max_drawdown)}` : ""}
      </span>
    </span>
  );
}

function RunDetail({ id }: { id: string }) {
  const q = useCondorBacktestRun(id);
  const run = q.data;
  if (!run) return <p className="mt-2 text-xs text-muted">Loading…</p>;
  const s = run.summary ?? {};
  return (
    <div className="mt-2 space-y-3 text-xs">
      {run.error ? <p className="text-down">{run.error}</p> : null}
      {(s.notes ?? []).map((n, i) => (
        <p key={i} className="text-muted">
          {n}
        </p>
      ))}
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <span>Campaigns finished: {s.campaigns_finished ?? 0} ({s.wins ?? 0} won, {s.losses ?? 0} lost)</span>
        <span>Worst campaign: {formatInr(s.worst_campaign_pnl)}</span>
        <span>Rolls: {s.rolls ?? 0}</span>
        <span>Charges: {formatInr(s.charges)}</span>
        <span>Checks replayed: {s.checks_replayed ?? 0}/{s.checks ?? 0}</span>
        <span>ICICI calls: {s.calls ?? 0}</span>
        <span className="sm:col-span-2">Spread model: {s.spread_model ?? "—"}</span>
      </div>
      {s.skipped && Object.keys(s.skipped).length ? (
        <p className="text-muted">
          Skipped checks: {Object.entries(s.skipped).map(([k, v]) => `${k} ${v}`).join(", ")}
        </p>
      ) : null}
      <table className="w-full">
        <thead className="text-muted">
          <tr>
            <th className="text-left font-normal">Campaign</th>
            <th className="text-left font-normal">Cycles</th>
            <th className="text-left font-normal">Ended</th>
            <th className="text-right font-normal">Worst at a check</th>
            <th className="text-right font-normal">Cash P&amp;L</th>
          </tr>
        </thead>
        <tbody className="font-mono">
          {(s.campaigns ?? []).map((c, i) => (
            <tr key={i}>
              <td>{c.started.slice(0, 10)}</td>
              <td>{c.cycles.map((y) => `${y.expiry} (${y.tranches}t/${y.rolls}r ${y.close_reason ?? "open"})`).join(", ")}</td>
              <td>{c.end_reason ?? "—"}</td>
              <td className="text-right">{formatInr(c.worst_pnl_at_check)}</td>
              <td className={`text-right ${c.pnl < 0 ? "text-down" : "text-up"}`}>{formatInr(c.pnl)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <details>
        <summary className="cursor-pointer">Every action ({run.trades?.length ?? 0})</summary>
        <ul className="mt-1 space-y-0.5 font-mono">
          {(run.trades ?? []).map((t, i) => (
            <li key={i}>
              {String(t.at)} {String(t.check)} · {String(t.action)} · {String(t.text ?? "")}
            </li>
          ))}
        </ul>
      </details>
    </div>
  );
}
