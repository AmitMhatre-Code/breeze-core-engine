"use client";

import { useMemo } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { AsyncLabelSpan } from "@/components/ui/AsyncLabelSpan";
import { BacktestEquityChart } from "@/components/bots/BacktestEquityChart";
import {
  comparisonRows,
  condorComparisonRows,
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
  type ComparisonRow,
  type CondorComparisonRow,
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
          // A hedged shape's wing is marked "+": bought, where every other leg is sold.
          .map(
            (l: { strike?: number; right?: string; action?: string }) =>
              `${l.action === "buy" ? "+" : ""}${l.strike} ${l.right === "call" ? "CE" : "PE"}`,
          )
          .join(" / "),
    },
    { key: "lots", label: "Lots", align: "right", render: (t) => String(t.lots) },
    { key: "premium", label: "Premium", align: "right", render: (t) => inr(num(t.premium_inr)) },
    { key: "why", label: "Exit", render: (t) => `${exitLabel(t.exit_reason)} ${time(t.exited_at).slice(11)}` },
  ],
  cas: [
    { key: "day", label: "Expiry", render: (t) => `${t.day} ${t.index === "BSESEN" ? "SENSEX" : t.index}` },
    { key: "in", label: "Entry", render: (t) => time(t.entered_at).slice(11) },
    { key: "what", label: "Structure", render: (t) => String(t.structure).replace(/_/g, " ") },
    { key: "legs", label: "Legs", render: (t) => String(t.legs ?? "") },
    { key: "lots", label: "Lots", align: "right", render: (t) => String(t.lots) },
    { key: "premium", label: "Net premium", align: "right", render: (t) => price(t.net_entry_per_unit) },
    { key: "why", label: "Exit", render: (t) => `${exitLabel(t.exit_reason)} ${time(t.exited_at).slice(11)}` },
  ],
  // One row per campaign: it can span several cycles, each with its tranches and rolls (#67).
  condor: [
    { key: "in", label: "Started", render: (t) => time(t.entered_at) },
    { key: "out", label: "Ended", render: (t) => (t.exited_at ? time(t.exited_at) : "open") },
    { key: "cycles", label: "Cycles", render: (t) => String(t.cycles ?? "") },
    { key: "tranches", label: "Tranches", align: "right", render: (t) => String(t.tranches ?? 0) },
    { key: "rolls", label: "Rolls", align: "right", render: (t) => String(t.rolls ?? 0) },
    { key: "worst", label: "Worst at a check", align: "right", render: (t) => inr(num(t.worst_pnl_at_check)) },
    { key: "why", label: "Ended by", render: (t) => exitLabel(t.exit_reason) },
  ],
};

/** Every signal setting the bot could trade, and its premium gate off and at three thresholds,
 *  side by side (docs/signals-streamline-plan.md 8, docs/premium-gate-plan.md 4). The trades
 *  below are the saved setting's; every setting's files are in the run's zip. */
