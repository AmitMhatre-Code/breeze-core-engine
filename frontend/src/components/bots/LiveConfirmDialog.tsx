"use client";

import { useRef } from "react";
import { Modal } from "@/components/ui/Modal";
import { formatIndianMoneyCompact, moneyToneClass } from "@/lib/format-money-in";
import type { LiveEligibility } from "@/lib/use-bots";

/** The confirmation that stands in front of unattended real orders.
 *
 *  Switching a scalper to Live is the one control on the bots surface that commits the user
 *  to **real orders placed on a signal that turns over in seconds, with no per-trade
 *  approval by construction**. Every other mode change is undone by the next click; this one
 *  can have placed a position before the user's hand leaves the mouse.
 *
 *  Two jobs, and the second is the point:
 *
 *  1. State plainly what Live means, including the daily downside being authorised.
 *  2. **Show the paper evidence the gate was satisfied by.** The gate only asserts that a
 *     full paper trading day happened on these settings -- it has no opinion on whether that
 *     day was any good. That judgement is the user's, and they can only make it with the
 *     numbers in front of them, at the moment they are deciding.
 *
 *  Deliberately not a typed phrase or a checkbox. The consequence is real, but so is the
 *  fact that someone running these bots does this repeatedly; "type LIVE to continue" would
 *  be theatre by the third time and would train the habit of clicking through.
 */
