"use client";

import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Modal } from "@/components/ui/Modal";
import { sb } from "@/lib/strategy-builder/ui";
import {
  cancelLegGttExitOrder,
  fetchLegGttStatus,
  placeLegGttExitOrder,
  type LegIdentity,
} from "@/lib/portfolio/gtt-exit-orders";

export type LegExitRuleTarget = LegIdentity & {
  quantity: string;
  product_type?: string;
  /** Position's own BUY/SELL side — the GTT legs close the opposite side. */
  action: string;
};

function gttQueryKey(leg: LegIdentity) {
  return [
    "portfolio",
    "gtt-exit-order",
    leg.stock_code,
    leg.exchange_code,
    leg.expiry_date,
    leg.strike_price,
    leg.right,
  ] as const;
}

function PriceField({
  label,
  color,
  value,
  onChange,
  placeholder,
  disabled,
}: {
  label: string;
  color: string;
  value: string;
  onChange: (v: string) => void;
  placeholder: string;
  disabled: boolean;
}) {
  return (
    <label className="block">
      <span className={sb.fieldLabel} style={{ color }}>
        {label}
      </span>
      <div className="flex items-center gap-1.5">
        <span className="text-sm text-faint">₹</span>
        <input
          type="text"
          inputMode="decimal"
          className={sb.input}
          value={value}
          onChange={(e) => onChange(e.target.value.replace(/[^0-9.]/g, ""))}
          placeholder={placeholder}
          disabled={disabled}
        />
      </div>
    </label>
  );
}

/**
 * Individual-leg Exit Rule — unlike `SquareOffRuleModal` (a P&L-target rule this
 * app polls for a whole scrip+expiry group), this places a broker-monitored GTT
 * OCO bracket for exactly one leg: ICICI itself watches the trigger prices and
 * fires the matching limit order, so there's no local polling for this path.
 */
export function LegExitRuleModal({
  open,
  onClose,
  leg,
  label,
}: {
  open: boolean;
  onClose: () => void;
  leg: LegExitRuleTarget;
  label: string;
}) {
  const queryClient = useQueryClient();
  const statusQuery = useQuery({
    queryKey: gttQueryKey(leg),
    queryFn: () => fetchLegGttStatus(leg),
    enabled: open,
  });
  const existingOrder = statusQuery.data ?? null;

  const [targetTrigger, setTargetTrigger] = useState("");
  const [targetLimit, setTargetLimit] = useState("");
  const [stopTrigger, setStopTrigger] = useState("");
  const [stopLimit, setStopLimit] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setTargetTrigger("");
    setTargetLimit("");
    setStopTrigger("");
    setStopLimit("");
    setError(null);
  }, [open]);

  const nums = [targetTrigger, targetLimit, stopTrigger, stopLimit].map(Number);
  const canSubmit = nums.every((n) => Number.isFinite(n) && n > 0);

  const handleSubmit = async () => {
    if (!canSubmit || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      await placeLegGttExitOrder({
        stock_code: leg.stock_code,
        exchange_code: leg.exchange_code,
        expiry_date: leg.expiry_date,
        strike_price: leg.strike_price,
        right: leg.right,
        quantity: leg.quantity,
        product_type: leg.product_type,
        action: leg.action,
        target_trigger_price: nums[0],
        target_limit_price: nums[1],
        stop_trigger_price: nums[2],
        stop_limit_price: nums[3],
      });
      await queryClient.invalidateQueries({ queryKey: gttQueryKey(leg) });
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to place GTT exit order");
    } finally {
      setSubmitting(false);
    }
  };

  const handleCancel = async () => {
    if (!existingOrder || cancelling) return;
    setCancelling(true);
    setError(null);
    try {
      await cancelLegGttExitOrder(existingOrder.gtt_order_id, leg.exchange_code);
      await queryClient.invalidateQueries({ queryKey: gttQueryKey(leg) });
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to cancel GTT exit order");
    } finally {
      setCancelling(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      titleId="leg-exit-rule-title"
      zIndexClass="z-[110]"
      panelClassName={`${sb.modalPanel} !w-max max-w-[min(96vw,26rem)] mx-auto`}
      pending={submitting || cancelling}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <h3 id="leg-exit-rule-title" className="text-base font-semibold text-foreground">
            Profit Booking / Stop Loss (this leg)
          </h3>
          <p className="mt-1 text-sm leading-relaxed text-muted">{label}</p>
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

      {existingOrder ? (
        <div className="space-y-2 rounded-md border border-border-soft bg-panel2 px-3.5 py-2.5 text-sm">
          <div className="flex items-center justify-between">
            <span className="text-muted">GTT order</span>
            <span className="font-mono text-xs text-faint">{existingOrder.gtt_order_id}</span>
          </div>
          {existingOrder.legs.map((l, i) => (
            <div key={i} className="flex items-center justify-between gap-3">
              <span className="shrink-0 text-muted">{l.gtt_leg_type ?? "Leg"}</span>
              <span className="font-mono tabular-nums">
                trig ₹{l.trigger_price ?? "—"} / limit ₹{l.limit_price ?? "—"}
                {l.status ? ` · ${l.status}` : ""}
              </span>
            </div>
          ))}
        </div>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-4">
            <PriceField
              label="Target trigger"
              color="var(--up)"
              value={targetTrigger}
              onChange={setTargetTrigger}
              placeholder="14.50"
              disabled={submitting}
            />
            <PriceField
              label="Target limit"
              color="var(--up)"
              value={targetLimit}
              onChange={setTargetLimit}
              placeholder="15"
              disabled={submitting}
            />
            <PriceField
              label="Stoploss trigger"
              color="var(--down)"
              value={stopTrigger}
              onChange={setStopTrigger}
              placeholder="7.50"
              disabled={submitting}
            />
            <PriceField
              label="Stoploss limit"
              color="var(--down)"
              value={stopLimit}
              onChange={setStopLimit}
              placeholder="7"
              disabled={submitting}
            />
          </div>
          <p className="px-3 py-2 text-xs leading-relaxed text-faint">
            Places a GTT (Good Till Trigger) OCO bracket directly with ICICI for this
            one leg — the broker itself watches these trigger prices and fires the
            matching limit order when hit; whichever side fires cancels the other.
            Unlike the grouped Profit Booking / Stop Loss rule, this app does not poll P&amp;L for it.
          </p>
        </>
      )}

      {error ? <p className="text-sm text-down">{error}</p> : null}

      <div className="grid grid-cols-1 gap-2 pt-1 sm:grid-cols-2 sm:gap-3">
        <button type="button" className={sb.btnSecondary} onClick={onClose}>
          Cancel
        </button>
        {existingOrder ? (
          <button
            type="button"
            className={sb.btnDanger}
            disabled={cancelling}
            onClick={handleCancel}
          >
            {cancelling ? "Cancelling…" : "Cancel GTT order"}
          </button>
        ) : (
          <button
            type="button"
            className={sb.btnPrimary}
            disabled={!canSubmit || submitting}
            onClick={handleSubmit}
          >
            {submitting ? "Placing…" : "Place GTT order"}
          </button>
        )}
      </div>
    </Modal>
  );
}
