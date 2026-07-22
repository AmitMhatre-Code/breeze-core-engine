"use client";

import { useQuery } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { Modal } from "@/components/ui/Modal";
import { formatIndianMoneyCompact } from "@/lib/format-money-in";
import {
  SQUAREOFF_RULES_QUERY_KEY,
  fetchSquareOffRules,
  type SquareOffRuleRecord,
} from "@/lib/portfolio/squareoff-rules";
import { sb } from "@/lib/strategy-builder/ui";

/** Statuses that still depend on a broker session: `armed` needs it to place exit
 * orders, `triggered`/`fired` need the order feed to reach Completed. `reset` is
 * excluded — its monitoring has already stopped, so logging out takes nothing more. */
const MONITORING_STATUSES = new Set(["armed", "triggered", "fired"]);

export function selectMonitoringRules(
  rules: SquareOffRuleRecord[] | undefined,
): SquareOffRuleRecord[] {
  return (rules ?? []).filter((r) => MONITORING_STATUSES.has(r.status));
}

export type LogoutConfirmDialogProps = {
  open: boolean;
  onClose: () => void;
};

/**
 * Logout is not a neutral navigation while PB/SL is armed — it clears the stored broker
 * session, and with it the engine's ability to place exit orders headless. Closing the
 * tab or letting the app session lapse does not do that; only this does. So the one
 * moment protection is about to end is the one moment we say so.
 */
export function LogoutConfirmDialog({ open, onClose }: LogoutConfirmDialogProps) {
  const router = useRouter();

  const rulesQ = useQuery({
    queryKey: SQUAREOFF_RULES_QUERY_KEY,
    queryFn: fetchSquareOffRules,
    enabled: open,
    // Fetch on open rather than polling: this is a one-shot question asked on click,
    // and the shell renders on every page.
    staleTime: 0,
  });

  const monitoring = selectMonitoringRules(rulesQ.data);
  const checking = rulesQ.isLoading;

  return (
    <Modal
      open={open}
      onClose={onClose}
      role="alertdialog"
      titleId="logout-confirm-title"
      descriptionId="logout-confirm-body"
      panelClassName={`${sb.modalPanel} !max-w-[min(96vw,32rem)] mx-auto`}
    >
      <h3 id="logout-confirm-title" className="app-text-title">
        Log out?
      </h3>

      <div id="logout-confirm-body" className="mt-2 text-sm leading-relaxed text-muted">
        {checking ? (
          <p>Checking your active Profit Booking / Stop Loss rules…</p>
        ) : monitoring.length > 0 ? (
          <>
            <div
              role="alert"
              className="rounded-md border border-amber-accent/40 bg-amber-tint px-3 py-2.5 text-amber-on-tint"
            >
              <p className="font-semibold">
                {monitoring.length === 1
                  ? "1 Strategy Group is being monitored right now."
                  : `${monitoring.length} Strategy Groups are being monitored right now.`}
              </p>
              <p className="mt-1">
                Logging out ends your broker session, so Profit Booking / Stop Loss will{" "}
                <span className="font-semibold">stop monitoring</span>
                {" and no exit orders will be placed. You'll need to log back in and re-arm."}
              </p>
            </div>
            <ul className="mt-3 divide-y divide-border-soft">
              {monitoring.map((r) => (
                <li
                  key={r.id}
                  className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 py-2"
                >
                  <span className="font-mono text-sm text-foreground">
                    {r.stock_code} · {r.expiry_display}
                  </span>
                  <span className="font-mono text-xs tabular-nums">
                    <span className="text-up">
                      +{formatIndianMoneyCompact(Math.abs(r.profit_target_pnl))}
                    </span>
                    <span className="text-faint"> / </span>
                    <span className="text-down">
                      −{formatIndianMoneyCompact(Math.abs(r.loss_limit_pnl))}
                    </span>
                  </span>
                </li>
              ))}
            </ul>
            <p className="mt-3">
              To keep monitoring running, close this tab instead — PB/SL keeps working
              without the browser until your broker session expires at midnight IST.
            </p>
          </>
        ) : rulesQ.isError ? (
          <p>
            Couldn&apos;t check whether Profit Booking / Stop Loss is active. If you have
            armed rules, logging out will stop them monitoring.
          </p>
        ) : (
          <p>You&apos;ll need to sign in again, including the ICICI broker login.</p>
        )}
      </div>

      <div className="grid grid-cols-1 gap-2 pt-4 sm:grid-cols-2 sm:gap-3">
        <button
          type="button"
          className="app-btn-secondary h-10 min-h-10 w-full sm:h-11 sm:min-h-11"
          onClick={onClose}
        >
          Stay signed in
        </button>
        <button
          type="button"
          className={`${sb.btnPrimary} h-10 min-h-10 w-full sm:h-11 sm:min-h-11`}
          disabled={checking}
          onClick={() => {
            onClose();
            router.push("/logout");
          }}
        >
          {monitoring.length > 0 ? "Log out and stop monitoring" : "Log out"}
        </button>
      </div>
    </Modal>
  );
}
