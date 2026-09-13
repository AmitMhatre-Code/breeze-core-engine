"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BacktestEquityChart } from "@/components/bots/backtest/BacktestEquityChart";
import {
  deleteBacktestRun,
  downloadBacktestCsv,
  equityCurve,
  exitLabel,
  fetchBacktestRun,
  inr,
  maxDrawdown,
  num,
  tradeTotals,
  type BacktestBot,
  type BacktestTrade,
  type EquityPoint,
  type RunTotals,
} from "@/lib/bots-backtest";

type Column = { key: string; label: string; align?: "right"; render: (t: BacktestTrade) => string };

const time = (v: unknown) => String(v ?? "").slice(0, 16).replace("T", " ");
const price = (v: unknown) => (num(v) == null ? "—" : (num(v) as number).toFixed(2));

const COLUMNS: Record<BacktestBot, Column[]> = {
  momentum: [
    { key: "in", label: "Entry", render: (t) => time(t.entered_at) },
    { key: "out", label: "Exit", render: (t) => time(t.exited_at).slice(11) },
    { key: "contract", label: "Contract", render: (t) => `${t.strike} ${t.right === "call" ? "CE" : "PE"}` },
    { key: "lots", label: "Lots", align: "right", render: (t) => String(t.lots) },
    { key: "buy", label: "Bought", align: "right", render: (t) => price(t.entry_price) },
    { key: "sell", label: "Sold", align: "right", render: (t) => price(t.exit_price) },
    { key: "why", label: "Exit reason", render: (t) => exitLabel(t.exit_reason) },
    { key: "res", label: "Bars", render: (t) => String(t.resolution ?? "") },
  ],
  fly: [
    { key: "in", label: "Entry", render: (t) => time(t.entered_at) },
    { key: "out", label: "Exit", render: (t) => time(t.exited_at).slice(11) },
    { key: "centre", label: "Centre", render: (t) => `${t.atm_strike} ±${t.wing_width}` },
    { key: "lots", label: "Lots", align: "right", render: (t) => String(t.lots) },
    { key: "credit", label: "Credit", align: "right", render: (t) => price(t.net_credit_per_unit) },
    { key: "close", label: "Closed at", align: "right", render: (t) => price(t.exit_cost_per_unit) },
    { key: "why", label: "Exit reason", render: (t) => exitLabel(t.exit_reason) },
  ],
  expiry: [
    { key: "day", label: "Expiry", render: (t) => String(t.day) },
    { key: "what", label: "Strategy", render: (t) => `${t.index} ${String(t.strategy).replace(/_/g, " ")}` },
    {
      key: "strikes",
      label: "Strikes",
      render: (t) =>
        (Array.isArray(t.legs) ? t.legs : [])
          .map((l: { strike?: number; right?: string }) => `${l.strike} ${l.right === "call" ? "CE" : "PE"}`)
          .join(" / "),
    },
    { key: "lots", label: "Lots", align: "right", render: (t) => String(t.lots) },
    { key: "premium", label: "Premium", align: "right", render: (t) => inr(num(t.premium_inr)) },
    { key: "why", label: "Exit", render: (t) => `${exitLabel(t.exit_reason)} ${time(t.exited_at).slice(11)}` },
  ],
};

function Tile({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="app-card-muted p-3">
      <div className="text-micro font-bold uppercase tracking-wide text-faint">{label}</div>
      <div className={`mt-1 font-mono text-lg tabular-nums ${tone ?? "text-foreground"}`}>{value}</div>
    </div>
  );
}

