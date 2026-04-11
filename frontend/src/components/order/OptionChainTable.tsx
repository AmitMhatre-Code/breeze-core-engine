"use client";

import type { RefObject } from "react";
import { Fragment } from "react";
import type { ChainRow, ChainSuccess, OrderSide, OptionRight } from "@/lib/strategy-builder/types";

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

export function chainLotSize(rows: ChainRow[]): number {
  for (const r of rows) {
    const ls = parseNum(r.call?.lot_size) || parseNum(r.put?.lot_size);
    if (Number.isFinite(ls) && ls > 0) return Math.round(ls);
  }
  return 1;
}

export function legLotSize(
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
      <div className="tabular-nums text-zinc-600 dark:text-zinc-400">{ratio}</div>
      <div className="text-[9px] leading-tight text-zinc-500 dark:text-zinc-500 sm:text-[10px]">
        Buy {buy}
        <span className="text-zinc-600 dark:text-zinc-500" aria-hidden>
          {" · "}
        </span>
        Sell {sell}
      </div>
    </div>
  );
}

const bsBtnClass =
  "inline-flex h-7 w-7 shrink-0 items-center justify-center rounded border border-zinc-200 bg-white/80 text-[11px] font-bold text-zinc-900 shadow-sm transition hover:bg-zinc-50 hover:border-sky-500/40 focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-500/30 dark:border-zinc-600/90 dark:bg-zinc-800/80 dark:text-zinc-100 dark:hover:bg-zinc-700 dark:hover:border-zinc-500 dark:focus-visible:ring-sky-500/45";

const bsTickClass =
  "inline-flex h-7 w-7 shrink-0 items-center justify-center rounded border border-emerald-200/80 bg-emerald-50 text-sm font-semibold text-emerald-700 dark:border-emerald-800/60 dark:bg-emerald-950/40 dark:text-emerald-400";

function StrategyBuySellPair({
  strike,
  right,
  isAdded,
  onBuySell,
}: {
  strike: number;
  right: OptionRight;
  isAdded: (side: OrderSide) => boolean;
  onBuySell: (side: OrderSide) => void;
}) {
  const strikeLabel = strike.toLocaleString("en-IN");
  const rightLabel = right === "Call" ? "Call" : "Put";
  return (
    <div className="flex items-start justify-center gap-1 py-0.5">
      {isAdded("Buy") ? (
        <span
          className={bsTickClass}
          aria-label={`Already added — Buy ${rightLabel} ${strikeLabel}`}
          role="img"
        >
          ✓
        </span>
      ) : (
        <button
          type="button"
          className={bsBtnClass}
          aria-label={`Buy ${rightLabel} ${strikeLabel}`}
          onClick={(e) => {
            e.stopPropagation();
            onBuySell("Buy");
          }}
        >
          B
        </button>
      )}
      {isAdded("Sell") ? (
        <span
          className={bsTickClass}
          aria-label={`Already added — Sell ${rightLabel} ${strikeLabel}`}
          role="img"
        >
          ✓
        </span>
      ) : (
        <button
          type="button"
          className={bsBtnClass}
          aria-label={`Sell ${rightLabel} ${strikeLabel}`}
          onClick={(e) => {
            e.stopPropagation();
            onBuySell("Sell");
          }}
        >
          S
        </button>
      )}
    </div>
  );
}

export type OptionChainTableMode = "trade" | "strategyBuilder";

export type OptionChainTableProps = {
  chainSuccess: ChainSuccess;
  scrollRef?: RefObject<HTMLDivElement | null>;
  mode: OptionChainTableMode;
  /** Trade mode: open sheet / focus row. */
  onRowClick?: (row: ChainRow) => void;
  /** Strategy builder: append leg from outer B/S. */
  onStrategyBuySell?: (
    side: OrderSide,
    row: ChainRow,
    right: OptionRight,
  ) => void;
  /** Strategy builder: slot already present in Legs (strike + right + side). */
  isStrategySlotAdded?: (
    strike: number,
    right: OptionRight,
    side: OrderSide,
  ) => boolean;
};

/** Scrolls the first visible ATM strike (table row or mobile card) into view. */
export function scrollOptionChainAtmIntoView(
  container: HTMLElement | null | undefined,
  options?: ScrollIntoViewOptions,
) {
  if (!container) return;
  const merged: ScrollIntoViewOptions = {
    block: "center",
    behavior: "smooth",
    ...options,
  };
  const nodes = container.querySelectorAll("[data-atm-strike='true']");
  for (const n of nodes) {
    if (!(n instanceof HTMLElement)) continue;
    if (!n.offsetParent) continue;
    n.scrollIntoView(merged);
    return;
  }
}

