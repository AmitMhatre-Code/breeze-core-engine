"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { AsyncLabelSpan } from "@/components/ui/AsyncLabelSpan";
import { Modal } from "@/components/ui/Modal";
import { BacktestEquityChart } from "@/components/bots/BacktestEquityChart";
import {
  MARK_LABELS,
  comparisonRows,
  condorComparisonRows,
  downloadBacktestCsv,
  equityCurve,
  exitLabel,
  fetchBacktestRun,
  inr,
  markedGroups,
  maxDrawdown,
  num,
  rowMarks,
  tradeTotals,
  type BacktestBot,
  type BacktestTrade,
  type ComparedRow,
  type ComparisonRow,
  type CondorComparisonRow,
  type RowMark,
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

/** What a compared row needs to show its marks and open its trades. */
type RowActions = {
  marks: Map<string, RowMark[]>;
  /** False for every row but yours once the run's results zip is gone. */
  canOpen: (row: ComparedRow) => boolean;
  onOpen: (row: ComparedRow & { label: string }) => void;
};

/** A marked row's wash. The worst is marked as plainly as the best (the user's call). */
const MARK_ROW: Record<RowMark, string> = {
  saved: "bg-accent-tint",
  best: "bg-up-tint",
  worst: "bg-down-tint",
};
const MARK_TEXT: Record<RowMark, string> = {
  saved: "text-accent-on-tint",
  best: "text-up-on-tint",
  worst: "text-down-on-tint",
};
/** The same marks as bare text, off the wash: the groups on top and the dialog's title. */
const MARK_BARE: Record<RowMark, string> = { saved: "text-accent", best: "text-up", worst: "text-down" };
const GONE_HINT = "This backtest's results file is no longer kept, so only your settings' trades can be shown.";

/** Your settings' wash wins when a row is both, so the best/worst label carries the second mark. */
function rowClass(marks: RowMark[] | undefined): string {
  return `app-table-row ${marks ? `${MARK_ROW[marks[0]]} font-semibold` : ""}`;
}

/** P&L tone; a washed row needs the `-on-tint` colours to keep AA (globals.css). */
function pnlTone(value: number | null | undefined, washed: boolean): string {
  const up = (value ?? 0) >= 0;
  return washed ? (up ? "text-up-on-tint" : "text-down-on-tint") : up ? "text-up" : "text-down";
}

function RowBadges({ marks }: { marks: RowMark[] | undefined }) {
  return (
    <>
      {(marks ?? []).map((m) => (
        <span key={m} className={`ml-2 text-hint ${MARK_TEXT[m]}`}>
          {MARK_LABELS[m].toLowerCase()}
        </span>
      ))}
    </>
  );
}

/** A table: this row's trades, in a dialog. */
function TradesIcon() {
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
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="M3 9h18M3 14h18M9 9v11" />
    </svg>
  );
}

function OpenCell({ row, label, actions }: { row: ComparedRow; label: string; actions: RowActions }) {
  const open = actions.canOpen(row);
  return (
    <td className="px-1.5 py-1">
      <button
        type="button"
        className="grid size-7 place-items-center rounded-md text-muted hover:bg-panel hover:text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/45 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent"
        aria-label={`View and download the trades of ${label}`}
        title={open ? "View and download these trades" : GONE_HINT}
        disabled={!open}
        onClick={() => actions.onOpen({ ...row, label })}
      >
        <TradesIcon />
      </button>
    </td>
  );
}

/** Every signal setting the bot could trade, and its premium gate off and at three thresholds,
 *  side by side (docs/signals-streamline-plan.md 8, docs/premium-gate-plan.md 4). Each row's
 *  trades open from its icon. */