export function LiveConfirmDialog({
  open,
  botTitle,
  evidence,
  dailyLossCapInr,
  combinedDownsideInr,
  pending,
  error,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  botTitle: string;
  evidence: LiveEligibility | undefined;
  dailyLossCapInr: number;
  /** The SUM across both scalpers when the other is also live -- separate stops mean the
   *  real exposure is the total, not the number set on either (plan section 5.2). */
  combinedDownsideInr: number | null;
  pending: boolean;
  error: string | null;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  // Focus starts on Cancel, not on the confirming button: the safe choice should be the one
  // a stray Enter takes.
  const cancelRef = useRef<HTMLButtonElement>(null);
  const days = evidence?.sessions ?? [];
  const backtest = evidence?.backtest ?? null;

  return (
    <Modal
      open={open}
      onClose={onCancel}
      pending={pending}
      role="alertdialog"
      titleId="live-confirm-title"
      initialFocusRef={cancelRef}
      panelClassName="w-full max-w-lg rounded-xl border-2 border-down bg-panel p-5 shadow-pop"
    >
      <h2
        id="live-confirm-title"
        className="text-lg font-bold uppercase tracking-wide text-down"
      >
        Enable live trading?
      </h2>

      <div className="mt-3 space-y-3 text-sm leading-relaxed text-text">
        <p>
          <strong>{botTitle}</strong> will place <strong>real orders on the exchange</strong>,
          on its own, whenever its signal fires inside a session window — until you set it
          back to Simulation or Off. It does not ask before each trade.
        </p>

        <div className="rounded-lg border border-border bg-panel2 p-3">
          <div className="flex items-baseline justify-between gap-3 text-hint">
            <span className="text-faint">This bot&apos;s daily loss cap</span>
            <span className="font-mono tabular-nums text-text">
              {formatIndianMoneyCompact(dailyLossCapInr)}
            </span>
          </div>
          {combinedDownsideInr !== null && (
            <div className="mt-1.5 flex items-baseline justify-between gap-3 text-hint">
              {/* Stated because the caps SUM. Two ₹10,000 stops is a ₹20,000 day, and that
                  is the number the user is actually authorising. */}
              <span className="text-faint">Combined, with the other scalper live</span>
              <span className="font-mono tabular-nums text-down">
                {formatIndianMoneyCompact(combinedDownsideInr)}
              </span>
            </div>
          )}
        </div>

        <div>
          <p className="text-hint text-faint">
            Simulation evidence on these exact settings — changing any setting that affects P&amp;L
            starts this over:
          </p>
          <ul className="mt-1.5 space-y-1">
            {days.map((d) => (
              <li
                key={d.trading_day}
                className="flex items-baseline justify-between gap-3 rounded border border-border bg-panel2 px-2.5 py-1.5 text-hint"
              >
                <span className="font-mono tabular-nums text-text">{d.trading_day}</span>
                <span className="text-faint">
                  {d.closed_cycles} cycle{d.closed_cycles === 1 ? "" : "s"}
                  {d.closed_cycles > 0 && ` · ${d.wins}W/${d.losses}L`}
                </span>
                <span className={`font-mono tabular-nums ${moneyToneClass(d.net_pnl)}`}>
                  {d.closed_cycles > 0 ? formatIndianMoneyCompact(d.net_pnl) : "—"}
                </span>
              </li>
            ))}
          </ul>
          {evidence && evidence.closed_cycles > 0 && (
            <p className="mt-1.5 text-hint text-faint">
              Across {evidence.days} day{evidence.days === 1 ? "" : "s"}:{" "}
              <span className={moneyToneClass(evidence.net_pnl)}>
                {formatIndianMoneyCompact(evidence.net_pnl)}
              </span>{" "}
              net after {formatIndianMoneyCompact(evidence.friction)} of friction.
            </p>
          )}
          {evidence && evidence.closed_cycles === 0 && (
            // A quiet day still satisfies the gate, and saying so is more honest than a
            // row of dashes the user has to interpret.
            <p className="mt-1.5 text-hint text-faint">
              The simulation day produced no completed cycles, so there is no P&amp;L record to
              judge these settings on yet.
            </p>
          )}
        </div>

        {/* The other half of the evidence. A Simulation day proves the bot runs end to end on
            live plumbing and real fills; only a replay over many days says anything about edge.
            Neither is required by the gate — both are here so the judgement is made on both. */}
        <div>
          <p className="text-hint text-faint">Backtest on these exact settings:</p>
          {backtest ? (
            <div className="mt-1.5 rounded border border-border bg-panel2 px-2.5 py-2 text-hint">
              <div className="flex items-baseline justify-between gap-3">
                <span className="font-mono tabular-nums text-text">
                  {backtest.from_date} → {backtest.to_date}
                </span>
                <span className={`font-mono tabular-nums ${moneyToneClass(backtest.net_pnl)}`}>
                  {formatIndianMoneyCompact(backtest.net_pnl)}
                </span>
              </div>
              <div className="mt-1 text-faint">
                {backtest.days_replayed} day{backtest.days_replayed === 1 ? "" : "s"} ·{" "}
                {backtest.cycles} trade{backtest.cycles === 1 ? "" : "s"}
                {backtest.win_rate_pct !== null ? ` · ${backtest.win_rate_pct}% won` : ""} · after{" "}
                {formatIndianMoneyCompact(backtest.friction)} of friction
              </div>
              {/* Model runs price every strike off Black-Scholes, so they describe the rules,
                  not what the market would have filled. */}
              {backtest.price_source.toLowerCase().includes("model") && (
                <div className="mt-1 text-amber-accent">
                  Model-priced run — evidence about the rules, not about fills.
                </div>
              )}
              {backtest.days_awaiting_data > 0 && (
                <div className="mt-1 text-amber-accent">
                  {backtest.days_awaiting_data} day
                  {backtest.days_awaiting_data === 1 ? " is" : "s are"} missing option prices and
                  not in these totals.
                </div>
              )}
              {/* The backtest has no bid or ask — its fills use a modelled spread — so the only
                  evidence that its prices are achievable is a day checked against Simulation. */}
              <div className="mt-1 text-faint">
                {backtest.compare_median_entry_gap !== null ? (
                  <>
                    Fills checked against Simulation on {backtest.compare_day}: median entry gap ₹
                    {backtest.compare_median_entry_gap.toFixed(2)} a unit
                    {backtest.compare_pairs ? ` over ${backtest.compare_pairs} matched trades` : ""}.
                  </>
                ) : (
                  "Fills have not been checked against a Simulation day, so its entry prices are a modelled spread, not observed ones."
                )}
              </div>
            </div>
          ) : (
            <p className="mt-1.5 text-hint text-faint">
              None. The gate does not require one — but a single Simulation day says whether this
              bot runs, not whether it makes money.
            </p>
          )}
        </div>

        {error && <p className="text-hint text-down">{error}</p>}
      </div>

      <div className="mt-5 flex justify-end gap-2">
        <button
          ref={cancelRef}
          type="button"
          disabled={pending}
          onClick={onCancel}
          className="app-btn-outline px-4 py-2.5 text-sm"
        >
          Cancel
        </button>
        <button
          type="button"
          disabled={pending}
          onClick={onConfirm}
          className="inline-flex items-center justify-center rounded-lg bg-down-btn px-4 py-2.5 text-sm font-bold text-down-ink transition hover:brightness-[1.06] focus:outline-none focus-visible:ring-2 focus-visible:ring-down/40 disabled:pointer-events-none disabled:opacity-50"
        >
          {pending ? "Enabling…" : "Enable live trading"}
        </button>
      </div>
    </Modal>
  );
}