export function OptionChainTable({
  chainSuccess,
  scrollRef,
  mode,
  onRowClick,
  onStrategyBuySell,
  isStrategySlotAdded,
}: OptionChainTableProps) {
  const maxCallOi = chainSuccess.max_call_oi ?? 0;
  const maxPutOi = chainSuccess.max_put_oi ?? 0;
  const spot = chainSuccess.spot_price ?? null;
  const atmStrike = chainSuccess.atm_strike ?? null;

  const itmCall = (strike: number) =>
    spot != null && Number.isFinite(spot) && strike < spot;
  const itmPut = (strike: number) =>
    spot != null && Number.isFinite(spot) && strike > spot;

  const outerHeader =
    mode === "strategyBuilder" ? "B/S" : "Buy/Sell";

  return (
    <div ref={scrollRef} className="min-w-0 space-y-0">
      <div className="space-y-2 md:hidden">
        {chainSuccess.chain_rows.map((row) => {
          const c = row.call ?? null;
          const p = row.put ?? null;
          const strike = row.strike_price;
          const isAtm = atmStrike != null && strike === atmStrike;
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
          const itmLegCls = "bg-zinc-900/5 dark:bg-zinc-500/22";
          const strikeAtmCls = isAtm
            ? "bg-sky-100/80 font-semibold text-sky-900 ring-1 ring-sky-500/20 dark:bg-sky-950/60 dark:text-sky-100 dark:ring-sky-500/35"
            : "font-semibold text-zinc-800 dark:text-zinc-100";
          const rowClick =
            mode === "trade" && onRowClick
              ? () => onRowClick(row)
              : undefined;
          const cardInteractiveCls =
            mode === "trade"
              ? "cursor-pointer transition hover:border-zinc-300 hover:bg-zinc-50/80 active:bg-zinc-100/80 dark:hover:border-zinc-600 dark:hover:bg-zinc-900/50 dark:active:bg-zinc-900/70"
              : "";

          return (
            <div
              key={strike}
              data-atm-strike={isAtm ? "true" : undefined}
              role={mode === "trade" ? "button" : undefined}
              tabIndex={mode === "trade" ? 0 : undefined}
              aria-label={
                mode === "trade"
                  ? `Open order sheet for strike ${strike.toLocaleString("en-IN")}`
                  : undefined
              }
              className={`rounded-lg border border-zinc-200/90 bg-white p-2.5 text-xs leading-snug shadow-sm ring-1 ring-black/[0.04] tabular-nums text-zinc-700 dark:border-zinc-800 dark:bg-[#0e0e10] dark:text-zinc-300 dark:ring-black/20 ${cardInteractiveCls}`}
              onClick={rowClick}
              onKeyDown={
                mode === "trade" && onRowClick
                  ? (e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        onRowClick(row);
                      }
                    }
                  : undefined
              }
            >
              <div
                className={`rounded-md px-2 py-1.5 text-center text-sm tabular-nums ${strikeAtmCls}`}
              >
                {strike.toLocaleString("en-IN")}
                {isAtm ? (
                  <span className="ms-1.5 text-[10px] font-medium uppercase tracking-wide text-sky-700 dark:text-sky-300">
                    ATM
                  </span>
                ) : null}
              </div>
              <div className="mt-2 grid min-w-0 grid-cols-2 gap-2">
                <div
                  className={`min-w-0 space-y-1.5 rounded-md border border-emerald-200/60 bg-emerald-50/40 p-2 dark:border-emerald-900/40 dark:bg-emerald-950/20 ${callItm ? itmLegCls : ""}`}
                >
                  <div className="text-center text-[10px] font-semibold uppercase tracking-wide text-emerald-800 dark:text-emerald-200/90">
                    Call
                  </div>
                  {c ? (
                    <Fragment>
                      <div className="flex justify-center">
                        {mode === "strategyBuilder" &&
                        onStrategyBuySell &&
                        isStrategySlotAdded ? (
                          <StrategyBuySellPair
                            strike={strike}
                            right="Call"
                            isAdded={(side) =>
                              isStrategySlotAdded(strike, "Call", side)
                            }
                            onBuySell={(side) =>
                              onStrategyBuySell(side, row, "Call")
                            }
                          />
                        ) : (
                          <BuySellBookLines leg={c} />
                        )}
                      </div>
                      <div className="flex items-center justify-between gap-2 text-[11px] text-zinc-600 dark:text-zinc-400">
                        <span className="shrink-0 text-zinc-500 dark:text-zinc-500">
                          OI (L)
                        </span>
                        <span className="truncate text-end tabular-nums">
                          {formatOiLakh(callOi)}
                        </span>
                      </div>
                      <div
                        className="relative h-2 min-w-0 overflow-hidden rounded-full bg-zinc-200/80 dark:bg-zinc-800/80"
                        title={`Call OI ${formatOiLakh(callOi)}`}
                      >
                        <div
                          className="absolute top-0 h-full rounded-l-full bg-emerald-600/60 dark:bg-[#2d4a3c]"
                          style={{ right: 0, width: `${callOiPct}%` }}
                        />
                      </div>
                      <div className="flex items-center justify-between gap-2 text-[11px]">
                        <span className="text-zinc-500 dark:text-zinc-500">
                          LTP
                        </span>
                        <span className="truncate text-end font-medium tabular-nums text-zinc-800 dark:text-zinc-100">
                          {formatLtpInr(c.ltp)}
                        </span>
                      </div>
                    </Fragment>
                  ) : (
                    <p className="py-2 text-center text-[11px] text-zinc-400 dark:text-zinc-600">
                      No contract
                    </p>
                  )}
                </div>
                <div
                  className={`min-w-0 space-y-1.5 rounded-md border border-rose-200/70 bg-rose-50/35 p-2 dark:border-rose-900/35 dark:bg-rose-950/15 ${putItm ? itmLegCls : ""}`}
                >
                  <div className="text-center text-[10px] font-semibold uppercase tracking-wide text-rose-900 dark:text-rose-200/90">
                    Put
                  </div>
                  {p ? (
                    <Fragment>
                      <div className="flex items-center justify-between gap-2 text-[11px]">
                        <span className="text-zinc-500 dark:text-zinc-500">
                          LTP
                        </span>
                        <span className="truncate text-end font-medium tabular-nums text-zinc-800 dark:text-zinc-100">
                          {formatLtpInr(p.ltp)}
                        </span>
                      </div>
                      <div
                        className="relative h-2 min-w-0 overflow-hidden rounded-full bg-zinc-200/80 dark:bg-zinc-800/80"
                        title={`Put OI ${formatOiLakh(putOi)}`}
                      >
                        <div
                          className="absolute top-0 h-full rounded-r-full bg-red-700/60 dark:bg-[#5a3d3a]"
                          style={{ left: 0, width: `${putOiPct}%` }}
                        />
                      </div>
                      <div className="flex items-center justify-between gap-2 text-[11px] text-zinc-600 dark:text-zinc-400">
                        <span className="shrink-0 text-zinc-500 dark:text-zinc-500">
                          OI (L)
                        </span>
                        <span className="truncate text-end tabular-nums">
                          {formatOiLakh(putOi)}
                        </span>
                      </div>
                      <div className="flex justify-center">
                        {mode === "strategyBuilder" &&
                        onStrategyBuySell &&
                        isStrategySlotAdded ? (
                          <StrategyBuySellPair
                            strike={strike}
                            right="Put"
                            isAdded={(side) =>
                              isStrategySlotAdded(strike, "Put", side)
                            }
                            onBuySell={(side) =>
                              onStrategyBuySell(side, row, "Put")
                            }
                          />
                        ) : (
                          <BuySellBookLines leg={p} />
                        )}
                      </div>
                    </Fragment>
                  ) : (
                    <p className="py-2 text-center text-[11px] text-zinc-400 dark:text-zinc-600">
                      No contract
                    </p>
                  )}
                </div>
              </div>
            </div>
          );
        })}
      </div>

      <div
        className={[
          "hidden min-w-0 md:block rounded-md border border-zinc-200 bg-white shadow-lg ring-1 ring-black/5 dark:border-zinc-800 dark:bg-[#0e0e10] dark:ring-black/20 dark:ring-zinc-800/80",
          mode === "strategyBuilder"
            ? "overflow-x-auto"
            : "max-h-[min(70vh,42rem)] overflow-x-auto overflow-y-auto overscroll-x-contain overscroll-y-auto",
        ].join(" ")}
      >
      <table className="w-full max-w-full border-collapse text-xs leading-snug tabular-nums text-zinc-700 dark:text-zinc-300 sm:text-sm">
        <thead className="sticky top-0 z-20 bg-white dark:bg-[#0e0e10]">
          <tr className="border-b border-zinc-200/90 dark:border-zinc-800">
            <th
              colSpan={4}
              className="bg-emerald-600 px-1 py-2 text-center text-xs font-semibold uppercase tracking-wide text-emerald-50 dark:bg-emerald-950/25 dark:text-emerald-200/95 sm:text-sm"
            >
              Calls
            </th>
            <th
              rowSpan={2}
              className="border-x border-zinc-200/80 bg-zinc-50 px-1 py-2 align-middle text-center text-xs font-medium uppercase tracking-wide text-zinc-600 dark:border-zinc-700/80 dark:bg-zinc-900 dark:text-zinc-300 sm:text-sm"
            >
              Strike
            </th>
            <th
              colSpan={4}
              className="bg-red-800/90 px-1 py-2 text-center text-xs font-semibold uppercase tracking-wide text-rose-50 dark:bg-red-950/25 dark:text-rose-200/95 sm:text-sm"
            >
              Puts
            </th>
          </tr>
          <tr className="border-b border-zinc-200/90 text-[10px] font-semibold uppercase tracking-wide sm:text-xs dark:border-zinc-800">
            <th className="min-w-[3.5rem] bg-emerald-500/50 px-0.5 py-1.5 text-center text-zinc-700 dark:bg-emerald-950 dark:text-zinc-500">
              {outerHeader}
            </th>
            <th className="bg-emerald-500/50 px-0.5 py-1.5 text-end text-zinc-700 dark:bg-emerald-950 dark:text-zinc-500">
              OI (L)
            </th>
            <th className="min-w-[11rem] bg-emerald-500/50 px-0 py-1.5 text-center text-zinc-700 dark:bg-emerald-950 dark:text-zinc-500 sm:min-w-[12.5rem]">
              <span className="inline-flex items-center justify-center gap-1">
                <span
                  className="h-1 w-3.5 shrink-0 rounded-full bg-[#00a63e]"
                  aria-hidden
                />
                OI
              </span>
            </th>
            <th className="w-[4.5rem] max-w-[4.5rem] bg-emerald-500/50 px-0 py-1.5 pe-0.5 text-end text-zinc-700 dark:bg-emerald-950 dark:text-zinc-500 sm:w-[4.75rem] sm:max-w-[4.75rem]">
              LTP
            </th>
            <th className="w-[4.5rem] max-w-[4.5rem] bg-rose-100 px-0 py-1.5 ps-0.5 text-start text-zinc-700 dark:bg-rose-950 dark:text-zinc-500 sm:w-[4.75rem] sm:max-w-[4.75rem]">
              LTP
            </th>
            <th className="min-w-[11rem] bg-rose-100 px-0 py-1.5 text-center text-zinc-700 dark:bg-rose-950 dark:text-zinc-500 sm:min-w-[12.5rem]">
              <span className="inline-flex items-center justify-center gap-1">
                <span
                  className="h-1 w-3.5 shrink-0 rounded-full bg-red-700/60 dark:bg-[#5a3d3a]"
                  aria-hidden
                />
                OI
              </span>
            </th>
            <th className="bg-rose-100 px-0.5 py-1.5 text-start text-zinc-700 dark:bg-rose-950 dark:text-zinc-500">
              OI (L)
            </th>
            <th className="min-w-[3.5rem] bg-rose-100 px-0.5 py-1.5 text-center text-zinc-700 dark:bg-rose-950 dark:text-zinc-500">
              {outerHeader}
            </th>
          </tr>
        </thead>
        <tbody>
          {chainSuccess.chain_rows.map((row) => {
            const c = row.call ?? null;
            const p = row.put ?? null;
            const strike = row.strike_price;
            const isAtm = atmStrike != null && strike === atmStrike;
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

            const itmLegCls = "bg-zinc-900/5 dark:bg-zinc-500/22";
            const strikeAtmCls = isAtm
              ? "bg-sky-100/80 font-normal text-sky-900 ring-1 ring-sky-500/20 dark:bg-sky-950/60 dark:text-sky-100 dark:ring-sky-500/35"
              : "font-normal text-zinc-700 dark:text-zinc-300";

            const rowClick =
              mode === "trade" && onRowClick
                ? () => onRowClick(row)
                : undefined;
            const trCls =
              mode === "trade"
                ? "cursor-pointer border-b border-zinc-200/90 transition hover:bg-zinc-100/70 dark:border-zinc-800/90 dark:hover:bg-zinc-800/35"
                : "border-b border-zinc-200/90 dark:border-zinc-800/90";

            return (
              <tr
                key={strike}
                data-atm-strike={isAtm ? "true" : undefined}
                className={trCls}
                onClick={rowClick}
              >
                {c ? (
                  <>
                    <td
                      className={`px-0.5 py-1 text-center align-top ${callItm ? itmLegCls : ""}`}
                    >
                      {mode === "strategyBuilder" &&
                      onStrategyBuySell &&
                      isStrategySlotAdded ? (
                        <StrategyBuySellPair
                          strike={strike}
                          right="Call"
                          isAdded={(side) =>
                            isStrategySlotAdded(strike, "Call", side)
                          }
                          onBuySell={(side) =>
                            onStrategyBuySell(side, row, "Call")
                          }
                        />
                      ) : (
                        <BuySellBookLines leg={c} />
                      )}
                    </td>
                    <td
                      className={`px-0.5 py-1 text-end text-zinc-600 dark:text-zinc-400 whitespace-nowrap ${callItm ? itmLegCls : ""}`}
                    >
                      {formatOiLakh(callOi)}
                    </td>
                    <td
                      className={`overflow-visible px-0 py-1 ${callItm ? itmLegCls : ""}`}
                    >
                      <div
                        className="relative h-2.5 w-full min-w-0 overflow-hidden bg-transparent sm:h-3"
                        title={`Call OI ${formatOiLakh(callOi)}`}
                      >
                        <div
                          className="absolute top-0 h-full rounded-l-full bg-emerald-600/60 dark:bg-[#2d4a3c] shadow-none"
                          style={{
                            right: 0,
                            width: `${callOiPct}%`,
                          }}
                        />
                      </div>
                    </td>
                    <td
                      className={`w-[4.5rem] max-w-[4.5rem] truncate px-0.5 py-1 pe-0.5 text-end text-xs whitespace-nowrap text-zinc-600 dark:text-zinc-400 sm:w-[4.75rem] sm:max-w-[4.75rem] sm:text-sm ${callItm ? itmLegCls : ""}`}
                    >
                      {formatLtpInr(c.ltp)}
                    </td>
                  </>
                ) : (
                  <td colSpan={4} className="bg-zinc-100/70 dark:bg-zinc-900/40" />
                )}
                <td
                  className={`border-x border-zinc-200/80 bg-zinc-50/90 px-1 py-1 text-center text-xs font-normal tabular-nums whitespace-nowrap sm:text-sm ${strikeAtmCls} dark:border-zinc-800/80 dark:bg-zinc-900/50`}
                >
                  {strike.toLocaleString("en-IN")}
                </td>
                {p ? (
                  <>
                    <td
                      className={`w-[4.5rem] max-w-[4.5rem] truncate px-0.5 py-1 ps-0.5 text-start text-xs whitespace-nowrap text-zinc-600 dark:text-zinc-400 sm:w-[4.75rem] sm:max-w-[4.75rem] sm:text-sm ${putItm ? itmLegCls : ""}`}
                    >
                      {formatLtpInr(p.ltp)}
                    </td>
                    <td
                      className={`overflow-visible px-0 py-1 ${putItm ? itmLegCls : ""}`}
                    >
                      <div
                        className="relative h-2.5 w-full min-w-0 overflow-hidden bg-transparent sm:h-3"
                        title={`Put OI ${formatOiLakh(putOi)}`}
                      >
                        <div
                          className="absolute top-0 h-full rounded-r-full bg-red-700/60 dark:bg-[#5a3d3a] shadow-none"
                          style={{
                            left: 0,
                            width: `${putOiPct}%`,
                          }}
                        />
                      </div>
                    </td>
                    <td
                      className={`px-0.5 py-1 text-start text-zinc-600 dark:text-zinc-400 whitespace-nowrap ${putItm ? itmLegCls : ""}`}
                    >
                      {formatOiLakh(putOi)}
                    </td>
                    <td
                      className={`px-0.5 py-1 text-center align-top ${putItm ? itmLegCls : ""}`}
                    >
                      {mode === "strategyBuilder" &&
                      onStrategyBuySell &&
                      isStrategySlotAdded ? (
                        <StrategyBuySellPair
                          strike={strike}
                          right="Put"
                          isAdded={(side) =>
                            isStrategySlotAdded(strike, "Put", side)
                          }
                          onBuySell={(side) =>
                            onStrategyBuySell(side, row, "Put")
                          }
                        />
                      ) : (
                        <BuySellBookLines leg={p} />
                      )}
                    </td>
                  </>
                ) : (
                  <td colSpan={4} className="bg-zinc-100/70 dark:bg-zinc-900/40" />
                )}
              </tr>
            );
          })}
        </tbody>
      </table>
      </div>
    </div>
  );
}
