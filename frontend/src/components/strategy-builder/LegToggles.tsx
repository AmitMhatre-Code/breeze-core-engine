"use client";

import type { OptionRight, OrderSide } from "@/lib/strategy-builder/types";

const GREEN_TOGGLE =
  "rounded px-2.5 py-1 text-[13px] font-bold uppercase tracking-[.05em] bg-up-tint text-up transition hover:brightness-[1.08]";
const RED_TOGGLE =
  "rounded px-2.5 py-1 text-[13px] font-bold uppercase tracking-[.05em] bg-down-tint text-down transition hover:brightness-[1.08]";

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
      aria-pressed={isCall}
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
      aria-pressed={isBuy}
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