function Comparison({ rows }: { rows: ComparisonRow[] }) {
  const best = rows.reduce<ComparisonRow | null>(
    (b, r) => (b === null || (r.net_pnl ?? -Infinity) > (b.net_pnl ?? -Infinity) ? r : b),
    null,
  );
  return (
    <div>
      <p className="mb-1.5 text-micro font-bold uppercase tracking-wide text-faint">
        Every setting compared, replayed ({rows.length})
      </p>
      <div className="app-table-wrap max-h-[22rem]">
        <table className="min-w-full text-left text-table">
          <thead className="app-table-head sticky top-0">
            <tr>
              <th className="px-2.5 py-2 font-semibold whitespace-nowrap">Setting</th>
              <th className="px-2.5 py-2 text-right font-semibold whitespace-nowrap">Trades</th>
              <th className="px-2.5 py-2 text-right font-semibold whitespace-nowrap">Win rate</th>
              <th className="px-2.5 py-2 text-right font-semibold whitespace-nowrap">Net P&amp;L</th>
              <th className="px-2.5 py-2 text-right font-semibold whitespace-nowrap">Max drawdown</th>
              <th className="px-2.5 py-2 text-right font-semibold whitespace-nowrap">Worst day</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} className={`app-table-row ${r.is_saved ? "font-semibold" : ""}`}>
                <td className="px-2.5 py-1.5 whitespace-nowrap text-foreground">
                  {r.label}
                  {r.is_saved ? <span className="ml-2 text-hint text-accent">your setting</span> : null}
                  {best && r.id === best.id && rows.length > 1 ? (
                    <span className="ml-2 text-hint text-up">best</span>
                  ) : null}
                </td>
                <td className="px-2.5 py-1.5 text-right font-mono tabular-nums">{r.trades}</td>
                <td className="px-2.5 py-1.5 text-right font-mono tabular-nums">
                  {r.win_rate_pct == null ? "—" : `${r.win_rate_pct}%`}
                </td>
                <td
                  className={`px-2.5 py-1.5 text-right font-mono tabular-nums ${
                    (r.net_pnl ?? 0) >= 0 ? "text-up" : "text-down"
                  }`}
                >
                  {inr(r.net_pnl)}
                </td>
                <td className="px-2.5 py-1.5 text-right font-mono tabular-nums text-muted">{inr(r.max_drawdown)}</td>
                <td className="px-2.5 py-1.5 text-right font-mono tabular-nums text-muted">
                  {r.worst_day == null ? "—" : inr(r.worst_day)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

const TH = "px-2.5 py-2 font-semibold whitespace-nowrap";
const TH_R = `${TH} text-right`;
const TD_NUM = "px-2.5 py-1.5 text-right font-mono tabular-nums";

/** Every settings combination a condor backtest replayed, side by side (#71): the settings each
 *  one sets, re-centre off and on included (#80), every other as saved. The campaigns below are the saved combination's; every
 *  combination's are in the run's zip. */
function CondorComparison({ rows }: { rows: CondorComparisonRow[] }) {
  const best = rows.reduce<CondorComparisonRow | null>(
    (b, r) => (b === null || (r.net_pnl ?? -Infinity) > (b.net_pnl ?? -Infinity) ? r : b),
    null,
  );
  return (
    <div>
      <p className="mb-1.5 text-micro font-bold uppercase tracking-wide text-faint">
        Every settings combination, replayed ({rows.length})
      </p>
      <div className="app-table-wrap max-h-[22rem]">
        <table className="min-w-full text-left text-table">
          <thead className="app-table-head sticky top-0">
            <tr>
              <th className={TH_R}>Net-Δ band</th>
              <th className={TH_R}>Min roll credit</th>
              <th className={TH_R}>Max loss</th>
              <th className={TH_R}>No-roll window</th>
              <th className={TH}>Exit action</th>
              <th className={TH_R}>Premium gate</th>
              <th className={TH_R}>Re-centre</th>
              <th className={TH_R}>Campaigns</th>
              <th className={TH_R}>Rolls</th>
              <th className={TH_R}>Win rate</th>
              <th className={TH_R}>Net P&amp;L</th>
              <th className={TH_R}>Max drawdown</th>
              <th className={TH_R}>Worst at a check</th>
              <th className={TH} />
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} className={`app-table-row ${r.is_saved ? "font-semibold" : ""}`}>
                <td className={TD_NUM}>{r.varied.net_delta_band_per_lot}</td>
                <td className={TD_NUM}>{r.varied.min_roll_credit_points}</td>
                <td className={TD_NUM}>{r.varied.max_loss}</td>
                <td className={TD_NUM}>{r.varied.no_roll_within_days_of_exit}d</td>
                <td className="px-2.5 py-1.5 whitespace-nowrap text-foreground">
                  {r.varied.exit_action === "close" ? "Close" : "Time roll"}
                </td>
                <td className={TD_NUM}>{r.varied.premium_gate ?? "—"}</td>
                <td className={`${TD_NUM} whitespace-nowrap`}>{r.varied.recentre ?? "—"}</td>
                <td className={TD_NUM}>{r.trades}</td>
                <td className={TD_NUM}>{r.rolls}</td>
                <td className={TD_NUM}>{r.win_rate_pct == null ? "—" : `${r.win_rate_pct}%`}</td>
                <td className={`${TD_NUM} ${(r.net_pnl ?? 0) >= 0 ? "text-up" : "text-down"}`}>{inr(r.net_pnl)}</td>
                <td className={`${TD_NUM} text-muted`}>{inr(r.max_drawdown)}</td>
                <td className={`${TD_NUM} text-muted`}>{r.worst_at_check == null ? "—" : inr(r.worst_at_check)}</td>
                <td className="px-2.5 py-1.5 whitespace-nowrap">
                  {r.is_saved ? <span className="text-hint text-accent">your settings</span> : null}
                  {best && r.id === best.id && rows.length > 1 ? (
                    <span className="ml-2 text-hint text-up">best</span>
                  ) : null}
                  {!r.complete ? (
                    <span className="ml-2 text-hint text-amber-on-tint" title={r.stopped_at ? `Stopped at the ${r.stopped_at} check` : undefined}>
                      partial
                    </span>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Tile({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="app-card-muted p-3">
      <div className="text-micro font-bold uppercase tracking-wide text-faint">{label}</div>
      <div className={`mt-1 font-mono text-lg tabular-nums ${tone ?? "text-foreground"}`}>{value}</div>
    </div>
  );
}

/** A backtest's constituent trades, opened from its Activity row (#35).
 *
 *  The replay is stored with the same id as the Activity row, so the row needs nothing but its
 *  own id to open this. Fetched only when expanded. */
export function BacktestRunTrades({ runId, bot }: { runId: string; bot: BacktestBot }) {
  const q = useQuery({ queryKey: ["bots", "backtest", "run", runId], queryFn: () => fetchBacktestRun(runId) });
  const csv = useMutation({ mutationFn: () => downloadBacktestCsv(runId) });
  const trades = useMemo(() => q.data?.trades ?? [], [q.data]);
  const totals = useMemo(() => tradeTotals(trades), [trades]);
  const curve = useMemo(() => equityCurve(trades), [trades]);

  if (q.isLoading) return <p className="app-text-muted p-3 text-xs">Loading the trades…</p>;
  if (q.error || !q.data) {
    return (
      <p className="p-3 text-xs text-down">
        {q.error instanceof Error ? q.error.message : "Could not load this backtest."}
      </p>
    );
  }
  const summary = (q.data.summary ?? {}) as Record<string, unknown>;
  const comparison = comparisonRows(q.data.summary);
  const waiting = num(summary.days_awaiting_data) ?? 0;
  const notes = Array.isArray(summary.notes) ? (summary.notes as string[]) : [];
  const sizing = q.data.params.sizing;
  const footnote = [
    `Real ICICI prices, today's lot sizes${sizing ? ` · ${sizing}` : ""}.`,
    waiting ? `${waiting} day(s) had no price data and were skipped.` : "",
    ...notes,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div className="space-y-3 p-3">
      <div className="flex justify-end">
        <button
          type="button"
          className="app-btn-outline rounded-[9px] px-3 py-1.5 text-xs"
          disabled={csv.isPending || !trades.length}
          onClick={() => csv.mutate()}
        >
          <AsyncLabelSpan busy={csv.isPending} idleLabel="Download trades CSV" busyLabel="Preparing…" />
        </button>
      </div>
      {comparison.length > 1 && bot === "condor" ? (
        <CondorComparison rows={condorComparisonRows(q.data.summary)} />
      ) : comparison.length > 1 ? (
        <Comparison rows={comparison} />
      ) : null}
      {comparison.length > 1 ? (
        <p className="text-micro font-bold uppercase tracking-wide text-faint">
          {bot === "condor" ? "Your settings" : "Your setting"}:{" "}
          {String(summary.setting_label ?? summary.signal_setting ?? "")}
        </p>
      ) : null}
      <RunResults trades={trades} totals={totals} curve={curve} columns={COLUMNS[bot]} footnote={footnote} />
      {!trades.length ? <p className="text-xs text-muted">No trades in this period.</p> : null}
    </div>
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