function Comparison({ rows, actions }: { rows: ComparisonRow[]; actions: RowActions }) {
  return (
    <div>
      <p className="mb-1.5 text-micro font-bold uppercase tracking-wide text-faint">
        Every setting compared, replayed ({rows.length})
      </p>
      <div className="app-table-wrap max-h-[22rem]">
        <table className="min-w-full text-left text-table">
          <thead className="app-table-head sticky top-0">
            <tr>
              <th className="w-0 px-1.5 py-2">
                <span className="sr-only">Trades</span>
              </th>
              <th className="px-2.5 py-2 font-semibold whitespace-nowrap">Setting</th>
              <th className="px-2.5 py-2 text-right font-semibold whitespace-nowrap">Trades</th>
              <th className="px-2.5 py-2 text-right font-semibold whitespace-nowrap">Win rate</th>
              <th className="px-2.5 py-2 text-right font-semibold whitespace-nowrap">Net P&amp;L</th>
              <th className="px-2.5 py-2 text-right font-semibold whitespace-nowrap">Max drawdown</th>
              <th className="px-2.5 py-2 text-right font-semibold whitespace-nowrap">Worst day</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const marks = actions.marks.get(r.id);
              return (
                <tr key={r.id} className={rowClass(marks)}>
                  <OpenCell row={r} label={r.label} actions={actions} />
                  <td className="px-2.5 py-1.5 whitespace-nowrap text-foreground">
                    {r.label}
                    <RowBadges marks={marks} />
                  </td>
                  <td className="px-2.5 py-1.5 text-right font-mono tabular-nums">{r.trades}</td>
                  <td className="px-2.5 py-1.5 text-right font-mono tabular-nums">
                    {r.win_rate_pct == null ? "—" : `${r.win_rate_pct}%`}
                  </td>
                  <td className={`px-2.5 py-1.5 text-right font-mono tabular-nums ${pnlTone(r.net_pnl, !!marks)}`}>
                    {inr(r.net_pnl)}
                  </td>
                  <td className="px-2.5 py-1.5 text-right font-mono tabular-nums text-muted">{inr(r.max_drawdown)}</td>
                  <td className="px-2.5 py-1.5 text-right font-mono tabular-nums text-muted">
                    {r.worst_day == null ? "—" : inr(r.worst_day)}
                  </td>
                </tr>
              );
            })}
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
 *  one sets, re-centre off and on included (#80), every other as saved. Each row's campaigns
 *  open from its icon. */
function CondorComparison({ rows, actions }: { rows: CondorComparisonRow[]; actions: RowActions }) {
  return (
    <div>
      <p className="mb-1.5 text-micro font-bold uppercase tracking-wide text-faint">
        Every settings combination, replayed ({rows.length})
      </p>
      <div className="app-table-wrap max-h-[22rem]">
        <table className="min-w-full text-left text-table">
          <thead className="app-table-head sticky top-0">
            <tr>
              <th className="w-0 px-1.5 py-2">
                <span className="sr-only">Campaigns</span>
              </th>
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
            {rows.map((r) => {
              const marks = actions.marks.get(r.id);
              return (
                <tr key={r.id} className={rowClass(marks)}>
                  <OpenCell row={r} label={r.label} actions={actions} />
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
                  <td className={`${TD_NUM} ${pnlTone(r.net_pnl, !!marks)}`}>{inr(r.net_pnl)}</td>
                  <td className={`${TD_NUM} text-muted`}>{inr(r.max_drawdown)}</td>
                  <td className={`${TD_NUM} text-muted`}>{r.worst_at_check == null ? "—" : inr(r.worst_at_check)}</td>
                  <td className="px-2.5 py-1.5 whitespace-nowrap">
                    <RowBadges marks={marks} />
                    {!r.complete ? (
                      <span className="ml-2 text-hint text-amber-on-tint" title={r.stopped_at ? `Stopped at the ${r.stopped_at} check` : undefined}>
                        partial
                      </span>
                    ) : null}
                  </td>
                </tr>
              );
            })}
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

/** The six headline figures. `drawdown` is ≤ 0; `gross` is null on condor runs from before the
 *  per-row trades view, whose rows did not record it. */
type Figures = {
  net: number | null;
  trades: number | null;
  winRate: number | null;
  charges: number | null;
  gross: number | null;
  drawdown: number | null;
};

function figuresFromRow(row: ComparedRow): Figures {
  return {
    net: row.net_pnl,
    trades: row.trades,
    winRate: row.win_rate_pct,
    charges: row.friction,
    gross: row.gross_pnl ?? null,
    drawdown: row.max_drawdown,
  };
}

function figuresFromTrades(trades: ReadonlyArray<BacktestTrade>): Figures {
  const totals = tradeTotals(trades);
  const drawdown = maxDrawdown(equityCurve(trades));
  return {
    net: totals.net,
    trades: totals.trades,
    winRate: totals.winRate,
    charges: totals.friction,
    gross: totals.gross,
    drawdown: drawdown ? -drawdown : 0,
  };
}

function FigureTiles({ figures: f }: { figures: Figures }) {
  const share = f.charges != null && f.gross ? ((100 * f.charges) / Math.abs(f.gross)).toFixed(1) : null;
  return (
    <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-6">
      <Tile label="Net P&L" value={inr(f.net)} tone={(f.net ?? 0) >= 0 ? "text-up" : "text-down"} />
      <Tile label="Trades" value={f.trades == null ? "—" : String(f.trades)} />
      <Tile label="Win rate" value={f.winRate == null ? "—" : `${f.winRate}%`} />
      <Tile label="Charges" value={f.charges == null ? "—" : inr(f.charges)} />
      <Tile label="Charges / gross" value={share == null ? "—" : `${share}%`} />
      <Tile label="Max drawdown" value={inr(f.drawdown ?? 0)} tone={f.drawdown ? "text-down" : undefined} />
    </div>
  );
}

function GroupHeading({ marks, label }: { marks: RowMark[]; label: string }) {
  return (
    <p className="text-micro font-bold uppercase tracking-wide text-faint">
      {marks.map((m, i) => (
        <span key={m} className={MARK_BARE[m]}>
          {i > 0 ? " · " : ""}
          {MARK_LABELS[m]}
        </span>
      ))}
      <span className="ml-2 font-normal normal-case tracking-normal text-muted">{label}</span>
    </p>
  );
}

/** A backtest's results, opened from its Activity row (#35).
 *
 *  The replay is stored with the same id as the Activity row, so the row needs nothing but its
 *  own id to open this. Fetched only when expanded. A run that compared settings shows your
 *  settings, the best and the worst on top and the comparison below, each row's trades a click
 *  away; one that compared nothing shows its trades as it always has. */
export function BacktestRunTrades({ runId, bot }: { runId: string; bot: BacktestBot }) {
  const q = useQuery({ queryKey: ["bots", "backtest", "run", runId], queryFn: () => fetchBacktestRun(runId) });
  const [opened, setOpened] = useState<(ComparedRow & { label: string }) | null>(null);
  const comparison = useMemo(() => comparisonRows(q.data?.summary), [q.data]);
  const marks = useMemo(() => rowMarks(comparison), [comparison]);

  if (q.isLoading) return <p className="app-text-muted p-3 text-xs">Loading the trades…</p>;
  if (q.error || !q.data) {
    return (
      <p className="p-3 text-xs text-down">
        {q.error instanceof Error ? q.error.message : "Could not load this backtest."}
      </p>
    );
  }
  const summary = (q.data.summary ?? {}) as Record<string, unknown>;
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

  if (comparison.length <= 1) {
    return (
      <div className="space-y-3 p-3">
        <SingleRun runId={runId} trades={q.data.trades ?? []} columns={COLUMNS[bot]} footnote={footnote} />
      </div>
    );
  }

  const kept = q.data.results_file_kept !== false;
  const actions: RowActions = {
    marks,
    canOpen: (row) => kept || row.is_saved,
    onOpen: setOpened,
  };
  return (
    <div className="space-y-4 p-3">
      {markedGroups(comparison).map(({ row, marks: m }) => (
        <div key={row.id} className="space-y-1.5">
          <GroupHeading marks={m} label={row.label} />
          <FigureTiles figures={figuresFromRow(row)} />
        </div>
      ))}
      <p className="text-xs text-faint">{footnote}</p>
      {bot === "condor" ? (
        <CondorComparison rows={condorComparisonRows(q.data.summary)} actions={actions} />
      ) : (
        <Comparison rows={comparison} actions={actions} />
      )}
      {opened ? (
        <CombinationDialog
          runId={runId}
          row={opened}
          marks={marks.get(opened.id) ?? []}
          columns={COLUMNS[bot]}
          onClose={() => setOpened(null)}
        />
      ) : null}
    </div>
  );
}

/** A run that compared nothing: its trades, as every backtest showed them before (#35). */
function SingleRun({
  runId,
  trades,
  columns,
  footnote,
}: {
  runId: string;
  trades: BacktestTrade[];
  columns: Column[];
  footnote: string;
}) {
  const csv = useMutation({ mutationFn: () => downloadBacktestCsv(runId) });
  const curve = useMemo(() => equityCurve(trades), [trades]);
  return (
    <>
      <div className="flex justify-end">
        <DownloadButton busy={csv.isPending} disabled={!trades.length} onClick={() => csv.mutate()} />
      </div>
      <FigureTiles figures={figuresFromTrades(trades)} />
      <BacktestEquityChart points={curve} />
      <p className="text-xs text-faint">{footnote}</p>
      <TradesTable trades={trades} columns={columns} />
      {!trades.length ? <p className="text-xs text-muted">No trades in this period.</p> : null}
    </>
  );
}

function DownloadButton({ busy, disabled, onClick }: { busy: boolean; disabled: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      className="app-btn-outline rounded-[9px] px-3 py-1.5 text-xs"
      disabled={busy || disabled}
      onClick={onClick}
    >
      <AsyncLabelSpan busy={busy} idleLabel="Download trades CSV" busyLabel="Preparing…" />
    </button>
  );
}

/** One compared row's trades: its figures as the row has them, its equity curve and its trades,
 *  and the same trades as a CSV. Every row but yours is read from the run's results zip. */
function CombinationDialog({
  runId,
  row,
  marks,
  columns,
  onClose,
}: {
  runId: string;
  row: ComparedRow & { label: string };
  marks: RowMark[];
  columns: Column[];
  onClose: () => void;
}) {
  const q = useQuery({
    queryKey: ["bots", "backtest", "run", runId, "combo", row.id],
    queryFn: () => fetchBacktestRun(runId, row.id),
  });
  const csv = useMutation({ mutationFn: () => downloadBacktestCsv(runId, row.id) });
  const trades = useMemo(() => q.data?.trades ?? [], [q.data]);
  const curve = useMemo(() => equityCurve(trades), [trades]);
  // A condor row from before gross was recorded: its campaigns carry it, once they are loaded.
  const figures = useMemo(() => {
    const f = figuresFromRow(row);
    return f.gross == null && trades.length ? { ...f, gross: tradeTotals(trades).gross } : f;
  }, [row, trades]);

  return (
    <Modal
      open
      onClose={onClose}
      titleId="backtest-combination-title"
      panelClassName="flex max-h-[90dvh] w-full !max-w-[min(96vw,64rem)] flex-col overflow-hidden rounded-xl border border-border bg-panel shadow-pop"
    >
      <div className="flex items-start justify-between gap-3 border-b border-border-soft px-5 py-4">
        <div className="min-w-0">
          <h2 id="backtest-combination-title" className="app-text-heading">
            Trades
            {marks.map((m) => (
              <span key={m} className={`ml-2 text-xs font-semibold ${MARK_BARE[m]}`}>
                {MARK_LABELS[m]}
              </span>
            ))}
          </h2>
          <p className="mt-0.5 text-xs text-muted">{row.label}</p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <DownloadButton busy={csv.isPending} disabled={!trades.length} onClick={() => csv.mutate()} />
          <button type="button" className="app-btn-outline rounded-[9px] px-3 py-1.5 text-xs" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
      <div className="space-y-3 overflow-y-auto px-5 py-4">
        <FigureTiles figures={figures} />
        {q.isLoading ? (
          <p className="app-text-muted text-xs">Loading the trades…</p>
        ) : q.error ? (
          <p className="text-xs text-down">
            {q.error instanceof Error ? q.error.message : "Could not load these trades."}
          </p>
        ) : (
          <>
            <BacktestEquityChart points={curve} />
            <TradesTable trades={trades} columns={columns} />
            {!trades.length ? <p className="text-xs text-muted">No trades in this period.</p> : null}
          </>
        )}
        {csv.error ? (
          <p className="text-xs text-down">{csv.error instanceof Error ? csv.error.message : "Download failed."}</p>
        ) : null}
      </div>
    </Modal>
  );
}

function TradesTable({ trades, columns }: { trades: BacktestTrade[]; columns: Column[] }) {
  if (!trades.length) return null;
  return (
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
  );
}
