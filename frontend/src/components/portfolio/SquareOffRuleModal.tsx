"use client";

import { useEffect, useState, type ChangeEvent } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { Modal } from "@/components/ui/Modal";
import { sb } from "@/lib/strategy-builder/ui";
import { formatSignedRupees } from "@/lib/portfolio/totals";
import {
  armSquareOffRule,
  cancelSquareOffOrphanOrders,
  disarmSquareOffRule,
  type SquareOffRuleRecord,
} from "@/lib/portfolio/squareoff-rules";
import {
  resetHazardTier,
  resetMessage,
  resetNoteClassName,
} from "@/lib/portfolio/reset-warning";
import { fetchTelegramStatus, TELEGRAM_STATUS_QUERY_KEY } from "@/lib/telegram/telegram-alerts";

/** Indian-grouped digits only (no decimals — thresholds are whole rupees). */
function digitsToIndianGroups(digits: string): string {
  if (!digits) return "";
  return Number(digits).toLocaleString("en-IN");
}

const PCT_MIN = 1;
const PCT_MAX = 20;

function isValidPct(value: string): boolean {
  if (!/^\d{1,2}$/.test(value)) return false;
  const n = Number(value);
  return n >= PCT_MIN && n <= PCT_MAX;
}

function handlePctInput(e: ChangeEvent<HTMLInputElement>, setValue: (v: string) => void) {
  setValue(e.target.value.replace(/\D/g, "").slice(0, 2));
}

/**
 * Reformats an amount `<input>` with Indian comma grouping (₹1,00,000 style)
 * as the user types, preserving cursor position across the reformat so
 * typing in the middle of the number doesn't jump the caret to the end.
 */
function handleAmountInput(
  e: ChangeEvent<HTMLInputElement>,
  setValue: (v: string) => void,
) {
  const input = e.target;
  const cursorPos = input.selectionStart ?? input.value.length;
  const digitsBeforeCursor = input.value
    .slice(0, cursorPos)
    .replace(/\D/g, "").length;
  const digitsOnly = input.value.replace(/\D/g, "");
  const formatted = digitsToIndianGroups(digitsOnly);

  input.value = formatted;
  let seenDigits = 0;
  let newCursorPos = formatted.length;
  for (let i = 0; i < formatted.length; i++) {
    if (/\d/.test(formatted[i])) seenDigits++;
    if (seenDigits === digitsBeforeCursor) {
      newCursorPos = i + 1;
      break;
    }
  }
  if (digitsBeforeCursor === 0) newCursorPos = 0;
  input.setSelectionRange(newCursorPos, newCursorPos);
  setValue(formatted);
}

const statusCopy: Record<SquareOffRuleRecord["status"], { label: string; className: string }> = {
  armed: { label: "Armed", className: "bg-accent-strong text-accent-ink" },
  triggered: { label: "Armed", className: "bg-accent-strong text-accent-ink" },
  fired: { label: "Fired", className: "bg-accent-strong text-accent-ink" },
  completed: { label: "Completed", className: "bg-up-tint text-up-on-tint" },
  reset: { label: "Reset", className: "bg-panel2 text-faint" },
  disarmed: { label: "Disarmed", className: "bg-panel2 text-faint" },
};

/** The Reset detail block: what happened, and — crucially — what is still live.
 *
 * This is the one surface with room for the whole picture, so it carries the orphan list
 * with their `orderReference`s. Reset withdraws future automation but does not retract
 * orders already placed, so "monitoring stopped" alone would be actively misleading. */
