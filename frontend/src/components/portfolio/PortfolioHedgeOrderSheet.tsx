"use client";

import { useCallback, useEffect, useState } from "react";
import { ltpAsOrderPrice } from "@/lib/order-confirm";
import type { PortfolioPositionRecord } from "@/lib/portfolio";

function parseNum(v: unknown): number {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string") {
    const n = parseFloat(v.replace(/,/g, ""));
    return Number.isFinite(n) ? n : NaN;
  }
  return NaN;
}

function formatSheetTitle(
  stockCode: string,
  expiryDisplay: string,
  strike: number,
): string {
  const datePart = expiryDisplay
    .trim()
    .replace(/\s+/g, "-")
    .replace(/,/g, "")
    .toUpperCase();
  const strikePart = String(Math.round(strike));
  return `${stockCode}-${datePart}-${strikePart}`;
}

function normRight(raw: unknown): "Call" | "Put" {
  const s = String(raw ?? "").toLowerCase();
  return /^p/.test(s) ? "Put" : "Call";
}

type PortfolioHedgeOrderSheetProps = {
  row: PortfolioPositionRecord;
  hedgeOption: Record<string, unknown>;
  onClose: () => void;
  onBuy: (args: {
    strike: number;
    right: "Call" | "Put";
    quantity: number;
    price: string;
  }) => void;
};

