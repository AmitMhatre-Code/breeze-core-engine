"use client";

import { formatIndianMoneyCompact, moneyToneClass } from "@/lib/format-money-in";
import { BACKTEST_SLUG } from "@/lib/bots-backtest";
import { useBotRuns, type BotType } from "@/lib/use-bots";

/**
 * The card's line for this bot's most recent backtest: its net P&L, marked as a backtest so
 * it can never be read as money the bot made (design-decisions #35).
 *
 * Read from the shared Activity query rather than a query of its own — the backtest's row there
 * is the record, and the cards and the table then cannot disagree. Rendered on every card, with a
 * dash where there is nothing, so the stat rows still read across the grid as a table.
 */
export function LastBacktestRow({ botType }: { botType: BotType }) {
  const runs = useBotRuns().data;
  const backtestable = Boolean(BACKTEST_SLUG[botType]);
  const last = backtestable
    ? (runs ?? []).find((r) => r.bot_type === botType && r.trigger === "backtest" && r.status !== "running")
    : undefined;
  const detail = (last?.detail ?? null) as
    | { from?: string; to?: string; summary?: { net_pnl?: number; cycles?: number; days_awaiting_data?: number } }
    | null;
  const net = detail?.summary?.net_pnl;
  const period = detail?.from ? (detail.from === detail.to ? detail.from : `${detail.from} → ${detail.to}`) : null;

  return (
    <div className="flex items-baseline justify-between gap-3 text-hint">
      <dt className="flex items-center gap-1.5 text-faint">
        Last backtest
        {last ? (
          <span className="rounded border border-border px-1 py-px text-[10px] font-medium uppercase tracking-wide text-muted">
            Backtest
          </span>
        ) : null}
      </dt>
      <dd
        className="m-0 font-mono tabular-nums"
        title={
          last
            ? `${period ?? ""}${detail?.summary?.days_awaiting_data ? ` · ${detail.summary.days_awaiting_data} day(s) had no data` : ""}`
            : undefined
        }
      >
        {!backtestable ? (
          <span className="text-faint">n/a</span>
        ) : !last ? (
          <span className="text-text">—</span>
        ) : last.status === "failed" ? (
          <span className="text-down">Failed</span>
        ) : typeof net === "number" ? (
          <span className={moneyToneClass(net)}>{formatIndianMoneyCompact(net)}</span>
        ) : (
          <span className="text-text">—</span>
        )}
      </dd>
    </div>
  );
}
