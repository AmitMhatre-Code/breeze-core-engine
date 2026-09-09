"use client";

import { useState } from "react";
import { formatIndianMoneyCompact, moneyToneClass } from "@/lib/format-money-in";
import { describeFeed, feedToneClass } from "@/lib/scalper-audit";
import {
  isScalper,
  useBotCycles,
  useBotRuns,
  type BotCycle,
  type BotRun,
  type BotRunStatus,
  BOT_META,
} from "@/lib/use-bots";

/** A run's outcome, not its severity. `skipped` is deliberately neutral rather than a
 *  warning colour: a bot correctly declining to trade is a normal day, and colouring it
 *  as a problem trains the user to ignore the log. */
const STATUS_TONE: Record<BotRunStatus, string> = {
  running: "bg-sky-500/10 text-sky-700 dark:text-sky-300",
  completed: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
  proposed: "bg-violet-500/10 text-violet-700 dark:text-violet-300",
  skipped: "bg-zinc-500/10 text-zinc-600 dark:text-zinc-400",
  failed: "bg-rose-500/10 text-rose-700 dark:text-rose-300",
};

function StatusBadge({ status }: { status: BotRunStatus }) {
  return (
    <span
      className={`inline-block rounded px-1.5 py-0.5 text-[11px] font-medium uppercase tracking-wide ${STATUS_TONE[status]}`}
    >
      {status}
    </span>
  );
}

function cycleTime(iso: string | null): string {
  return iso ? String(iso).slice(11, 19) : "—";
}

function legLabel(cycle: BotCycle): string {
  const leg = (cycle.legs ?? [])[0] as
    | { right?: string; strike_price?: number }
    | undefined;
  if (cycle.structure === "iron_fly") {
    const centre = (cycle.detail as { atm_strike?: number } | null)?.atm_strike;
    return centre ? `Fly ${Math.round(centre)}` : "Fly";
  }
  if (!leg) return cycle.structure;
  const right = String(leg.right ?? "").toUpperCase() === "PUT" ? "PE" : "CE";
  return `${right} ${Math.round(Number(leg.strike_price ?? 0))}`;
}

/** A session's round trips.
 *
 *  Nested under the run rather than listed alongside it: a scalper produces dozens a day,
 *  and flattening them into the run log would bury the record of *why nothing happened*
 *  that the log exists to preserve. Fetched only when expanded, so a collapsed month costs
 *  nothing.
 */
