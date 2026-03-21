"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useOrderConfirm } from "@/components/order/OrderConfirmProvider";
import { ExpirySelectPill } from "@/components/strategy-builder/ExpirySelectPill";
import { OptionChainUnderlyingSearch } from "@/components/order/OptionChainUnderlyingSearch";
import { apiClient } from "@/lib/api-client";
import { ltpAsOrderPrice } from "@/lib/order-confirm";
import { sortExpiryDatesAsc } from "@/lib/strategy-builder/expiry";
import { sb } from "@/lib/strategy-builder/ui";
import type {
  ChainApiResponse,
  ChainRow,
  ChainSuccess,
  UnderlyingsApiResponse,
} from "@/lib/strategy-builder/types";

const LAKH = 100_000;

function parseNum(v: unknown): number {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string") {
    const n = parseFloat(v.replace(/,/g, ""));
    return Number.isFinite(n) ? n : NaN;
  }
  return NaN;
}

function formatOiLakh(oi: number): string {
  if (!Number.isFinite(oi)) return "—";
  return `${(oi / LAKH).toLocaleString("en-IN", {
    minimumFractionDigits: 0,
    maximumFractionDigits: 1,
  })}L`;
}

function formatLtpInr(ltp: unknown): string {
  if (ltp == null || ltp === "") return "—";
  const s = String(ltp).trim();
  if (!s) return "—";
  return `₹${s}`;
}

function chainLotSize(rows: ChainRow[]): number {
  for (const r of rows) {
    const ls =
      parseNum(r.call?.lot_size) || parseNum(r.put?.lot_size);
    if (Number.isFinite(ls) && ls > 0) return Math.round(ls);
  }
  return 1;
}

function legLotSize(
  side: Record<string, unknown> | null | undefined,
  fallback: number,
): number {
  const ls = parseNum(side?.lot_size);
  if (Number.isFinite(ls) && ls > 0) return Math.round(ls);
  return fallback;
}

function formatBuySellRatio(ratio: unknown): string {
  if (typeof ratio === "number" && Number.isFinite(ratio)) {
    return ratio.toLocaleString("en-IN", {
      minimumFractionDigits: 0,
      maximumFractionDigits: 1,
    });
  }
  if (typeof ratio === "string" && ratio.trim()) {
    return ratio.trim();
  }
  return "—";
}

function formatBookQtyLakh(q: unknown): string {
  const v = parseNum(q);
  return formatOiLakh(v);
}

function BuySellBookLines({ leg }: { leg: Record<string, unknown> }) {
  const ratio = formatBuySellRatio(leg.buy_sell_ratio);
  const buy = formatBookQtyLakh(leg.total_buy_qty);
  const sell = formatBookQtyLakh(leg.total_sell_qty);
  return (
    <div className="w-full min-w-0 space-y-0.5 py-0.5 text-center">
      <div className="tabular-nums text-zinc-400">
        {ratio}
      </div>
      <div className="text-[9px] leading-tight text-zinc-500 sm:text-[10px]">
        Buy {buy}
        <span className="text-zinc-600" aria-hidden>
          {" · "}
        </span>
        Sell {sell}
      </div>
    </div>
  );
}

type SheetState = { strike: number; row: ChainRow };