function ResetDetail({ rule }: { rule: SquareOffRuleRecord }) {
  const orphans = rule.orphan_orders ?? [];
  const tier = resetHazardTier(rule);
  return (
    <>
      <p
        className={`rounded-md border px-3 py-2 text-sm leading-relaxed ${resetNoteClassName(rule)}`}
        role={tier === "contra_risk" ? "alert" : undefined}
      >
        {resetMessage(rule)}
      </p>
      {orphans.length > 0 ? (
        <div>
          <div className={sb.fieldLabel}>Still live</div>
          <div className="overflow-hidden rounded-md border border-border">
            <table className="w-full border-collapse">
              <tbody>
                {orphans.map((o) => (
                  <tr key={o.order_id} className="border-t border-border-soft first:border-t-0">
                    <td className="px-2.5 py-2">
                      <div className="font-mono text-xs text-foreground">
                        {o.stock_code} {o.strike_price}{" "}
                        {o.right.toLowerCase().startsWith("c") ? "CE" : "PE"}
                      </div>
                      <div className="font-mono text-micro text-faint">{o.order_id}</div>
                    </td>
                    <td className="px-2.5 py-2 font-mono text-xs text-muted">{o.action}</td>
                    <td className="px-2.5 py-2 text-right font-mono text-xs text-muted">
                      {o.quantity}
                    </td>
                    <td className="px-2.5 py-2 text-right font-mono text-xs text-muted">
                      {o.price ?? "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-1.5 text-hint leading-relaxed text-faint">
            Reset stops future automation — it doesn&rsquo;t retract orders already placed.
            You can&rsquo;t set a new rule until these fill, expire, or you cancel them.
          </p>
        </div>
      ) : null}
    </>
  );
}

/**
 * Group-level profit/loss exit rule — mirrors `SquareOffLegsModal`'s shape
 * (a focused confirm modal) rather than an inline panel, so an armed group
 * costs nothing on the row until it's reopened or fires.
 *
 * A still-`armed` rule's target/stop values are directly editable — submitting
 * calls the same arm endpoint, which upserts the existing row rather than
 * inserting a duplicate (see `repositories/squareoff_rules.arm_rule`). Once a
 * rule has moved past `armed` (triggered/fired/fire_failed) there's nothing
 * left to update, so the modal falls back to view-only + "Disarm" only.
 */
export function SquareOffRuleModal({
  open,
  onClose,
  stockCode,
  expiryDisplay,
  exchangeCode,
  currentPnl,
  existingRule,
  onArmed,
  onDisarmed,
}: {
  open: boolean;
  onClose: () => void;
  stockCode: string;
  expiryDisplay: string;
  exchangeCode: string;
  currentPnl: number | null;
  existingRule?: SquareOffRuleRecord | null;
  onArmed: (record: SquareOffRuleRecord) => void;
  onDisarmed: () => void;
}) {
  const [profitTarget, setProfitTarget] = useState("");
  const [lossLimit, setLossLimit] = useState("");
  const [targetPremiumPct, setTargetPremiumPct] = useState("");
  const [stopLossPremiumPct, setStopLossPremiumPct] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [disarming, setDisarming] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setProfitTarget(
      existingRule ? digitsToIndianGroups(String(existingRule.profit_target_pnl)) : "",
    );
    setLossLimit(
      existingRule ? digitsToIndianGroups(String(existingRule.loss_limit_pnl)) : "",
    );
    setTargetPremiumPct(existingRule ? String(existingRule.target_premium_pct) : "");
    setStopLossPremiumPct(existingRule ? String(existingRule.stop_loss_premium_pct) : "");
    setError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const pnl = formatSignedRupees(currentPnl);
  const isReset = existingRule?.status === "reset";
  // A Reset SG is re-armable only once its own exit orders are terminal. Otherwise a
  // re-fire would stack a SECOND exit order on top of each live one — double-exiting the
  // leg and potentially flipping the position net-contra. Same condition blocks dismissal:
  // hiding the card would hide live risk.
  const rearmBlocked = Boolean(existingRule?.rearm_blocked);
  const editable =
    !existingRule ||
    existingRule.status === "armed" ||
    (isReset && !rearmBlocked);
  const target = Number(profitTarget.replace(/,/g, ""));
  const stop = Number(lossLimit.replace(/,/g, ""));
  const canSubmit =
    editable &&
    Number.isFinite(target) &&
    target > 0 &&
    Number.isFinite(stop) &&
    stop > 0 &&
    isValidPct(targetPremiumPct) &&
    isValidPct(stopLossPremiumPct);

  const handleSubmit = async () => {
    if (!canSubmit || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const record = await armSquareOffRule({
        stock_code: stockCode,
        expiry_date: expiryDisplay,
        exchange_code: exchangeCode,
        profit_target_pnl: target,
        loss_limit_pnl: stop,
        target_premium_pct: Number(targetPremiumPct),
        stop_loss_premium_pct: Number(stopLossPremiumPct),
      });
      onArmed(record);
      onClose();
    } catch (e) {
      setError(
        e instanceof Error
          ? e.message
          : "Failed to arm Profit Booking / Stop Loss rule",
      );
    } finally {
      setSubmitting(false);
    }
  };

  const handleCancelOrphans = async () => {
    if (!existingRule || cancelling) return;
    setCancelling(true);
    setError(null);
    try {
      const result = await cancelSquareOffOrphanOrders(existingRule.id);
      if (result.failed.length > 0) {
        setError(
          `${result.failed.length} order(s) couldn't be cancelled: ` +
            `${result.failed.map((f) => f.error).join("; ")}`,
        );
      }
      // Always refresh: even a partial success changes what's still live, and the caller
      // re-reads the rule to recompute the hazard tier.
      onDisarmed();
      if (result.failed.length === 0) onClose();
    } catch (e) {
      setError(
        e instanceof Error ? e.message : "Failed to cancel the remaining exit orders",
      );
    } finally {
      setCancelling(false);
    }
  };

  const handleDisarm = async () => {
    if (!existingRule || disarming) return;
    setDisarming(true);
    setError(null);
    try {
      await disarmSquareOffRule(existingRule.id);
      onDisarmed();
      onClose();
    } catch (e) {
      setError(
        e instanceof Error
          ? e.message
          : "Failed to disarm Profit Booking / Stop Loss rule",
      );
    } finally {
      setDisarming(false);
    }
  };

  const status = existingRule ? statusCopy[existingRule.status] : null;

  const telegramStatusQ = useQuery({
    queryKey: TELEGRAM_STATUS_QUERY_KEY,
    queryFn: fetchTelegramStatus,
    staleTime: 10_000,
  });
  const telegramStatus = telegramStatusQ.data;
  const showTelegramRegisterMessage =
    telegramStatusQ.isSuccess && !!telegramStatus?.bot_configured && !telegramStatus.connected;

  return (
    <Modal
      open={open}
      onClose={onClose}
      titleId="squareoff-rule-title"
      zIndexClass="z-[110]"
      panelClassName={`${sb.modalPanel} !w-max max-w-[min(96vw,26rem)] mx-auto`}
      pending={submitting || disarming}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <h3
            id="squareoff-rule-title"
            className="flex items-center gap-2 text-base font-semibold text-foreground"
          >
            <span>Set P&amp;L Profit Booking / Stop Loss</span>
            {status ? (
              <span
                className={`rounded-full px-2 py-0.5 font-mono text-[11px] font-semibold ${status.className}`}
              >
                {status.label}
              </span>
            ) : null}
          </h3>
          <p className="mt-1 text-sm leading-relaxed text-muted">
            {stockCode} &middot; {expiryDisplay}
          </p>
        </div>
        <button
          type="button"
          className="-m-1 size-9 shrink-0 rounded-lg text-xl leading-none text-muted transition hover:bg-border-soft"
          onClick={onClose}
          aria-label="Close"
        >
          &times;
        </button>
      </div>

      {existingRule && isReset ? <ResetDetail rule={existingRule} /> : null}

      <div className="flex items-center justify-between rounded-md border border-border-soft bg-panel2 px-3.5 py-2.5 text-sm">
        <span className="text-muted">Current group P&amp;L</span>
        <span className={`font-mono font-semibold tabular-nums ${pnl.className}`}>
          {pnl.text}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-4">
        <label className="block">
          <span className={sb.fieldLabel} style={{ color: "var(--up)" }}>
            Profit target
          </span>
          <div className="flex items-center gap-1.5">
            <span className="text-sm text-faint">₹</span>
            <input
              type="text"
              inputMode="decimal"
              className={sb.input}
              value={profitTarget}
              onChange={(e) => handleAmountInput(e, setProfitTarget)}
              placeholder="1,00,000"
              disabled={!editable}
            />
          </div>
        </label>
        <label className="block">
          <span className={sb.fieldLabel} style={{ color: "var(--down)" }}>
            Loss limit
          </span>
          <div className="flex items-center gap-1.5">
            <span className="text-sm text-faint">₹</span>
            <input
              type="text"
              inputMode="decimal"
              className={sb.input}
              value={lossLimit}
              onChange={(e) => handleAmountInput(e, setLossLimit)}
              placeholder="20,000"
              disabled={!editable}
            />
          </div>
        </label>
        <label className="block">
          <span className={sb.fieldLabel} style={{ color: "var(--up)" }}>
            Profit-booking offset
          </span>
          <div className="flex items-center gap-1.5">
            <input
              type="text"
              inputMode="numeric"
              className={sb.input}
              value={targetPremiumPct}
              onChange={(e) => handlePctInput(e, setTargetPremiumPct)}
              placeholder="10"
              disabled={!editable}
            />
            <span className="text-sm text-faint">%</span>
          </div>
        </label>
        <label className="block">
          <span className={sb.fieldLabel} style={{ color: "var(--down)" }}>
            Stop-loss offset
          </span>
          <div className="flex items-center gap-1.5">
            <input
              type="text"
              inputMode="numeric"
              className={sb.input}
              value={stopLossPremiumPct}
              onChange={(e) => handlePctInput(e, setStopLossPremiumPct)}
              placeholder="5"
              disabled={!editable}
            />
            <span className="text-sm text-faint">%</span>
          </div>
        </label>
      </div>

      <p className="px-3 py-2 text-xs leading-relaxed text-faint">
        On hit: closes every leg with a limit order priced off LTP by the
        offset above — Buy legs at a premium, Sell legs at a discount — so the
        order is priced to fill rather than a raw market order. Re-evaluated
        on the same clock as the P&amp;L engine (Settings &rsaquo; Advanced).
      </p>

      {error ? <p className="text-sm text-down">{error}</p> : null}

      {showTelegramRegisterMessage ? (
        <p className="text-center text-sm text-foreground">
          <Link
            href="/settings?tab=telegram-alerts"
            className="app-link"
            onClick={onClose}
          >
            Register
          </Link>{" "}
          to receive trigger alerts on Telegram
        </p>
      ) : null}

      {rearmBlocked ? (
        <>
          <button
            type="button"
            // Accent-primary, not danger-red: in the contra case this is the *recommended*
            // safe action. Colouring it red would read as "this is the dangerous thing to
            // click" — the red is spent on the warning above, where the hazard actually is.
            className={sb.btnPrimary}
            disabled={cancelling}
            onClick={handleCancelOrphans}
          >
            {cancelling ? "Cancelling…" : "Cancel remaining exit orders"}
          </button>
          <p className="text-center text-hint leading-relaxed text-faint">
            This can&rsquo;t be dismissed or re-armed while an exit order is still live.
          </p>
        </>
      ) : null}

      <div
        className={`${sb.modalActions} grid grid-cols-1 gap-2 sm:gap-3 ${
          existingRule && editable ? "sm:grid-cols-3" : "sm:grid-cols-2"
        }`}
      >
        <button type="button" className={sb.btnSecondary} onClick={onClose}>
          Cancel
        </button>
        {existingRule ? (
          <button
            type="button"
            className={sb.btnDanger}
            // Dismissing a Reset while its orders are still working would erase the hazard
            // from the UI while the orders keep going — one of them may open a contra
            // position. The UI must not be able to lie about live risk.
            disabled={disarming || rearmBlocked}
            onClick={handleDisarm}
          >
            {disarming ? "Dismissing…" : isReset ? "Dismiss" : "Disarm"}
          </button>
        ) : null}
        {editable ? (
          <button
            type="button"
            className={sb.btnPrimary}
            disabled={!canSubmit || submitting}
            onClick={handleSubmit}
          >
            {submitting
              ? existingRule && !isReset
                ? "Updating…"
                : "Arming…"
              : existingRule && !isReset
                ? "Update rule"
                : "Arm rule"}
          </button>
        ) : null}
      </div>
    </Modal>
  );
}
