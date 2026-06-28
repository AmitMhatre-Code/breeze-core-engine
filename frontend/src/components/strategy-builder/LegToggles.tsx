"use client";

import type { OptionRight, OrderSide } from "@/lib/strategy-builder/types";

const GREEN_TOGGLE =
  "rounded px-2.5 py-1 text-[11px] font-semibold bg-emerald-100 text-emerald-900 transition hover:bg-emerald-200/80 dark:bg-emerald-950/50 dark:text-emerald-300 dark:hover:bg-emerald-950/70";
const RED_TOGGLE =
  "rounded px-2.5 py-1 text-[11px] font-semibold bg-red-100 text-red-900 transition hover:bg-red-200/80 dark:bg-red-950/50 dark:text-red-300 dark:hover:bg-red-950/70";

export function LegRightToggle({
  value,
  onChange,
}: {
  value: OptionRight;
  onChange: (right: OptionRight) => void;
}) {
  const isCall = value === "Call";
  return (
    <button
      type="button"
      aria-pressed
      aria-label={
        isCall
          ? "Call (CE). Click to switch to Put."
          : "Put (PE). Click to switch to Call."
      }
      onClick={() => onChange(isCall ? "Put" : "Call")}
      className={isCall ? GREEN_TOGGLE : RED_TOGGLE}
    >
      {isCall ? "CE" : "PE"}
    </button>
  );
}

export function LegSideToggle({
  value,
  onChange,
}: {
  value: OrderSide;
  onChange: (side: OrderSide) => void;
}) {
  const isBuy = value === "Buy";
  return (
    <button
      type="button"
      aria-pressed
      aria-label={
        isBuy
          ? "Buy. Click to switch to Sell."
          : "Sell. Click to switch to Buy."
      }
      onClick={() => onChange(isBuy ? "Sell" : "Buy")}
      className={isBuy ? GREEN_TOGGLE : RED_TOGGLE}
    >
      {isBuy ? "Buy" : "Sell"}
    </button>
  );
}
