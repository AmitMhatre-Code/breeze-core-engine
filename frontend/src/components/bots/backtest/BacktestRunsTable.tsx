"use client";

import { formatApiDateTime } from "@/lib/format-iso-date";
import { inr, summaryTotals, type BacktestRunListItem } from "@/lib/bots-backtest";

export function BacktestRunsTable({
  runs,
  selectedId,
  onSelect,
}: {
  runs: BacktestRunListItem[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  if (runs.length === 0) {
    return (
      <section className="rounded-[10px] border border-border px-4 py-3.5">
        <h2 className="text-heading font-bold text-foreground">Runs</h2>
        <p className="mt-1 text-xs text-muted">No backtests yet. Choose a bot and a range above.</p>
      </section>
    );
  }
  return (
    <section className="space-y-2 rounded-[10px] border border-border px-4 py-3.5">
      <h2 className="text-heading font-bold text-foreground">Runs</h2>
      <div className="app-table-wrap">
        <table className="min-w-full text-left text-table">
          <thead className="app-table-head">
            <tr>
              <th className="px-2.5 py-2 font-semibold whitespace-nowrap">Run</th>
              <th className="px-2.5 py-2 font-semibold whitespace-nowrap">Bot</th>
              <th className="px-2.5 py-2 font-semibold whitespace-nowrap">Range</th>
              <th className="px-2.5 py-2 font-semibold whitespace-nowrap">Prices</th>
              <th className="px-2.5 py-2 text-right font-semibold whitespace-nowrap">Trades</th>
              <th className="px-2.5 py-2 text-right font-semibold whitespace-nowrap">Net</th>
              <th className="px-2.5 py-2 font-semibold whitespace-nowrap">Status</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => {
              const totals = summaryTotals(run.summary);
              const waiting = Number(run.summary?.days_awaiting_data ?? 0);
              const selected = run.id === selectedId;
              return (
                <tr
                  key={run.id}
                  className={`app-table-row cursor-pointer ${selected ? "bg-accent-tint" : ""}`}
                  onClick={() => onSelect(run.id)}
                >
                  <td className="px-2.5 py-2 whitespace-nowrap text-foreground">{formatApiDateTime(run.created_at)}</td>
                  <td className="px-2.5 py-2 whitespace-nowrap text-foreground">{run.params.label ?? run.bot}</td>
                  <td className="px-2.5 py-2 whitespace-nowrap text-muted">
                    {run.params.from} → {run.params.to}
                  </td>
                  <td className="px-2.5 py-2 whitespace-nowrap text-muted">{run.params.model ? "Model" : "Real"}</td>
                  <td className="px-2.5 py-2 text-right font-mono tabular-nums text-foreground">{totals.trades ?? "—"}</td>
                  <td
                    className={`px-2.5 py-2 text-right font-mono tabular-nums ${
                      (totals.net ?? 0) >= 0 ? "text-up" : "text-down"
                    }`}
                  >
                    {inr(totals.net)}
                  </td>
                  <td className="px-2.5 py-2 whitespace-nowrap">
                    <span
                      className={
                        run.status === "completed"
                          ? waiting
                            ? "text-accent-strong"
                            : "text-foreground"
                          : run.status === "running"
                            ? "text-accent-strong"
                            : "text-down"
                      }
                      title={run.error ?? undefined}
                    >
                      {run.status === "completed" && waiting ? `${waiting} days need data` : run.status}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