export function PortfolioHedgeOrderSheet({
  row,
  hedgeOption,
  onClose,
  onBuy,
}: PortfolioHedgeOrderSheetProps) {
  const stockCode = String(row.stock_code ?? "").trim();
  const expiryDisplay = String(row.expiry_date ?? "").trim();
  const strike = parseNum(hedgeOption.strike_price);
  const right = normRight(row.right);
  const offer = parseNum(hedgeOption.best_offer_price);
  const ltp = parseNum(hedgeOption.ltp);
  const hq = parseNum(hedgeOption.hedge_quantity);
  const initialPrice =
    offer > 0 ? String(Number(offer.toFixed(4))) : ltpAsOrderPrice(ltp);

  const [sheetQty, setSheetQty] = useState(
    Number.isFinite(hq) && hq > 0 ? String(Math.round(hq)) : "",
  );
  const [sheetPrice, setSheetPrice] = useState(initialPrice);
  const [sheetFormError, setSheetFormError] = useState<string | null>(null);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const submitBuy = useCallback(() => {
    const qn = parseInt(sheetQty.trim(), 10);
    if (!Number.isFinite(qn) || qn <= 0) {
      setSheetFormError("Enter a valid quantity (positive integer).");
      return;
    }
    if (!Number.isFinite(strike)) {
      setSheetFormError("Invalid strike.");
      return;
    }
    setSheetFormError(null);
    onBuy({
      strike,
      right,
      quantity: qn,
      price: sheetPrice.trim() || "0",
    });
  }, [sheetQty, sheetPrice, strike, right, onBuy]);

  const lotSize = parseNum(hedgeOption.lot_size);

  return (
    <>
      <div
        className="fixed inset-0 z-[105] bg-black/40 dark:bg-black/55"
        role="presentation"
        aria-hidden
        onClick={onClose}
      />
      <div
        className="fixed inset-x-4 bottom-0 z-[106] mx-auto flex max-h-[min(85dvh,28rem)] w-full max-w-[17.5rem] flex-col overflow-y-auto rounded-t-[1.25rem] border border-zinc-200/90 bg-white/95 px-3.5 pb-[max(0.875rem,env(safe-area-inset-bottom))] pt-3 shadow-[0_12px_48px_-8px_rgba(0,0,0,0.28)] backdrop-blur-xl dark:border-zinc-700/90 dark:bg-zinc-950/95 dark:shadow-[0_12px_48px_-8px_rgba(0,0,0,0.55)] sm:px-4 sm:pt-3.5 lg:inset-x-auto lg:bottom-auto lg:left-1/2 lg:top-1/2 lg:max-h-[min(85dvh,30rem)] lg:-translate-x-1/2 lg:-translate-y-1/2 lg:rounded-lg lg:px-4 lg:pb-4 lg:pt-4 lg:ring-1 lg:ring-zinc-950/[0.06] lg:dark:ring-white/[0.08]"
        role="dialog"
        aria-modal="true"
        aria-labelledby="portfolio-hedge-sheet-title"
      >
        <div className="mx-auto mb-2.5 h-1 w-9 shrink-0 rounded-full bg-zinc-300/90 dark:bg-zinc-600 lg:hidden" />
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0 flex-1">
            <h2
              id="portfolio-hedge-sheet-title"
              className="break-words text-[0.8125rem] font-semibold leading-snug tracking-tight text-zinc-900 tabular-nums dark:text-zinc-50"
            >
              {Number.isFinite(strike)
                ? formatSheetTitle(stockCode, expiryDisplay, strike)
                : "Hedge order"}
            </h2>
            <p className="mt-1.5 text-sm text-zinc-500 dark:text-zinc-400">
              <span className="font-medium text-zinc-700 dark:text-zinc-300">
                {right}
              </span>
              <span className="mx-1.5 text-zinc-300 dark:text-zinc-600" aria-hidden>
                ·
              </span>
              <span className="text-zinc-500 dark:text-zinc-500">Buy to hedge</span>
              {Number.isFinite(lotSize) && lotSize > 0 ? (
                <>
                  <span
                    className="mx-1.5 text-zinc-300 dark:text-zinc-600"
                    aria-hidden
                  >
                    ·
                  </span>
                  <span className="text-zinc-500 dark:text-zinc-500">
                    Lot {Math.round(lotSize).toLocaleString("en-IN")}
                  </span>
                </>
              ) : null}
            </p>
          </div>
          <button
            type="button"
            className="-m-0.5 shrink-0 rounded-full p-1.5 text-zinc-400 transition hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
            onClick={onClose}
            aria-label="Close"
          >
            <span className="block text-lg leading-none" aria-hidden>
              ×
            </span>
          </button>
        </div>

        <div className="mt-3 w-full space-y-2.5">
          <div className="w-full min-w-0">
            <label
              htmlFor="portfolio-hedge-qty"
              className="block text-[0.625rem] font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-500"
            >
              Quantity
            </label>
            <input
              id="portfolio-hedge-qty"
              type="number"
              min={1}
              inputMode="numeric"
              className="mt-1 block h-8 w-full min-w-0 rounded-lg border border-zinc-200/90 bg-zinc-50/80 px-2.5 py-0 text-sm tabular-nums text-zinc-900 shadow-inner shadow-zinc-900/5 transition placeholder:text-zinc-400 focus:border-sky-500/60 focus:bg-white focus:outline-none focus:ring-2 focus:ring-sky-500/25 dark:border-zinc-700 dark:bg-zinc-900/60 dark:text-zinc-100 dark:shadow-none dark:placeholder:text-zinc-600 dark:focus:border-sky-500/50 dark:focus:bg-zinc-950"
              value={sheetQty}
              onChange={(e) => setSheetQty(e.target.value)}
            />
          </div>
          <div className="w-full min-w-0">
            <label
              htmlFor="portfolio-hedge-price"
              className="block text-[0.625rem] font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-500"
            >
              Price (₹)
            </label>
            <input
              id="portfolio-hedge-price"
              type="number"
              step={0.05}
              inputMode="decimal"
              className="mt-1 block h-8 w-full min-w-0 rounded-lg border border-zinc-200/90 bg-zinc-50/80 px-2.5 py-0 text-sm tabular-nums text-zinc-900 shadow-inner shadow-zinc-900/5 transition placeholder:text-zinc-400 focus:border-sky-500/60 focus:bg-white focus:outline-none focus:ring-2 focus:ring-sky-500/25 dark:border-zinc-700 dark:bg-zinc-900/60 dark:text-zinc-100 dark:shadow-none dark:placeholder:text-zinc-600 dark:focus:border-sky-500/50 dark:focus:bg-zinc-950"
              value={sheetPrice}
              onChange={(e) => setSheetPrice(e.target.value)}
            />
          </div>
        </div>

        {sheetFormError ? (
          <p className="mt-2 text-[0.6875rem] leading-snug text-red-600 dark:text-red-400">
            {sheetFormError}
          </p>
        ) : null}

        <div className="mt-4 flex gap-2 border-t border-zinc-200/90 pt-3.5 dark:border-zinc-700/80">
          <button
            type="button"
            className="inline-flex h-9 min-w-0 flex-1 items-center justify-center rounded-md border border-emerald-600/95 bg-gradient-to-b from-emerald-500 to-emerald-600 px-2 text-xs font-semibold text-white shadow-sm shadow-emerald-900/20 transition hover:from-emerald-400 hover:to-emerald-500 focus:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400/40 disabled:opacity-50 dark:to-emerald-600"
            onClick={submitBuy}
          >
            Buy
          </button>
        </div>
      </div>
    </>
  );
}
