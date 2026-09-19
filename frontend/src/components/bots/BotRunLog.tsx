"use client";

import { useState, type ReactNode } from "react";
import { formatIndianMoneyCompact, moneyToneClass } from "@/lib/format-money-in";
import { BacktestRunTrades } from "@/components/bots/BacktestTrades";
import { BACKTEST_SLUG, backtestAuditHref } from "@/lib/bots-backtest";
import { describeFeed, feedToneClass } from "@/lib/scalper-audit";
import {
  bundleKey,
  customRangeError,
  istToday,
  presetRange,
  type DateRange,
  type RunLogPreset,
} from "@/lib/bot-run-bundles";
import {
  isScalper,
  useBotCycles,
  useBotRunBundles,
  useBundleRuns,
  type BotCycle,
  type BotRun,
  type BotRunBundle,
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

function AuditLink({ href, backtest }: { href: string; backtest: boolean }) {
  return (
    /* One verdict per row; this is every verdict of that day -- or, for a backtest, the
       whole replay. Rendered as a plain download rather than an expandable panel because the
       file is meant to be read outside the browser. */
    <a
      href={
        backtest
          ? backtestAuditHref(href)
          : `/api/settings/bot-audit-logs/${encodeURIComponent(href)}/download`
      }
      className="mt-0.5 block w-fit text-[11px] text-accent underline underline-offset-2 hover:no-underline focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/45"
    >
      {backtest
        ? href.endsWith(".zip")
          ? "Download backtest results (.zip)"
          : "Download backtest audit trail"
        : "Download full-day audit trail"}
    </a>
  );
}

function ExpandToggle({
  expanded,
  onToggle,
  children,
}: {
  expanded: boolean;
  onToggle: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      aria-expanded={expanded}
      onClick={onToggle}
      className="inline-flex items-center gap-1.5 rounded text-left transition hover:text-accent focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/45"
    >
      <span aria-hidden className="font-mono text-[10px]">
        {expanded ? "▾" : "▸"}
      </span>
      {children}
    </button>
  );
}

/** One run. `nested` is a member row inside an expanded bundle: the bundle row already states
 *  the bot, trigger, outcome and day's audit trail, so the member shows only its time and why. */
function RunRow({ run, nested = false }: { run: BotRun; nested?: boolean }) {
  const [expanded, setExpanded] = useState(false);
  // A scalper session has cycles beneath it, and a backtest has the trades it replayed (#35);
  // the writers resolve in one pass and have nothing to expand into.
  const isBacktest = run.trigger === "backtest";
  const backtestBot = isBacktest ? BACKTEST_SLUG[run.bot_type] : undefined;
  const expandable =
    (isScalper(run.bot_type) && run.trigger === "session") ||
    (isBacktest && run.status === "completed" && Boolean(backtestBot));
  const feed = isBacktest ? null : describeFeed(run.detail);
  const title = BOT_META[run.bot_type]?.title ?? run.bot_type;
  const toggle = () => setExpanded((v) => !v);
  // Live runs share one audit file per day, which a bundle row carries; a backtest's is its own.
  const showAudit = Boolean(run.audit_log) && (!nested || isBacktest);

  return (
    <>
      <tr className="app-table-row align-top">
        <td
          className={`whitespace-nowrap py-2 tabular-nums text-xs ${nested ? "pl-8 pr-3 text-muted" : "px-3"}`}
        >
          {nested ? cycleTime(run.started_at) : (run.started_at ?? "—")}
        </td>
        <td className="px-3 py-2 text-xs">
          {expandable ? (
            <ExpandToggle expanded={expanded} onToggle={toggle}>
              {nested ? (isBacktest ? "Trades" : "Cycles") : title}
            </ExpandToggle>
          ) : nested ? null : (
            title
          )}
        </td>
        <td className="px-3 py-2 text-xs capitalize">
          {nested ? null : isBacktest ? (
            /* Marked, not just labelled: a backtest's P&L in this table must never be read as
               money a bot made. */
            <span className="rounded border border-border px-1.5 py-0.5 text-[11px] font-medium uppercase tracking-wide text-muted">
              Backtest
            </span>
          ) : (
            run.trigger.replace("_", " ")
          )}
        </td>
        <td className="px-3 py-2">{nested ? null : <StatusBadge status={run.status} />}</td>
        <td className="px-3 py-2 text-xs">
          <RunReason run={run} feed={feed} />
          {showAudit && run.audit_log && <AuditLink href={run.audit_log} backtest={isBacktest} />}
        </td>
      </tr>
      {expandable && expanded && (
        <tr>
          <td colSpan={5} className="bg-panel2 p-0">
            {isBacktest && backtestBot ? (
              <BacktestRunTrades runId={run.id} bot={backtestBot} />
            ) : (
              <CycleTable runId={run.id} />
            )}
          </td>
        </tr>
      )}
    </>
  );
}

function RunReason({
  run,
  feed,
  otherReasons = 0,
}: {
  run: BotRun;
  feed: ReturnType<typeof describeFeed>;
  otherReasons?: number;
}) {
  return (
    <>
      {/* Both halves matter: the text is for the user, the code is what support and tests can
          rely on when the text is later reworded. */}
      <div>{run.reason_text ?? "—"}</div>
      {(run.reason_code || otherReasons > 0) && (
        <div className="flex flex-wrap items-baseline gap-x-2">
          {run.reason_code && (
            <code className="app-text-muted text-[11px]">{run.reason_code}</code>
          )}
          {otherReasons > 0 && (
            <span className="text-[11px] text-faint">
              +{otherReasons} other reason{otherReasons === 1 ? "" : "s"}
            </span>
          )}
        </div>
      )}
      {feed && (
        /* The scalpers' reason codes are ambiguous on their own -- `not_warm` covers both a
           feed that is filling and one that was never subscribed. This is the half that tells
           them apart, kept in the log so it is still there tomorrow. */
        <div className={`mt-0.5 font-mono text-[11px] ${feedToneClass(feed.tone)}`}>
          {feed.text}
        </div>
      )}
    </>
  );
}

function BundleMembers({ bundle }: { bundle: BotRunBundle }) {
  const { data, isLoading, isError, error } = useBundleRuns(bundle, true);
  const runs = bundle.runs ?? data;

  if (!runs) {
    return (
      <tr>
        <td colSpan={5} className="py-2 pl-8 pr-3 text-xs">
          {isError ? (
            <span className="text-down">
              Could not load runs: {(error as Error)?.message ?? "unknown error"}
            </span>
          ) : (
            <span className="app-text-muted">{isLoading ? "Loading runs…" : "No runs."}</span>
          )}
        </td>
      </tr>
    );
  }
  return (
    <>
      {runs.map((run) => (
        <RunRow key={run.id} run={run} nested />
      ))}
    </>
  );
}

/** A stretch of back-to-back runs with the same day, bot, trigger and outcome. A lone run is
 *  just its row: an arrow that expands into the same single line would be noise. */
function BundleRow({ bundle }: { bundle: BotRunBundle }) {
  const [expanded, setExpanded] = useState(false);
  if (bundle.count === 1) return <RunRow run={bundle.latest} />;

  const { latest } = bundle;
  const isBacktest = bundle.trigger === "backtest";
  const title = BOT_META[bundle.bot_type]?.title ?? bundle.bot_type;

  return (
    <>
      <tr className="app-table-row align-top">
        <td className="whitespace-nowrap px-3 py-2 tabular-nums text-xs">
          {latest.started_at ?? "—"}
          <span className="ml-2 rounded bg-panel2 px-1.5 py-0.5 font-mono text-[11px] text-muted">
            ×{bundle.count}
          </span>
        </td>
        <td className="px-3 py-2 text-xs">
          <ExpandToggle expanded={expanded} onToggle={() => setExpanded((v) => !v)}>
            {title}
          </ExpandToggle>
        </td>
        <td className="px-3 py-2 text-xs capitalize">
          {isBacktest ? (
            <span className="rounded border border-border px-1.5 py-0.5 text-[11px] font-medium uppercase tracking-wide text-muted">
              Backtest
            </span>
          ) : (
            bundle.trigger.replace("_", " ")
          )}
        </td>
        <td className="px-3 py-2">
          <StatusBadge status={bundle.status} />
        </td>
        <td className="px-3 py-2 text-xs">
          <RunReason
            run={latest}
            feed={isBacktest ? null : describeFeed(latest.detail)}
            otherReasons={bundle.distinct_reasons - 1}
          />
          {bundle.audit_log && <AuditLink href={bundle.audit_log} backtest={isBacktest} />}
        </td>
      </tr>
      {expanded && <BundleMembers bundle={bundle} />}
    </>
  );
}

const PRESETS: readonly (readonly [RunLogPreset, string])[] = [
  ["today", "Today"],
  ["week", "Week"],
  ["month", "Month"],
  ["custom", "Custom"],
];

export function BotRunLog() {
  // Fixed for the page's life: a tab left open past midnight keeps the day it was opened on
  // until reloaded, rather than silently emptying under the user.
  const [today] = useState(istToday);
  const [preset, setPreset] = useState<RunLogPreset>("today");
  const [custom, setCustom] = useState<DateRange>(() => presetRange("week", today));

  const customError = preset === "custom" ? customRangeError(custom, today) : null;
  const range: DateRange | null =
    preset === "custom" ? (customError ? null : custom) : presetRange(preset, today);
  const { data, isLoading, isError, error } = useBotRunBundles(range);

  return (
    <section className="app-card p-4">
      <h2 className="app-text-heading">Activity</h2>
      <p className="app-text-muted mt-1 text-xs">
        Every scan, order, and skip across all bots — including the days nothing happened,
        and why. Backtests are listed here too, marked, with their trades and audit trail.
        Back-to-back runs with the same outcome are bundled; expand one to see each run.
      </p>

      <div className="mt-4 flex flex-wrap items-center gap-2.5">
        <div
          role="group"
          aria-label="Date range"
          className="inline-flex rounded-[9px] border border-border bg-panel2 p-[3px]"
        >
          {PRESETS.map(([value, label]) => (
            <button
              key={value}
              type="button"
              aria-pressed={preset === value}
              onClick={() => setPreset(value)}
              className={[
                "rounded-[6px] px-3 py-1 font-mono text-xs font-semibold transition",
                preset === value
                  ? "bg-accent-strong text-accent-ink"
                  : "text-muted hover:text-foreground",
              ].join(" ")}
            >
              {label}
            </button>
          ))}
        </div>
        {preset === "custom" && (
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <label className="flex items-center gap-1.5">
              <span className="text-muted">From</span>
              <input
                type="date"
                className="app-input py-1 text-xs"
                value={custom.from}
                max={today}
                onChange={(e) => setCustom((c) => ({ ...c, from: e.target.value }))}
              />
            </label>
            <label className="flex items-center gap-1.5">
              <span className="text-muted">To</span>
              <input
                type="date"
                className="app-input py-1 text-xs"
                value={custom.to}
                max={today}
                onChange={(e) => setCustom((c) => ({ ...c, to: e.target.value }))}
              />
            </label>
            {customError && <span className="text-down">{customError}</span>}
          </div>
        )}
      </div>

      {isLoading && range && <p className="app-text-muted mt-4 text-sm">Loading activity…</p>}
      {isError && (
        <p className="mt-4 text-sm text-rose-600 dark:text-rose-400">
          Could not load activity: {(error as Error)?.message ?? "unknown error"}
        </p>
      )}

      {data && data.length === 0 && (
        <p className="app-text-muted mt-4 text-sm">
          No bot activity in this period. Runs appear here once a bot is enabled.
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
              {data.map((bundle) => (
                <BundleRow key={bundleKey(bundle)} bundle={bundle} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
