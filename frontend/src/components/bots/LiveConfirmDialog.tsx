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