function CycleTable({ runId }: { runId: string }) {
  const { data, isLoading, isError, error } = useBotCycles(runId);

  if (isLoading) return <p className="app-text-muted px-3 py-2 text-xs">Loading cycles…</p>;
  if (isError) {
    return (
      <p className="px-3 py-2 text-xs text-down">
        Could not load cycles: {(error as Error)?.message ?? "unknown error"}
      </p>
    );
  }
  if (!data || data.length === 0) {
    return <p className="app-text-muted px-3 py-2 text-xs">No cycles in this session.</p>;
  }

  const ordered = [...data].sort((a, b) => a.cycle_no - b.cycle_no);
  return (
    <div className="app-table-wrap m-2">
      <table className="w-full text-left">
        <thead className="app-table-head">
          <tr>
            <th className="px-2 py-1.5 text-[11px] font-medium">#</th>
            <th className="px-2 py-1.5 text-[11px] font-medium">In</th>
            <th className="px-2 py-1.5 text-[11px] font-medium">Out</th>
            <th className="px-2 py-1.5 text-[11px] font-medium">Contract</th>
            <th className="px-2 py-1.5 text-[11px] font-medium">Lots</th>
            <th className="px-2 py-1.5 text-right text-[11px] font-medium">Gross</th>
            <th className="px-2 py-1.5 text-right text-[11px] font-medium">Friction</th>
            <th className="px-2 py-1.5 text-right text-[11px] font-medium">Net</th>
            <th className="px-2 py-1.5 text-[11px] font-medium">Exit</th>
          </tr>
        </thead>
        <tbody>
          {ordered.map((cycle) => (
            <tr key={cycle.id} className="app-table-row">
              <td className="px-2 py-1.5 tabular-nums text-[11px]">{cycle.cycle_no}</td>
              <td className="px-2 py-1.5 tabular-nums text-[11px]">{cycleTime(cycle.opened_at)}</td>
              <td className="px-2 py-1.5 tabular-nums text-[11px]">{cycleTime(cycle.closed_at)}</td>
              <td className="px-2 py-1.5 text-[11px]">{legLabel(cycle)}</td>
              <td className="px-2 py-1.5 tabular-nums text-[11px]">{cycle.lots ?? "—"}</td>
              <td className="px-2 py-1.5 text-right tabular-nums text-[11px]">
                {cycle.gross_pnl === null ? "—" : formatIndianMoneyCompact(cycle.gross_pnl)}
              </td>
              {/* Friction gets its own column rather than being netted away silently: it is
                  the constraint that decides whether this strategy works at all. */}
              <td className="px-2 py-1.5 text-right tabular-nums text-[11px] text-faint">
                {cycle.friction === null ? "—" : formatIndianMoneyCompact(cycle.friction)}
              </td>
              <td
                className={`px-2 py-1.5 text-right tabular-nums text-[11px] ${
                  cycle.net_pnl === null ? "" : moneyToneClass(cycle.net_pnl)
                }`}
              >
                {cycle.net_pnl === null ? "—" : formatIndianMoneyCompact(cycle.net_pnl)}
              </td>
              <td className="px-2 py-1.5 text-[11px]">
                <code className="app-text-muted text-[11px]">
                  {cycle.exit_reason_code ?? (cycle.closed_at ? "—" : "open")}
                </code>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function RunRow({ run }: { run: BotRun }) {
  const [expanded, setExpanded] = useState(false);
  // Only a scalper session has cycles beneath it; the writers resolve in one pass and have
  // nothing to expand into.
  const expandable = isScalper(run.bot_type) && run.trigger === "session";
  const feed = describeFeed(run.detail);

  return (
    <>
      <tr className="app-table-row align-top">
        <td className="whitespace-nowrap px-3 py-2 tabular-nums text-xs">
          {run.started_at ?? "—"}
        </td>
        <td className="px-3 py-2 text-xs">
          {expandable ? (
            <button
              type="button"
              aria-expanded={expanded}
              onClick={() => setExpanded((v) => !v)}
              className="inline-flex items-center gap-1.5 rounded text-left transition hover:text-accent focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/45"
            >
              <span aria-hidden className="font-mono text-[10px]">
                {expanded ? "▾" : "▸"}
              </span>
              {BOT_META[run.bot_type]?.title ?? run.bot_type}
            </button>
          ) : (
            BOT_META[run.bot_type]?.title ?? run.bot_type
          )}
        </td>
        <td className="px-3 py-2 text-xs capitalize">{run.trigger.replace("_", " ")}</td>
        <td className="px-3 py-2">
          <StatusBadge status={run.status} />
        </td>
        <td className="px-3 py-2 text-xs">
          {/* Both halves matter: the text is for the user, the code is what support and
              tests can rely on when the text is later reworded. */}
          <div>{run.reason_text ?? "—"}</div>
          {run.reason_code && (
            <code className="app-text-muted text-[11px]">{run.reason_code}</code>
          )}
          {feed && (
            /* The scalpers' reason codes are ambiguous on their own -- `not_warm` covers
               both a feed that is filling and one that was never subscribed. This is the
               half that tells them apart, kept in the log so it is still there tomorrow. */
            <div className={`mt-0.5 font-mono text-[11px] ${feedToneClass(feed.tone)}`}>
              {feed.text}
            </div>
          )}
          {run.audit_log && (
            /* The row shows one verdict; this is every verdict of that day. Rendered as a
               plain download rather than an expandable panel because the file is a tick-level
               record — thousands of lines — meant to be read outside the browser. */
            <a
              href={`/api/settings/bot-audit-logs/${encodeURIComponent(run.audit_log)}/download`}
              className="mt-0.5 inline-block text-[11px] text-accent underline underline-offset-2 hover:no-underline focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/45"
            >
              Download full-day audit trail
            </a>
          )}
        </td>
      </tr>
      {expandable && expanded && (
        <tr>
          <td colSpan={5} className="bg-panel2 p-0">
            <CycleTable runId={run.id} />
          </td>
        </tr>
      )}
    </>
  );
}

export function BotRunLog() {
  const { data, isLoading, isError, error } = useBotRuns();

  return (
    <section className="app-card p-4">
      <h2 className="app-text-heading">Activity</h2>
      <p className="app-text-muted mt-1 text-xs">
        Every scan, order, and skip across all bots — including the days nothing happened,
        and why.
      </p>

      {isLoading && <p className="app-text-muted mt-4 text-sm">Loading activity…</p>}
      {isError && (
        <p className="mt-4 text-sm text-rose-600 dark:text-rose-400">
          Could not load activity: {(error as Error)?.message ?? "unknown error"}
        </p>
      )}

      {data && data.length === 0 && (
        <p className="app-text-muted mt-4 text-sm">
          No bot activity yet. Runs appear here once a bot is enabled.
        </p>
      )}

      {data && data.length > 0 && (
        <div className="app-table-wrap mt-4">
          <table className="w-full text-left">
            <thead className="app-table-head">
              <tr>
                <th className="px-3 py-2 text-xs font-medium">Started</th>
                <th className="px-3 py-2 text-xs font-medium">Bot</th>
                <th className="px-3 py-2 text-xs font-medium">Trigger</th>
                <th className="px-3 py-2 text-xs font-medium">Outcome</th>
                <th className="px-3 py-2 text-xs font-medium">Reason</th>
              </tr>
            </thead>
            <tbody>
              {data.map((run) => (
                <RunRow key={run.id} run={run} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
