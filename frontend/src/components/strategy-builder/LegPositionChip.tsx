"use client";

import type { OrderSide } from "@/lib/strategy-builder/types";

export function LegPositionChip({ side }: { side: OrderSide }) {
  const buy = side === "Buy";
  return (
    <span
      className={
        buy
          ? "inline-flex shrink-0 rounded-full border border-emerald-600/80 bg-emerald-600/15 px-2 py-0.5 text-sm font-semibold text-emerald-800 dark:border-emerald-500/70 dark:bg-emerald-500/15 dark:text-emerald-200"
          : "inline-flex shrink-0 rounded-full border border-red-600/80 bg-red-600/15 px-2 py-0.5 text-sm font-semibold text-red-800 dark:border-red-500/70 dark:bg-red-500/15 dark:text-red-200"
      }
    >
      {side}
    </span>
  );
}
