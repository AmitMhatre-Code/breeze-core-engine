"use client";

import { useSearchParams } from "next/navigation";
import { useMemo } from "react";
import { useOrderConfirm } from "@/components/order/OrderConfirmProvider";
import { prefillFromOrdersSearchParams } from "@/lib/order-confirm";

/**
 * When the URL carries legacy-style order query params (including `SquareOff` + `position`),
 * show the same confirm entry point as legacy `order.html` / strategy redirects.
 */
export function PrefilledOrderCard() {
  const sp = useSearchParams();
  const payload = useMemo(() => prefillFromOrdersSearchParams(sp), [sp]);
  const { openOrderConfirm } = useOrderConfirm();

  if (!payload) return null;

  const summary = `${payload.action} ${payload.quantity} × ${payload.stock_code} ${payload.expiry_date} ${payload.right} ${payload.strike_price}`;

  return (
    <div className="app-card-muted mb-4 rounded-xl border border-zinc-200 p-4 dark:border-zinc-800">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
        Order from link
      </h3>
      <p className="mt-2 text-sm text-zinc-800 dark:text-zinc-200">{summary}</p>
      <button
        type="button"
        className={
          payload.action === "Sell"
            ? "mt-3 inline-flex w-full items-center justify-center rounded-lg bg-red-600 px-3 py-2 text-sm font-medium text-white hover:bg-red-700 sm:w-auto dark:bg-red-600 dark:hover:bg-red-500"
            : "mt-3 inline-flex w-full items-center justify-center rounded-lg bg-emerald-600 px-3 py-2 text-sm font-medium text-white hover:bg-emerald-700 sm:w-auto dark:bg-emerald-600 dark:hover:bg-emerald-500"
        }
        onClick={() => openOrderConfirm(payload)}
      >
        Confirm &amp; place order
      </button>
    </div>
  );
}
