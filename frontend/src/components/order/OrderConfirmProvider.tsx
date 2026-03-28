"use client";

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { apiClient } from "@/lib/api-client";
import type { OrderConfirmPayload } from "@/lib/order-confirm";

type OrderConfirmContextValue = {
  openOrderConfirm: (payload: OrderConfirmPayload) => void;
};

const OrderConfirmContext = createContext<OrderConfirmContextValue | null>(
  null,
);

export function useOrderConfirm(): OrderConfirmContextValue {
  const ctx = useContext(OrderConfirmContext);
  if (!ctx) {
    throw new Error("useOrderConfirm must be used within OrderConfirmProvider");
  }
  return ctx;
}

type ModalState =
  | { open: false; base: null }
  | { open: true; base: OrderConfirmPayload };

export function OrderConfirmProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [state, setState] = useState<ModalState>({ open: false, base: null });
  const [qty, setQty] = useState("");
  const [price, setPrice] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const openOrderConfirm = useCallback((payload: OrderConfirmPayload) => {
    setError(null);
    setQty(payload.quantity ?? "");
    setPrice(payload.price ?? "0");
    setState({ open: true, base: payload });
  }, []);

  const close = useCallback(() => {
    if (submitting) return;
    setState({ open: false, base: null });
    setError(null);
  }, [submitting]);

  const value = useMemo(
    () => ({ openOrderConfirm }),
    [openOrderConfirm],
  );

  const base = state.open ? state.base : null;

  async function onConfirm() {
    if (!base) return;
    const qn = parseInt(qty.trim(), 10);
    if (!Number.isFinite(qn) || qn <= 0) {
      setError("Enter a valid quantity (positive integer).");
      return;
    }
    setError(null);
    setSubmitting(true);
    try {
      const idem =
        typeof crypto !== "undefined" && crypto.randomUUID
          ? crypto.randomUUID()
          : undefined;
      const res = await apiClient.post<{ redirect: string }>(
        "/order",
        {
          product_type: base.product_type || "Options",
          stock_code: base.stock_code,
          exchange_code: base.exchange_code || "NFO",
          expiry_date: base.expiry_date,
          right: base.right,
          strike_price: base.strike_price,
          quantity: String(qn),
          price: (price.trim() || "0") as string,
          action: base.action,
        },
        idem ? { headers: { "Idempotency-Key": idem } } : undefined,
      );
      setState({ open: false, base: null });
      void queryClient.invalidateQueries({ queryKey: ["orders", "list"] });
      void queryClient.invalidateQueries({ queryKey: ["book"] });
      const dest = res.redirect || "/orders";
      router.push(dest);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Order failed");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <OrderConfirmContext.Provider value={value}>
      {children}
      {base ? (
        <div
          className="fixed inset-0 z-[100] flex items-center justify-center bg-black/40 p-4 dark:bg-black/60"
          role="presentation"
          onClick={close}
          onKeyDown={(e) => {
            if (e.key === "Escape") close();
          }}
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="order-confirm-title"
            className="max-h-[90vh] w-full max-w-md overflow-y-auto rounded-lg border border-zinc-200 bg-white p-5 shadow-lg dark:border-zinc-800 dark:bg-zinc-900"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-start justify-between gap-2">
              <h2
                id="order-confirm-title"
                className="text-base font-semibold text-zinc-900 dark:text-zinc-100"
              >
                Confirm order
              </h2>
              <button
                type="button"
                className="rounded-lg p-1 text-zinc-500 hover:bg-zinc-100 hover:text-zinc-800 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
                onClick={close}
                disabled={submitting}
                aria-label="Close"
              >
                ×
              </button>
            </div>
            <p className="mt-3 text-sm leading-relaxed text-zinc-800 dark:text-zinc-200">
              <span
                className={
                  base.action === "Sell"
                    ? "mr-1 inline-block rounded px-1.5 py-0.5 text-xs font-semibold text-white bg-red-600"
                    : "mr-1 inline-block rounded px-1.5 py-0.5 text-xs font-semibold text-white bg-emerald-600"
                }
              >
                {base.action}
              </span>
              {base.stock_code} {base.expiry_date} {base.right}{" "}
              {base.strike_price}
            </p>
            <div className="mt-4 grid grid-cols-2 gap-3">
              <div>
                <label
                  htmlFor="order-confirm-qty"
                  className="block text-xs font-medium text-zinc-500 dark:text-zinc-400"
                >
                  Quantity
                </label>
                <input
                  id="order-confirm-qty"
                  type="number"
                  min={1}
                  className="mt-1 w-full rounded-lg border border-zinc-200 bg-white px-2 py-1.5 text-sm text-zinc-900 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
                  value={qty}
                  onChange={(e) => setQty(e.target.value)}
                  disabled={submitting}
                />
              </div>
              <div>
                <label
                  htmlFor="order-confirm-price"
                  className="block text-xs font-medium text-zinc-500 dark:text-zinc-400"
                >
                  Price (₹)
                </label>
                <input
                  id="order-confirm-price"
                  type="number"
                  step={0.05}
                  className="mt-1 w-full rounded-lg border border-zinc-200 bg-white px-2 py-1.5 text-sm text-zinc-900 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
                  value={price}
                  onChange={(e) => setPrice(e.target.value)}
                  disabled={submitting}
                />
              </div>
            </div>
            {error ? (
              <p className="mt-3 text-xs text-red-600 dark:text-red-400">
                {error}
              </p>
            ) : null}
            <div className="mt-5 flex justify-end gap-2">
              <button
                type="button"
                className="app-btn-secondary"
                onClick={close}
                disabled={submitting}
              >
                Cancel
              </button>
              <button
                type="button"
                className="app-btn-primary px-3 py-1.5 text-xs font-medium disabled:opacity-60"
                onClick={() => void onConfirm()}
                disabled={submitting}
              >
                {submitting ? "Placing…" : "Confirm order"}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </OrderConfirmContext.Provider>
  );
}