export function OptionChainPlaceSection() {
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
    mutationFn: async () => {
      const q = new URLSearchParams({
        stock_code: stockCode.trim(),
        exchange_code: segmentExchange,
        expiry_date: expiryDate.trim(),
      });
      return apiClient.get<ChainApiResponse>(
        `/strategy-builder/chain?${q.toString()}`,
      );
    },
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

  const maxCallOi = chainSuccess?.max_call_oi ?? 0;
  const maxPutOi = chainSuccess?.max_put_oi ?? 0;
  const spot = chainSuccess?.spot_price ?? null;
  const atmStrike = chainSuccess?.atm_strike ?? null;

  useEffect(() => {
    if (!chainSuccess) return;
    const t = requestAnimationFrame(() => {
      const row = scrollRef.current?.querySelector(
        "tr[data-atm-strike='true']",
      );
      row?.scrollIntoView({ block: "center", behavior: "smooth" });
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
      openOrderConfirm({
        product_type: "Options",
        stock_code: chainSuccess.stock_code,
        exchange_code: chainSuccess.exchange_code,
        expiry_date: chainSuccess.expiry_display,
        right: sheetRight,
        strike_price: String(sheet.strike),
        quantity: String(qn),
        price: (sheetPrice.trim() || "0") as string,
        action,
      });
    },
    [
      chainSuccess,
      sheet,
      sheetRight,
      sheetQty,
      sheetPrice,
      closeSheet,
      openOrderConfirm,
    ],
  );

  useEffect(() => {
    if (!sheet) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") closeSheet();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [sheet, closeSheet]);

  const itmCall = (strike: number) =>
    spot != null && Number.isFinite(spot) && strike < spot;
  const itmPut = (strike: number) =>
    spot != null && Number.isFinite(spot) && strike > spot;

  const sheetHasCall = Boolean(sheet?.row.call);
  const sheetHasPut = Boolean(sheet?.row.put);

  return (
    <section className="space-y-4" aria-label="Option chain">
      <div
        className="flex min-h-[2.75rem] flex-col overflow-visible rounded-xl bg-[#1b1c1f] sm:flex-row sm:items-center"
        role="toolbar"
        aria-label="Underlying, expiry, and fetch"
      >
        <div
          className="flex shrink-0 items-center border-b border-zinc-700/70 px-2 py-2 sm:border-b-0 sm:border-r sm:border-zinc-700/70 sm:py-0 sm:ps-2.5 sm:pe-2"
          role="group"
          aria-label="Exchange segment"
        >
          <div className="inline-flex rounded-lg bg-black/30 p-0.5 ring-1 ring-zinc-700/70">
            <button
              type="button"
              onClick={() => onSegmentChange("NFO")}
              className={`rounded-md px-3 py-1.5 text-xs font-semibold transition focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-500/45 sm:text-sm ${
                segmentExchange === "NFO"
                  ? "bg-zinc-700 text-white shadow-sm"
                  : "text-zinc-400 hover:bg-zinc-800/80 hover:text-zinc-200"
              }`}
            >
              NSE
            </button>
            <button
              type="button"
              onClick={() => onSegmentChange("BFO")}
              className={`rounded-md px-3 py-1.5 text-xs font-semibold transition focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-500/45 sm:text-sm ${
                segmentExchange === "BFO"
                  ? "bg-zinc-700 text-white shadow-sm"
                  : "text-zinc-400 hover:bg-zinc-800/80 hover:text-zinc-200"
              }`}
            >
              BSE
            </button>
          </div>
        </div>

        <div className="relative z-30 flex min-w-0 flex-1 items-center overflow-visible border-b border-zinc-700/70 px-3 py-2 sm:border-b-0 sm:border-r sm:border-zinc-700/70 sm:py-2.5">
          <OptionChainUnderlyingSearch
            variant="ticker"
            chainBar
            underlyings={uq.data?.underlyings ?? []}
            value={stockCode}
            disabled={uq.isLoading}
            spot={chainSuccess?.spot_price ?? null}
            onChange={(code) => {
              setStockCode(code);
              setExpiryDate("");
              setChainSuccess(null);
              setFetchError(null);
              setSheet(null);
            }}
          />
        </div>

        <div className="relative z-20 flex shrink-0 items-center overflow-visible border-b border-zinc-700/70 px-3 py-2 sm:border-b-0 sm:border-r sm:border-zinc-700/70 sm:py-2.5">
          <ExpirySelectPill
            layout="toolbar"
            tone="darkToolbar"
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
            className="inline-flex w-full items-center justify-center rounded-lg border border-sky-500/85 bg-transparent px-4 py-2 text-sm font-semibold text-sky-400 shadow-none transition hover:bg-sky-500/10 hover:text-sky-300 focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-500/45 disabled:pointer-events-none disabled:opacity-45 sm:w-auto"
          >
            {chainMut.isPending ? "…" : "Fetch"}
          </button>
        </div>
      </div>

      {fetchError ? (
        <div className="app-alert-error text-sm">{fetchError}</div>
      ) : null}

      {chainSuccess?.chain_rows?.length ? (
        <div
          ref={scrollRef}
          className="max-h-[min(70vh,42rem)] overflow-auto rounded-xl border border-zinc-800 bg-[#0e0e10] shadow-lg ring-1 ring-black/20 dark:ring-zinc-800/80"
        >
          <table className="w-full max-w-full border-collapse text-xs leading-snug tabular-nums text-zinc-300 sm:text-sm">
            <thead className="sticky top-0 z-20 bg-[#0e0e10]">
              <tr className="border-b border-zinc-800">
                <th
                  colSpan={4}
                  className="bg-emerald-950/25 px-1 py-2 text-center text-xs font-semibold uppercase tracking-wide text-emerald-200/95 sm:text-sm"
                >
                  Calls
                </th>
                <th
                  rowSpan={2}
                  className="border-x border-zinc-700/80 bg-zinc-900 px-1 py-2 align-middle text-center text-xs font-medium uppercase tracking-wide text-zinc-300 sm:text-sm"
                >
                  Strike
                </th>
                <th
                  colSpan={4}
                  className="bg-rose-950/25 px-1 py-2 text-center text-xs font-semibold uppercase tracking-wide text-rose-200/95 sm:text-sm"
                >
                  Puts
                </th>
              </tr>
              <tr className="border-b border-zinc-800 text-[10px] font-semibold uppercase tracking-wide sm:text-xs">
                <th className="min-w-[3.5rem] bg-emerald-950 px-0.5 py-1.5 text-center text-zinc-500">
                  Buy/Sell
                </th>
                <th className="bg-emerald-950 px-0.5 py-1.5 text-end text-zinc-500">
                  OI (L)
                </th>
                <th className="min-w-[12rem] bg-emerald-950 px-0 py-1.5 text-center text-zinc-500 sm:min-w-[13.5rem]">
                  <span className="inline-flex items-center justify-center gap-1">
                    <span
                      className="h-1 w-3.5 shrink-0 rounded-full bg-[#2d4a3c]"
                      aria-hidden
                    />
                    OI
                  </span>
                </th>
                <th className="w-[3.5rem] max-w-[3.5rem] bg-emerald-950 px-0 py-1.5 pe-0.5 text-end text-zinc-500 sm:w-[3.75rem] sm:max-w-[3.75rem]">
                  LTP
                </th>
                <th className="w-[3.5rem] max-w-[3.5rem] bg-rose-950 px-0 py-1.5 ps-0.5 text-start text-zinc-500 sm:w-[3.75rem] sm:max-w-[3.75rem]">
                  LTP
                </th>
                <th className="min-w-[12rem] bg-rose-950 px-0 py-1.5 text-center text-zinc-500 sm:min-w-[13.5rem]">
                  <span className="inline-flex items-center justify-center gap-1">
                    <span
                      className="h-1 w-3.5 shrink-0 rounded-full bg-[#5a3d3a]"
                      aria-hidden
                    />
                    OI
                  </span>
                </th>
                <th className="bg-rose-950 px-0.5 py-1.5 text-start text-zinc-500">
                  OI (L)
                </th>
                <th className="min-w-[3.5rem] bg-rose-950 px-0.5 py-1.5 text-center text-zinc-500">
                  Buy/Sell
                </th>
              </tr>
            </thead>
            <tbody>
              {chainSuccess.chain_rows.map((row) => {
                const c = row.call ?? null;
                const p = row.put ?? null;
                const strike = row.strike_price;
                const isAtm =
                  atmStrike != null && strike === atmStrike;
                const callItm = c != null && itmCall(strike);
                const putItm = p != null && itmPut(strike);
                const callOi = c ? parseNum(c.open_interest) : NaN;
                const putOi = p ? parseNum(p.open_interest) : NaN;
                const callOiPct =
                  maxCallOi > 0 && Number.isFinite(callOi)
                    ? Math.min(100, (callOi / maxCallOi) * 100)
                    : 0;
                const putOiPct =
                  maxPutOi > 0 && Number.isFinite(putOi)
                    ? Math.min(100, (putOi / maxPutOi) * 100)
                    : 0;

                /** Light gray wash on ITM legs — reads as “avoid / out of play” on the dark chain. */
                const itmLegCls = "bg-zinc-500/22";
                const strikeAtmCls = isAtm
                  ? "bg-sky-950/60 font-normal text-sky-100 ring-1 ring-sky-500/35"
                  : "font-normal text-zinc-300";

                return (
                  <tr
                    key={strike}
                    data-atm-strike={isAtm ? "true" : undefined}
                    className="cursor-pointer border-b border-zinc-800/90 transition hover:bg-zinc-800/35"
                    onClick={() => openSheetForRow(row)}
                  >
                    {c ? (
                      <>
                        <td
                          className={`px-0.5 py-1 text-center align-top ${callItm ? itmLegCls : ""}`}
                        >
                          <BuySellBookLines leg={c} />
                        </td>
                        <td
                          className={`px-0.5 py-1 text-end text-zinc-400 whitespace-nowrap ${callItm ? itmLegCls : ""}`}
                        >
                          {formatOiLakh(callOi)}
                        </td>
                        <td
                          className={`px-0 py-1 ${callItm ? itmLegCls : ""}`}
                        >
                          <div
                            className="relative mx-auto h-2.5 w-full max-w-[12.5rem] overflow-hidden bg-transparent sm:h-3 sm:max-w-[14rem]"
                            title={`Call OI ${formatOiLakh(callOi)}`}
                          >
                            <div
                              className="absolute top-0 h-full rounded-l-full bg-[#2d4a3c] shadow-none"
                              style={{
                                right: 0,
                                width: `${callOiPct}%`,
                              }}
                            />
                          </div>
                        </td>
                        <td
                          className={`w-[3.5rem] max-w-[3.5rem] truncate px-0.5 py-1 pe-0.5 text-end text-xs whitespace-nowrap text-zinc-400 sm:w-[3.75rem] sm:max-w-[3.75rem] sm:text-sm ${callItm ? itmLegCls : ""}`}
                        >
                          {formatLtpInr(c.ltp)}
                        </td>
                      </>
                    ) : (
                      <td
                        colSpan={4}
                        className="bg-zinc-900/40"
                      />
                    )}
                    <td
                      className={`border-x border-zinc-800/80 bg-zinc-900/50 px-1 py-1 text-center text-xs font-normal tabular-nums whitespace-nowrap sm:text-sm ${strikeAtmCls}`}
                    >
                      {strike.toLocaleString("en-IN")}
                    </td>
                    {p ? (
                      <>
                        <td
                          className={`w-[3.5rem] max-w-[3.5rem] truncate px-0.5 py-1 ps-0.5 text-start text-xs whitespace-nowrap text-zinc-400 sm:w-[3.75rem] sm:max-w-[3.75rem] sm:text-sm ${putItm ? itmLegCls : ""}`}
                        >
                          {formatLtpInr(p.ltp)}
                        </td>
                        <td
                          className={`px-0 py-1 ${putItm ? itmLegCls : ""}`}
                        >
                          <div
                            className="relative mx-auto h-2.5 w-full max-w-[12.5rem] overflow-hidden bg-transparent sm:h-3 sm:max-w-[14rem]"
                            title={`Put OI ${formatOiLakh(putOi)}`}
                          >
                            <div
                              className="absolute top-0 h-full rounded-r-full bg-[#5a3d3a] shadow-none"
                              style={{
                                left: 0,
                                width: `${putOiPct}%`,
                              }}
                            />
                          </div>
                        </td>
                        <td
                          className={`px-0.5 py-1 text-start text-zinc-400 whitespace-nowrap ${putItm ? itmLegCls : ""}`}
                        >
                          {formatOiLakh(putOi)}
                        </td>
                        <td
                          className={`px-0.5 py-1 text-center align-top ${putItm ? itmLegCls : ""}`}
                        >
                          <BuySellBookLines leg={p} />
                        </td>
                      </>
                    ) : (
                      <td
                        colSpan={4}
                        className="bg-zinc-900/40"
                      />
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}

      {sheet && chainSuccess ? (
        <>
          <div
            className="fixed inset-0 z-[105] bg-black/40 dark:bg-black/55"
            role="presentation"
            aria-hidden
            onClick={closeSheet}
          />
          <div
            className="fixed inset-x-0 bottom-0 z-[106] max-h-[min(85dvh,28rem)] overflow-y-auto rounded-t-2xl border border-zinc-200 bg-white px-4 pb-[max(1rem,env(safe-area-inset-bottom))] pt-4 shadow-2xl dark:border-zinc-700 dark:bg-zinc-900 sm:px-5"
            role="dialog"
            aria-modal="true"
            aria-labelledby="option-chain-sheet-title"
          >
            <div className="mx-auto mb-3 h-1 w-10 shrink-0 rounded-full bg-zinc-300 dark:bg-zinc-600" />
            <h2
              id="option-chain-sheet-title"
              className="text-base font-semibold text-zinc-900 dark:text-zinc-50"
            >
              {chainSuccess.stock_code}{" "}
              <span className="font-normal text-zinc-500 dark:text-zinc-400">
                {chainSuccess.expiry_display}
              </span>
            </h2>
            <p className="mt-1 text-sm tabular-nums text-zinc-700 dark:text-zinc-300">
              Strike{" "}
              <span className="font-semibold text-zinc-900 dark:text-zinc-100">
                {sheet.strike.toLocaleString("en-IN")}
              </span>
            </p>

            <div className="mt-4">
              <span className="text-xs font-medium text-zinc-500 dark:text-zinc-400">
                Option type
              </span>
              <div
                className="mt-1.5 inline-flex rounded-xl border border-zinc-200 bg-zinc-100/90 p-0.5 dark:border-zinc-700 dark:bg-zinc-950/80"
                role="group"
                aria-label="Call or Put"
              >
                <button
                  type="button"
                  disabled={!sheetHasCall}
                  onClick={() => setRightAndSyncFields("Call")}
                  className={`rounded-lg px-3 py-2 text-sm font-semibold transition focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-500/45 disabled:cursor-not-allowed disabled:opacity-40 ${
                    sheetRight === "Call"
                      ? "bg-white text-zinc-900 shadow-sm ring-1 ring-zinc-200/80 dark:bg-zinc-800 dark:text-zinc-50 dark:ring-zinc-600"
                      : "text-zinc-600 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100"
                  }`}
                >
                  Call
                </button>
                <button
                  type="button"
                  disabled={!sheetHasPut}
                  onClick={() => setRightAndSyncFields("Put")}
                  className={`rounded-lg px-3 py-2 text-sm font-semibold transition focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-500/45 disabled:cursor-not-allowed disabled:opacity-40 ${
                    sheetRight === "Put"
                      ? "bg-white text-zinc-900 shadow-sm ring-1 ring-zinc-200/80 dark:bg-zinc-800 dark:text-zinc-50 dark:ring-zinc-600"
                      : "text-zinc-600 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100"
                  }`}
                >
                  Put
                </button>
              </div>
            </div>

            <div className="mt-4 grid grid-cols-2 gap-3">
              <div>
                <label
                  htmlFor="chain-sheet-qty"
                  className="text-xs font-medium text-zinc-500 dark:text-zinc-400"
                >
                  Quantity
                </label>
                <input
                  id="chain-sheet-qty"
                  type="number"
                  min={1}
                  className="mt-1 w-full rounded-lg border border-zinc-200 bg-white px-2 py-2 text-sm tabular-nums text-zinc-900 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
                  value={sheetQty}
                  onChange={(e) => setSheetQty(e.target.value)}
                />
              </div>
              <div>
                <label
                  htmlFor="chain-sheet-price"
                  className="text-xs font-medium text-zinc-500 dark:text-zinc-400"
                >
                  Price (₹)
                </label>
                <input
                  id="chain-sheet-price"
                  type="number"
                  step={0.05}
                  className="mt-1 w-full rounded-lg border border-zinc-200 bg-white px-2 py-2 text-sm tabular-nums text-zinc-900 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
                  value={sheetPrice}
                  onChange={(e) => setSheetPrice(e.target.value)}
                />
              </div>
            </div>

            {sheetFormError ? (
              <p className="mt-2 text-xs text-red-600 dark:text-red-400">
                {sheetFormError}
              </p>
            ) : null}

            <div className="mt-5 flex flex-wrap gap-2 border-t border-zinc-200 pt-4 dark:border-zinc-700">
              <button
                type="button"
                className={sb.btnSecondary}
                onClick={closeSheet}
              >
                Cancel
              </button>
              <button
                type="button"
                className="inline-flex flex-1 min-w-[6rem] items-center justify-center rounded-xl border border-emerald-600 bg-emerald-600 px-3 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:border-emerald-500 hover:bg-emerald-500 focus:outline-none focus-visible:ring-4 focus-visible:ring-emerald-500/35 disabled:opacity-50 dark:border-emerald-500 dark:bg-emerald-600"
                onClick={() => submitFromSheet("Buy")}
              >
                Buy
              </button>
              <button
                type="button"
                className="inline-flex flex-1 min-w-[6rem] items-center justify-center rounded-xl bg-red-600 px-3 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-red-700 focus:outline-none focus-visible:ring-4 focus-visible:ring-red-500/30 dark:bg-red-600 dark:hover:bg-red-500"
                onClick={() => submitFromSheet("Sell")}
              >
                Sell
              </button>
            </div>
          </div>
        </>
      ) : null}
    </section>
  );
}