export function BacktestRunDetail({ runId, onDeleted }: { runId: string; onDeleted: () => void }) {
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);
  const q = useQuery({
    queryKey: ["bots-backtest-run", runId],
    queryFn: () => fetchBacktestRun(runId),
    refetchInterval: (query) => (query.state.data?.status === "running" ? 3000 : false),
  });
  const del = useMutation({
    mutationFn: () => deleteBacktestRun(runId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["bots-backtest-overview"] });
      onDeleted();
    },
    onError: (e) => setError(e instanceof Error ? e.message : "Delete failed"),
  });

  const run = q.data;
  const trades = useMemo(() => run?.trades ?? [], [run]);
  const totals = useMemo(() => tradeTotals(trades), [trades]);
  const curve = useMemo(() => equityCurve(trades), [trades]);

  if (q.isLoading || !run) return <p className="text-sm text-muted">Loading run…</p>;
  if (run.status === "running") return <p className="text-sm text-muted">This run is still in progress…</p>;

  const s = run.summary ?? {};
  const waiting = Number(s.days_awaiting_data ?? 0);
  const outside = Number(s.days_outside_history ?? 0);
  const noSpot = Number(s.days_without_spot ?? 0);
  const skippedData = Number(s.skipped_no_data ?? 0) + Number(s.skipped_no_fill ?? 0);
  const footnote = [
    `${Number(s.days_replayed ?? s.expiry_days ?? 0)} days replayed`,
    outside ? `${outside} before the history start` : "",
    noSpot ? `${noSpot} without index prices` : "",
    skippedData ? `${skippedData} entries skipped for missing or untraded prices` : "",
    s.spread_source ? `spread: ${String(s.spread_source)}` : "",
  ]
    .filter(Boolean)
    .join(" · ");

  async function onDownload() {
    setDownloading(true);
    setError(null);
    try {
      await downloadBacktestCsv(runId);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Download failed");
    } finally {
      setDownloading(false);
    }
  }

  return (
    <section className="space-y-4 rounded-[10px] border border-border px-4 py-3.5">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 className="text-heading font-bold text-foreground">{run.params.label ?? run.bot}</h2>
          <p className="text-xs text-muted">
            {run.params.from} → {run.params.to} · {String(s.price_source ?? (run.params.model ? "model" : "real"))}
          </p>
          {run.params.sizing ? <p className="mt-1 text-xs text-muted">Sizing: {run.params.sizing}</p> : null}
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            className="app-btn-outline"
            disabled={downloading || !trades.length}
            onClick={() => void onDownload()}
          >
            {downloading ? "Preparing…" : "Download CSV"}
          </button>
          <button type="button" className="app-btn-danger" disabled={del.isPending} onClick={() => del.mutate()}>
            Delete
          </button>
        </div>
      </div>

      {error ? <div className="app-alert-error text-xs">{error}</div> : null}

      {run.status === "failed" ? (
        // A failed run has no results; zeroed tiles would read as a run that traded nothing.
        <div className="app-alert-error text-xs">{run.error}</div>
      ) : (
        <>
          {waiting ? (
            <p className="app-card-muted p-3 text-xs text-accent-strong">
              {waiting} day(s) are missing from these totals because their option prices aren&apos;t
              cached. Fetch data for this bot and range, then run again.
            </p>
          ) : null}
          <RunResults
            trades={trades}
            totals={totals}
            curve={curve}
            columns={COLUMNS[run.bot] ?? COLUMNS.momentum}
            footnote={footnote}
          />
        </>
      )}
    </section>
  );
}

function RunResults({
  trades,
  totals,
  curve,
  columns,
  footnote,
}: {
  trades: BacktestTrade[];
  totals: RunTotals;
  curve: EquityPoint[];
  columns: Column[];
  footnote: string;
}) {
  const drawdown = maxDrawdown(curve);
  const frictionShare = totals.gross ? ((100 * totals.friction) / Math.abs(totals.gross)).toFixed(1) : null;
  return (
    <>
      <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <Tile label="Net P&L" value={inr(totals.net)} tone={totals.net >= 0 ? "text-up" : "text-down"} />
        <Tile label="Trades" value={String(totals.trades)} />
        <Tile label="Win rate" value={totals.winRate == null ? "—" : `${totals.winRate}%`} />
        <Tile label="Charges" value={inr(totals.friction)} />
        <Tile label="Charges / gross" value={frictionShare == null ? "—" : `${frictionShare}%`} />
        <Tile label="Max drawdown" value={inr(drawdown ? -drawdown : 0)} tone={drawdown ? "text-down" : undefined} />
      </div>

      <BacktestEquityChart points={curve} />

      <p className="text-xs text-faint">{footnote}</p>

      {trades.length ? (
        <div className="app-table-wrap max-h-[28rem]">
          <table className="min-w-full text-left text-table">
            <thead className="app-table-head sticky top-0">
              <tr>
                {columns.map((c) => (
                  <th
                    key={c.key}
                    className={`px-2.5 py-2 font-semibold whitespace-nowrap ${c.align === "right" ? "text-right" : ""}`}
                  >
                    {c.label}
                  </th>
                ))}
                <th className="px-2.5 py-2 text-right font-semibold whitespace-nowrap">Charges</th>
                <th className="px-2.5 py-2 text-right font-semibold whitespace-nowrap">Net</th>
              </tr>
            </thead>
            <tbody>
              {trades.map((t, i) => (
                <tr key={i} className="app-table-row">
                  {columns.map((c) => (
                    <td
                      key={c.key}
                      className={`px-2.5 py-1.5 whitespace-nowrap text-foreground ${
                        c.align === "right" ? "text-right font-mono tabular-nums" : ""
                      }`}
                    >
                      {c.render(t)}
                    </td>
                  ))}
                  <td className="px-2.5 py-1.5 text-right font-mono tabular-nums text-muted">{inr(num(t.friction))}</td>
                  <td
                    className={`px-2.5 py-1.5 text-right font-mono tabular-nums ${
                      (num(t.net_pnl) ?? 0) >= 0 ? "text-up" : "text-down"
                    }`}
                  >
                    {inr(num(t.net_pnl))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </>
  );
}
