"use client";

import { useBotRuns, type BotRun, type BotRunStatus, BOT_META } from "@/lib/use-bots";

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

function RunRow({ run }: { run: BotRun }) {
  return (
    <tr className="app-table-row align-top">
      <td className="whitespace-nowrap px-3 py-2 tabular-nums text-xs">
        {run.started_at ?? "—"}
      </td>
      <td className="px-3 py-2 text-xs">{BOT_META[run.bot_type]?.title ?? run.bot_type}</td>
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
      </td>
    </tr>
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
