"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useOrderConfirm } from "@/components/order/OrderConfirmProvider";
import { AsyncLabelSpan } from "@/components/ui/AsyncLabelSpan";
import { Modal } from "@/components/ui/Modal";
import { ExpirySelectPill } from "@/components/strategy-builder/ExpirySelectPill";
import { OptionChainUnderlyingSearch } from "@/components/order/OptionChainUnderlyingSearch";
import { apiClient } from "@/lib/api-client";
import { formatIndianMoneyCompact } from "@/lib/format-money-in";
import { ltpAsOrderPrice } from "@/lib/order-confirm";
import { fetchStrategyBuilderChain } from "@/lib/strategy-builder/api";
import { quoteMetaFromChain } from "@/lib/quote-source";
import { sortExpiryDatesAsc } from "@/lib/strategy-builder/expiry";
import { useWsSubscriptionHolder } from "@/lib/use-ws-subscription-holder";
import {
  chainLotSize,
  legLotSize,
  OptionChainTable,
  scrollOptionChainAtmIntoView,
} from "@/components/order/OptionChainTable";
import type {
  ChainApiResponse,
  ChainRow,
  ChainSuccess,
  MarginApiResponse,
  UnderlyingsApiResponse,
} from "@/lib/strategy-builder/types";

function parseNum(v: unknown): number {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string") {
    const n = parseFloat(v.replace(/,/g, ""));
    return Number.isFinite(n) ? n : NaN;
  }
  return NaN;
}

