"use client";

import { useEffect, useState, type ChangeEvent } from "react";
import { Modal } from "@/components/ui/Modal";
import { TelegramNudgeBanner } from "@/components/telegram/TelegramNudgeBanner";
import { sb } from "@/lib/strategy-builder/ui";
import { formatSignedRupees } from "@/lib/portfolio/totals";
import {
  armSquareOffRule,
  disarmSquareOffRule,
  type SquareOffRuleRecord,
} from "@/lib/portfolio/squareoff-rules";

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
  triggered: { label: "Triggered", className: "bg-accent-strong text-accent-ink" },
  fired: { label: "Fired", className: "bg-accent-strong text-accent-ink" },
  fire_failed: { label: "Fire failed", className: "bg-down-btn text-white" },
  disarmed: { label: "Disarmed", className: "bg-down-btn text-white" },
};

/**
 * Group-level profit/loss exit rule — mirrors `SquareOffLegsModal`'s shape
 * (a focused confirm modal) rather than an inline panel, so an armed group
 * costs nothing on the row until it's reopened or fires.
 *
 * When a rule already exists for this group, the modal is view-only: the
 * target/stop values can't be edited here (disarm then re-arm fresh instead),
 * and the primary button becomes "Disarm".
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
  const target = Number(profitTarget.replace(/,/g, ""));
  const stop = Number(lossLimit.replace(/,/g, ""));
  const canSubmit =
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
            className="text-base font-semibold text-foreground"
          >
            Set P&amp;L Profit Booking / Stop Loss
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

      <TelegramNudgeBanner />

      <div className="flex items-center justify-between rounded-md border border-border-soft bg-panel2 px-3.5 py-2.5 text-sm">
        <span className="text-muted">Current group P&amp;L</span>
        <span className={`font-mono font-semibold tabular-nums ${pnl.className}`}>
          {pnl.text}
        </span>
      </div>

      {status ? (
        <div className="flex items-center justify-between rounded-md border border-border-soft bg-panel2 px-3.5 py-2.5 text-sm">
          <span className="text-muted">Rule status</span>
          <span
            className={`rounded-full px-2 py-0.5 font-mono text-[11px] font-semibold ${status.className}`}
          >
            {status.label}
          </span>
        </div>
      ) : null}

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
              disabled={!!existingRule}
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
              disabled={!!existingRule}
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
              disabled={!!existingRule}
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
              disabled={!!existingRule}
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

      <div className="grid grid-cols-1 gap-2 pt-1 sm:grid-cols-2 sm:gap-3">
        <button type="button" className={sb.btnSecondary} onClick={onClose}>
          Cancel
        </button>
        {existingRule ? (
          <button
            type="button"
            className={sb.btnDanger}
            disabled={disarming}
            onClick={handleDisarm}
          >
            {disarming ? "Disarming…" : "Disarm"}
          </button>
        ) : (
          <button
            type="button"
            className={sb.btnPrimary}
            disabled={!canSubmit || submitting}
            onClick={handleSubmit}
          >
            {submitting ? "Arming…" : "Arm rule"}
          </button>
        )}
      </div>
    </Modal>
  );
}
