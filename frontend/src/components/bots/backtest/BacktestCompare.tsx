"use client";

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { AsyncLabelSpan } from "@/components/ui/AsyncLabelSpan";
import { DatePicker } from "@/components/ui/DatePicker";
import { Select } from "@/components/ui/Select";
import { exitLabel, fetchBacktestCompare, inr, type ComparePayload, type CompareTotals } from "@/lib/bots-backtest";

const BOTS = [
  { value: "momentum" as const, label: "Bot 3 · Momentum scalper" },
  { value: "fly" as const, label: "Bot 4 · Iron fly" },
];

function Totals({ label, t }: { label: string; t: CompareTotals }) {
  return (
    <div className="app-card-muted p-3 text-xs">
      <div className="font-semibold text-foreground">{label}</div>
      <div className="mt-1 text-muted">
        {t.cycles} trades · gross {inr(t.gross_pnl)} · charges {inr(t.friction)}
      </div>
      <div className={`mt-1 font-mono text-base tabular-nums ${t.net_pnl >= 0 ? "text-up" : "text-down"}`}>{inr(t.net_pnl)}</div>
    </div>
  );
}

/**
 * The one check of whether the backtest can be believed: where the simulation and the
 * backtest took the same trade, the entry-price gap is the backtest's fill error.
 */
export function BacktestCompare({ defaultDate }: { defaultDate: string }) {
  const [bot, setBot] = useState<"momentum" | "fly">("momentum");
  const [date, setDate] = useState(defaultDate);
  const [error, setError] = useState<string | null>(null);
  const cmp = useMutation({
    mutationFn: () => fetchBacktestCompare(bot, date, false),
    onMutate: () => setError(null),
    onError: (e) => setError(e instanceof Error ? e.message : "Compare failed"),
  });
  const data: ComparePayload | undefined = cmp.data;

  return (
    <section className="space-y-3 rounded-[10px] border border-border px-4 py-3.5">
      <div>
        <h2 className="text-heading font-bold text-foreground">Compare with a simulation day</h2>
        <p className="mt-1 text-table leading-relaxed text-muted">
          Replays a day the bot ran in Simulation and lines the two up trade by trade. The median
          entry-price gap is how far backtest fills sit from the fills Simulation got against the
          live bid and ask.
        </p>
      </div>
      <div className="flex flex-wrap items-end gap-3">
        <label className="min-w-[14rem] space-y-1 text-xs text-muted">
          <span>Bot</span>
          <Select value={bot} options={BOTS} onChange={setBot} ariaLabel="Bot to compare" />
        </label>
        <label className="space-y-1 text-xs text-muted">
          <span>Simulation day</span>
          <DatePicker value={date} onChange={setDate} />
        </label>
        <button type="button" className="app-btn-secondary" disabled={cmp.isPending} onClick={() => cmp.mutate()}>
          <AsyncLabelSpan busy={cmp.isPending} busyLabel="Comparing…" idleLabel="Compare" />
        </button>
      </div>
      {error ? <div className="app-alert-error text-xs">{error}</div> : null}

      {data ? (
        <div className="space-y-3">
          {data.settings_changed ? (
            <p className="app-card-muted p-3 text-xs text-accent-strong">
              This bot&apos;s settings have changed since that day, and the backtest uses today&apos;s
              settings, so the two describe different strategies.
            </p>
          ) : null}
          {data.lots ? <p className="text-xs text-muted">Replayed at {data.lots} lots, the simulation&apos;s size.</p> : null}
          <div className="grid gap-3 sm:grid-cols-3">
            <Totals label="Simulation" t={data.paper} />
            <Totals label="Backtest" t={data.backtest} />
            <div className="app-card-muted p-3 text-xs">
              <div className="font-semibold text-foreground">Median entry gap</div>
              <div className="mt-1 font-mono text-base tabular-nums text-foreground">
                {data.median_abs_entry_diff == null ? "—" : `₹${data.median_abs_entry_diff.toFixed(2)} a unit`}
              </div>
              <div className="mt-1 text-muted">over {data.pairs.length} matched trades</div>
            </div>
          </div>
          {data.pairs.length ? (
            <div className="app-table-wrap">
              <table className="min-w-full text-left text-table">
                <thead className="app-table-head">
                  <tr>
                    <th className="px-2.5 py-2 font-semibold">Contract</th>
                    <th className="px-2.5 py-2 font-semibold">Sim / backtest entry</th>
                    <th className="px-2.5 py-2 text-right font-semibold">Sim price</th>
                    <th className="px-2.5 py-2 text-right font-semibold">Backtest price</th>
                    <th className="px-2.5 py-2 text-right font-semibold">Gap</th>
                    <th className="px-2.5 py-2 font-semibold">Exit sim / backtest</th>
                    <th className="px-2.5 py-2 text-right font-semibold">Net sim / backtest</th>
                  </tr>
                </thead>
                <tbody>
                  {data.pairs.map((p) => (
                    <tr key={`${p.paper_at}-${p.contract}`} className="app-table-row">
                      <td className="px-2.5 py-1.5 whitespace-nowrap text-foreground">{p.contract}</td>
                      <td className="px-2.5 py-1.5 whitespace-nowrap text-muted">
                        {p.paper_at} / {p.backtest_at}
                      </td>
                      <td className="px-2.5 py-1.5 text-right font-mono tabular-nums">{p.paper_entry?.toFixed(2) ?? "—"}</td>
                      <td className="px-2.5 py-1.5 text-right font-mono tabular-nums">{p.backtest_entry?.toFixed(2) ?? "—"}</td>
                      <td className="px-2.5 py-1.5 text-right font-mono tabular-nums">
                        {p.entry_diff == null ? "—" : `${p.entry_diff > 0 ? "+" : ""}${p.entry_diff.toFixed(2)}`}
                      </td>
                      <td className="px-2.5 py-1.5 whitespace-nowrap text-muted">
                        {exitLabel(p.paper_exit)} / {exitLabel(p.backtest_exit)}
                      </td>
                      <td className="px-2.5 py-1.5 text-right font-mono tabular-nums whitespace-nowrap">
                        {inr(p.paper_net)} / {inr(p.backtest_net)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="text-xs text-muted">No trade was taken by both on the same contract within two minutes.</p>
          )}
          {data.paper_only.length || data.backtest_only.length ? (
            <p className="text-xs text-faint">
              {data.paper_only.length ? `Simulation only: ${data.paper_only.join(", ")}. ` : ""}
              {data.backtest_only.length ? `Backtest only: ${data.backtest_only.join(", ")}.` : ""}
              {" "}Totals drift apart for reasons other than fills: once one trade exits at a different
              moment, every later trade can differ.
            </p>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
