"use client";

import { useSearchParams } from "next/navigation";
import { useMemo } from "react";
import { useOrderConfirm } from "@/components/shared/order/OrderConfirmProvider";
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
    <div className="app-card-muted mb-4 p-4">
      <h3 className="text-hint font-bold uppercase tracking-[.06em] text-faint">
        Order from link
      </h3>
      <p className="mt-2 text-sm text-foreground">{summary}</p>
      <button
        type="button"
        className={
          payload.action === "Sell"
            ? "app-btn-danger mt-3 h-10 min-h-10 w-full sm:w-auto"
            : "app-btn-primary mt-3 h-10 min-h-10 w-full sm:w-auto"
        }
        onClick={() => openOrderConfirm(payload)}
      >
        Confirm &amp; place order
      </button>
    </div>
  );
}
