"use client";

import type { OrderSide } from "@/lib/strategy-builder/types";

export function LegPositionChip({ side }: { side: OrderSide }) {
  const buy = side === "Buy";
  return (
    <span
      className={
        buy
          ? "inline-flex shrink-0 rounded-full border border-up/40 bg-up-tint px-2 py-0.5 text-sm font-semibold text-up"
          : "inline-flex shrink-0 rounded-full border border-down/40 bg-down-tint px-2 py-0.5 text-sm font-semibold text-down"
      }
    >
      {side}
    </span>
  );
}