/** e.g. `NIFTY-24-MAR-2026-25000` */
function formatOptionChainSheetTitle(
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

function formatChainMetaInt(n: unknown): string {
  if (n == null) return "—";
  const v = typeof n === "number" ? n : parseNum(n);
  if (!Number.isFinite(v)) return "—";
  return Math.round(v).toLocaleString("en-IN");
}

function spanFromMarginResponse(res: MarginApiResponse): number | null {
  if (res.Status !== 200 || !res.Success) return null;
  const raw = (res.Success as { span_margin_required?: unknown })
    .span_margin_required;
  const v = parseNum(raw);
  return Number.isFinite(v) ? v : null;
}

type SheetState = { strike: number; row: ChainRow };

export function OptionChainPlaceSection() {
  const subscriptionHolder = useWsSubscriptionHolder();
  const { openOrderConfirm } = useOrderConfirm();
  const [segmentExchange, setSegmentExchange] = useState<"NFO" | "BFO">("NFO");
  const [stockCode, setStockCode] = useState("");
  const [expiryDate, setExpiryDate] = useState("");
  const [chainSuccess, setChainSuccess] = useState<ChainSuccess | null>(null);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  const [sheet, setSheet] = useState<SheetState | null>(null);
  const [sheetRight, setSheetRight] = useState<"Call" | "Put">("Call");
  const [sheetQty, setSheetQty] = useState("");
  const [sheetPrice, setSheetPrice] = useState("");
  const [sheetFormError, setSheetFormError] = useState<string | null>(null);

  const onSegmentChange = useCallback((ex: "NFO" | "BFO") => {
    if (ex === segmentExchange) return;
    setSegmentExchange(ex);
    setStockCode("");
    setExpiryDate("");
    setChainSuccess(null);
    setFetchError(null);
    setSheet(null);
  }, [segmentExchange]);

  const uq = useQuery({
    queryKey: ["orders", "option-chain", "underlyings", segmentExchange],
    queryFn: () =>
      apiClient.get<UnderlyingsApiResponse>(
        `/strategy-builder/underlyings?exchange_code=${segmentExchange}`,
      ),
  });

  const expiryOptions = useMemo(() => {
    const raw = uq.data?.underlyings ?? [];
    const u = raw.find((x) => x.stock_code === stockCode);
    return sortExpiryDatesAsc(u?.expiry_dates ?? []);
  }, [uq.data?.underlyings, stockCode]);

  const chainMut = useMutation({
    mutationFn: async () =>
      fetchStrategyBuilderChain({
        stock_code: stockCode.trim(),
        exchange_code: segmentExchange,
        expiry_date: expiryDate.trim(),
        subscription_holder: subscriptionHolder,
      }),
    onSuccess: (data) => {
      if (data.Status === 200 && data.Success) {
        setChainSuccess(data.Success);
        setFetchError(null);
      } else {
        setChainSuccess(null);
        setFetchError(
          (data.Error && String(data.Error).trim()) ||
            "Unable to load option chain.",
        );
      }
    },
    onError: (e) => {
      setChainSuccess(null);
      setFetchError(
        e instanceof Error ? e.message : "Unable to load option chain.",
      );
    },
  });

  const defaultLot = useMemo(
    () => chainLotSize(chainSuccess?.chain_rows ?? []),
    [chainSuccess?.chain_rows],
  );

  const chainQuoteMeta = useMemo(
    () => quoteMetaFromChain(chainSuccess),
    [chainSuccess],
  );

  useEffect(() => {
    if (!chainSuccess) return;
    const t = requestAnimationFrame(() => {
      scrollOptionChainAtmIntoView(scrollRef.current);
    });
    return () => cancelAnimationFrame(t);
  }, [chainSuccess]);

  function onFetch() {
    if (!stockCode.trim() || !expiryDate.trim()) return;
    setSheet(null);
    chainMut.mutate();
  }

  const openSheetForRow = useCallback(
    (row: ChainRow, preferRight?: "Call" | "Put") => {
      const c = row.call ?? null;
      const p = row.put ?? null;
      if (!c && !p) return;
      let right: "Call" | "Put";
      if (preferRight === "Call" && c) right = "Call";
      else if (preferRight === "Put" && p) right = "Put";
      else right = c ? "Call" : "Put";
      const leg = right === "Call" ? c : p;
      setSheetRight(right);
      setSheetQty(String(legLotSize(leg, defaultLot)));
      setSheetPrice(ltpAsOrderPrice(leg?.ltp));
      setSheetFormError(null);
      setSheet({ strike: row.strike_price, row });
    },
    [defaultLot],
  );

  const closeSheet = useCallback(() => {
    setSheet(null);
    setSheetFormError(null);
  }, []);

  const setRightAndSyncFields = useCallback(
    (next: "Call" | "Put") => {
      if (!sheet) return;
      const leg =
        next === "Call" ? sheet.row.call ?? null : sheet.row.put ?? null;
      if (!leg) return;
      setSheetRight(next);
      setSheetQty(String(legLotSize(leg, defaultLot)));
      setSheetPrice(ltpAsOrderPrice(leg?.ltp));
      setSheetFormError(null);
    },
    [sheet, defaultLot],
  );

  const submitFromSheet = useCallback(
    (action: "Buy" | "Sell") => {
      if (!chainSuccess || !sheet) return;
      const leg =
        sheetRight === "Call"
          ? sheet.row.call ?? null
          : sheet.row.put ?? null;
      if (!leg) {
        setSheetFormError("This strike has no data for the selected side.");
        return;
      }
      const qn = parseInt(sheetQty.trim(), 10);
      if (!Number.isFinite(qn) || qn <= 0) {
        setSheetFormError("Enter a valid quantity (positive integer).");
        return;
      }
      setSheetFormError(null);
      closeSheet();
      openOrderConfirm(
        {
          product_type: "Options",
          stock_code: chainSuccess.stock_code,
          exchange_code: chainSuccess.exchange_code,
          expiry_date: chainSuccess.expiry_display,
          right: sheetRight,
          strike_price: String(sheet.strike),
          quantity: String(qn),
          price: (sheetPrice.trim() || "0") as string,
          action,
        },
        { quoteMeta: chainQuoteMeta },
      );
    },
    [
      chainSuccess,
      sheet,
      sheetRight,
      sheetQty,
      sheetPrice,
      closeSheet,
      openOrderConfirm,
      chainQuoteMeta,
    ],
  );

  const sheetHasCall = Boolean(sheet?.row.call);
  const sheetHasPut = Boolean(sheet?.row.put);

  const sheetLegForMargin = useMemo(() => {
    if (!sheet) return null;
    return sheetRight === "Call"
      ? (sheet.row.call ?? null)
      : (sheet.row.put ?? null);
  }, [sheet, sheetRight]);

  const sheetLotUnits = useMemo(() => {
    if (!sheetLegForMargin) return 0;
    return legLotSize(sheetLegForMargin, defaultLot);
  }, [sheetLegForMargin, defaultLot]);

  const marginPerLotQ = useQuery({
    queryKey: [
      "orders",
      "option-chain",
      "margin-per-lot",
      chainSuccess?.stock_code,
      chainSuccess?.exchange_code,
      chainSuccess?.expiry_display,
      sheet?.strike,
      sheetRight,
      sheetLotUnits,
    ],
    queryFn: async () => {
      const cs = chainSuccess!;
      const sh = sheet!;
      const legBody = {
        stock_code: cs.stock_code,
        exchange_code: cs.exchange_code,
        expiry_date: cs.expiry_display,
        product_type: "Options" as const,
        right: sheetRight,
        strike_price: String(sh.strike),
        quantity: String(sheetLotUnits),
        price: ltpAsOrderPrice(sheetLegForMargin?.ltp),
        action: "Sell" as const,
      };
      return apiClient.post<MarginApiResponse>("/strategy-builder/margin", {
        legs: [legBody],
      });
    },
    enabled: Boolean(
      sheet && chainSuccess && sheetLegForMargin && sheetLotUnits > 0,
    ),
    staleTime: 5000,
  });

  const marginPerLotDisplay = useMemo(() => {
    const q = marginPerLotQ;
    if (q.isPending) return "…";
    if (q.isError) return "—";
    const d = q.data;
    if (!d) return "—";
    const sell = spanFromMarginResponse(d);
    return sell != null ? formatIndianMoneyCompact(sell) : "—";
  }, [marginPerLotQ]);

  return (
    <section className="space-y-4" aria-label="Option chain">
      <div
        className="flex min-h-[2.75rem] flex-col overflow-visible rounded-md border border-zinc-200/90 bg-zinc-100 shadow-sm dark:border-transparent dark:bg-[var(--elevated)] dark:shadow-none sm:flex-row sm:items-center"
        role="toolbar"
        aria-label="Underlying, expiry, and fetch"
      >
        <div
          className="flex shrink-0 items-center border-b border-zinc-200 dark:border-zinc-700/70 px-2 py-2 sm:border-b-0 sm:border-r sm:border-zinc-200 sm:dark:border-zinc-700/70 sm:py-0 sm:ps-2.5 sm:pe-2"
          role="group"
          aria-label="Exchange segment"
        >
          <div className="inline-flex rounded-lg bg-zinc-200/70 p-0.5 ring-1 ring-zinc-300/70 dark:bg-black/30 dark:ring-zinc-700/70">
            <button
              type="button"
              onClick={() => onSegmentChange("NFO")}
              className={`rounded-md px-3 py-1.5 text-xs font-semibold transition focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-500/45 sm:text-sm ${
                segmentExchange === "NFO"
                  ? "bg-white text-zinc-900 shadow-sm dark:bg-zinc-700 dark:text-white"
                  : "text-zinc-600 hover:bg-zinc-300/50 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-800/80 dark:hover:text-zinc-200"
              }`}
            >
              NSE
            </button>
            <button
              type="button"
              onClick={() => onSegmentChange("BFO")}
              className={`rounded-md px-3 py-1.5 text-xs font-semibold transition focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-500/45 sm:text-sm ${
                segmentExchange === "BFO"
                  ? "bg-white text-zinc-900 shadow-sm dark:bg-zinc-700 dark:text-white"
                  : "text-zinc-600 hover:bg-zinc-300/50 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-800/80 dark:hover:text-zinc-200"
              }`}
            >
              BSE
            </button>
          </div>
        </div>

        <div className="relative z-30 flex min-w-0 max-w-[min(100%,26rem)] flex-1 items-center overflow-visible border-b border-zinc-200 dark:border-zinc-700/70 px-3 py-2 sm:border-b-0 sm:border-r sm:border-zinc-200 sm:dark:border-zinc-700/70 sm:py-2.5">
          <OptionChainUnderlyingSearch
            variant="ticker"
            chainBar
            underlyings={uq.data?.underlyings ?? []}
            value={stockCode}
            disabled={uq.isLoading}
            spot={chainSuccess?.spot_price ?? null}
            quoteMeta={chainQuoteMeta}
            onChange={(code) => {
              setStockCode(code);
              setExpiryDate("");
              setChainSuccess(null);
              setFetchError(null);
              setSheet(null);
            }}
          />
        </div>

        <div className="relative z-20 flex shrink-0 items-center overflow-visible border-b border-zinc-200 dark:border-zinc-700/70 px-3 py-2 sm:border-b-0 sm:border-r sm:border-zinc-200 sm:dark:border-zinc-700/70 sm:py-2.5">
          <ExpirySelectPill
            layout="toolbar"
            dates={expiryOptions}
            value={expiryDate}
            disabled={!stockCode}
            onChange={(d) => {
              setExpiryDate(d);
              setChainSuccess(null);
              setFetchError(null);
              setSheet(null);
            }}
          />
        </div>

        <div className="flex shrink-0 items-center px-3 py-2 sm:py-2.5 sm:pe-3.5">
          <button
            type="button"
            onClick={onFetch}
            disabled={
              uq.isLoading ||
              !stockCode.trim() ||
              !expiryDate.trim() ||
              chainMut.isPending
            }
            className="inline-flex w-full min-w-[9.25rem] items-center justify-center rounded-lg border border-sky-600/70 bg-transparent px-4 py-2 text-sm font-semibold text-sky-700 shadow-none transition hover:bg-sky-500/15 hover:text-sky-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-500/45 disabled:pointer-events-none disabled:cursor-not-allowed disabled:border-zinc-300 disabled:bg-zinc-100 disabled:text-zinc-400 dark:border-sky-500/85 dark:text-sky-400 dark:hover:bg-sky-500/10 dark:hover:text-sky-300 dark:disabled:border-zinc-600 dark:disabled:bg-zinc-800 dark:disabled:text-zinc-500 sm:w-auto"
            aria-busy={chainMut.isPending}
          >
            <AsyncLabelSpan
              busy={chainMut.isPending}
              idleLabel="Fetch"
              busyLabel="Fetching..."
            />
          </button>
        </div>
      </div>

      {fetchError ? (
        <div className="app-alert-error text-sm">{fetchError}</div>
      ) : null}

      {chainSuccess?.chain_rows?.length ? (
        <OptionChainTable
          chainSuccess={chainSuccess}
          scrollRef={scrollRef}
          mode="trade"
          onRowClick={openSheetForRow}
        />
      ) : null}

      {sheet && chainSuccess ? (
        <Modal
          open
          onClose={closeSheet}
          variant="bottomSheet"
          titleId="option-chain-sheet-title"
          zIndexClass="z-[106]"
          panelClassName="mx-auto flex max-h-[min(85dvh,28rem)] w-full max-w-[17.5rem] flex-col overflow-y-auto rounded-t-[1.25rem] border border-zinc-200/90 bg-white/95 px-3.5 pb-[max(0.875rem,env(safe-area-inset-bottom))] pt-3 shadow-[0_12px_48px_-8px_rgba(0,0,0,0.28)] backdrop-blur-xl dark:border-zinc-700/90 dark:bg-zinc-950/95 dark:shadow-[0_12px_48px_-8px_rgba(0,0,0,0.55)] sm:px-4 sm:pt-3.5 lg:max-h-[min(85dvh,30rem)] lg:max-w-lg lg:rounded-lg lg:px-4 lg:pb-4 lg:pt-4 lg:ring-1 lg:ring-zinc-950/[0.06] lg:dark:ring-white/[0.08]"
        >
          <div className="mx-auto mb-2.5 h-1 w-9 shrink-0 rounded-full bg-zinc-300/90 dark:bg-zinc-600 lg:hidden" />
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0 flex-1">
                <h2
                  id="option-chain-sheet-title"
                  className="break-words text-[0.8125rem] font-semibold leading-snug tracking-tight text-zinc-900 tabular-nums dark:text-zinc-50"
                >
                  {formatOptionChainSheetTitle(
                    chainSuccess.stock_code,
                    chainSuccess.expiry_display,
                    sheet.strike,
                  )}
                </h2>
                <p className="mt-1.5 leading-snug">
                  <span className="text-sm text-zinc-600 dark:text-zinc-400">
                    Lot:
                  </span>{" "}
                  <span className="text-sm tabular-nums text-zinc-600 dark:text-zinc-400">
                    {formatChainMetaInt(chainSuccess.lot_size)}
                  </span>
                  <span
                    className="mx-1.5 text-sm text-zinc-300 dark:text-zinc-700"
                    aria-hidden
                  >
                    ·
                  </span>
                  {/* <span className="text-sm text-zinc-600 dark:text-zinc-400">
                    Max Qty / Order:
                  </span>{" "}
                  <span className="text-sm tabular-nums text-zinc-600 dark:text-zinc-400">
                    {formatChainMetaInt(chainSuccess.freeze_quantity)}
                  </span> */}
                  <span className="text-sm text-zinc-600 dark:text-zinc-400">
                    Margin / Lot:
                  </span>{" "}
                  <span className="text-sm tabular-nums text-zinc-600 dark:text-zinc-400">
                    {marginPerLotDisplay}
                  </span>                  
                </p>
              </div>
              <button
                type="button"
                className="-m-0.5 shrink-0 rounded-full p-1.5 text-zinc-500 transition hover:bg-zinc-100 hover:text-zinc-700 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
                onClick={closeSheet}
                aria-label="Close"
              >
                <span className="block text-lg leading-none" aria-hidden>
                  ×
                </span>
              </button>
            </div>

            <div className="mt-3">
              <div
                className="flex w-full gap-0.5 rounded-full bg-zinc-100/95 p-0.5 ring-1 ring-zinc-200/80 dark:bg-zinc-900/90 dark:ring-zinc-700/80"
                role="group"
                aria-label="Call or Put"
              >
                <button
                  type="button"
                  disabled={!sheetHasCall}
                  onClick={() => setRightAndSyncFields("Call")}
                  className={`min-w-0 flex-1 rounded-full px-2.5 py-1.5 text-xs font-semibold tracking-wide transition focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-500/50 disabled:cursor-not-allowed disabled:text-zinc-300 disabled:ring-0 dark:disabled:text-zinc-600 ${
                    sheetRight === "Call"
                      ? "bg-white text-zinc-900 shadow-sm ring-1 ring-zinc-200/90 dark:bg-zinc-800 dark:text-zinc-50 dark:ring-zinc-600/90"
                      : "text-zinc-500 hover:text-zinc-800 dark:text-zinc-400 dark:hover:text-zinc-200"
                  }`}
                >
                  Call
                </button>
                <button
                  type="button"
                  disabled={!sheetHasPut}
                  onClick={() => setRightAndSyncFields("Put")}
                  className={`min-w-0 flex-1 rounded-full px-2.5 py-1.5 text-xs font-semibold tracking-wide transition focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-500/50 disabled:cursor-not-allowed disabled:text-zinc-300 disabled:ring-0 dark:disabled:text-zinc-600 ${
                    sheetRight === "Put"
                      ? "bg-white text-zinc-900 shadow-sm ring-1 ring-zinc-200/90 dark:bg-zinc-800 dark:text-zinc-50 dark:ring-zinc-600/90"
                      : "text-zinc-500 hover:text-zinc-800 dark:text-zinc-400 dark:hover:text-zinc-200"
                  }`}
                >
                  Put
                </button>
              </div>
            </div>

            <div className="mt-3 w-full space-y-2.5">
              <div className="w-full min-w-0">
                <label
                  htmlFor="chain-sheet-qty"
                  className="block text-xs font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400"
                >
                  Quantity
                </label>
                <input
                  id="chain-sheet-qty"
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
                  htmlFor="chain-sheet-price"
                  className="block text-xs font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400"
                >
                  Price (₹)
                </label>
                <input
                  id="chain-sheet-price"
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
                className="inline-flex h-9 min-w-0 flex-1 items-center justify-center rounded-md border border-emerald-600/95 bg-gradient-to-b from-emerald-500 to-emerald-600 px-2 text-xs font-semibold text-white shadow-sm shadow-emerald-900/20 transition hover:from-emerald-400 hover:to-emerald-500 focus:outline-none focus-visible:ring-2 focus-visible:ring-emerald-400/40 disabled:cursor-not-allowed disabled:from-emerald-800 disabled:to-emerald-900 dark:to-emerald-600"
                onClick={() => submitFromSheet("Buy")}
              >
                Buy
              </button>
              <button
                type="button"
                className="inline-flex h-9 min-w-0 flex-1 items-center justify-center rounded-md border border-red-600/90 bg-gradient-to-b from-red-500 to-red-600 px-2 text-xs font-semibold text-white shadow-sm shadow-red-900/25 transition hover:from-red-400 hover:to-red-500 focus:outline-none focus-visible:ring-2 focus-visible:ring-red-400/35 disabled:cursor-not-allowed disabled:from-red-800 disabled:to-red-900 dark:to-red-600"
                onClick={() => submitFromSheet("Sell")}
              >
                Sell
              </button>
            </div>
        </Modal>
      ) : null}
    </section>
  );
}
