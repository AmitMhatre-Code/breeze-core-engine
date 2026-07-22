"use client";

import type { RefObject } from "react";
import { Fragment } from "react";
import { QuoteSourceBadge } from "@/components/shared/market-data/QuoteSourceBadge";
import { quoteMetaFromChain } from "@/lib/quote-source";
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
      <div className="font-mono tabular-nums text-muted">{ratio}</div>
      <div className="text-body leading-tight text-faint sm:text-body">
        Buy <span className="font-mono">{buy}</span>
        <span className="text-faint" aria-hidden>
          {" · "}
        </span>
        Sell <span className="font-mono">{sell}</span>
      </div>
    </div>
  );
}

const bsBtnClass =
  "inline-flex h-7 w-7 shrink-0 items-center justify-center rounded border border-border bg-panel text-heading font-bold text-foreground transition hover:bg-panel2 hover:border-accent/40 focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/30";

const bsTickClass =
  "inline-flex h-7 w-7 shrink-0 items-center justify-center rounded border border-up/30 bg-up-tint text-sm font-semibold text-up-on-tint";

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

  const quoteMeta = quoteMetaFromChain(chainSuccess);

  return (
    <div ref={scrollRef} className="min-w-0 space-y-0">
      {quoteMeta ? (
        <div className="mb-2 flex flex-wrap items-center justify-end gap-2 px-0.5">
          <QuoteSourceBadge meta={quoteMeta} variant="default" />
        </div>
      ) : null}
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
          const itmLegCls = "bg-panel2/70";
          const strikeAtmCls = isAtm
            ? "bg-atm-tint font-semibold text-accent-strong ring-1 ring-accent/25"
            : "font-semibold text-foreground";
          const rowClick =
            mode === "trade" && onRowClick
              ? () => onRowClick(row)
              : undefined;
          const cardInteractiveCls =
            mode === "trade"
              ? "cursor-pointer transition hover:border-accent/30 hover:bg-panel2 active:bg-panel2"
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
              className={`rounded-lg border border-border bg-panel p-2.5 text-xs leading-snug tabular-nums text-muted ${cardInteractiveCls}`}
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
                className={`rounded-md px-2 py-1.5 text-center text-sm ${strikeAtmCls}`}
              >
                <span className="font-mono tabular-nums">
                  {strike.toLocaleString("en-IN")}
                </span>
                {isAtm ? (
                  <span className="ms-1.5 text-body font-medium uppercase tracking-wide text-accent-strong">
                    ATM
                  </span>
                ) : null}
              </div>
              <div className="mt-2 grid min-w-0 grid-cols-2 gap-2">
                <div
                  className={`min-w-0 space-y-1.5 rounded-md border border-up/25 bg-call-tint p-2 ${callItm ? itmLegCls : ""}`}
                >
                  <div className="text-center text-body font-semibold uppercase tracking-wide text-up">
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
                      <div className="flex items-center justify-between gap-2 text-heading text-muted">
                        <span className="shrink-0 text-faint">
                          OI (L)
                        </span>
                        <span className="truncate text-end font-mono tabular-nums">
                          {formatOiLakh(callOi)}
                        </span>
                      </div>
                      <div
                        className="relative h-2 min-w-0 overflow-hidden rounded-full bg-track"
                        title={`Call OI ${formatOiLakh(callOi)}`}
                      >
                        <div
                          className="absolute top-0 h-full rounded-l-full bg-up"
                          style={{ right: 0, width: `${callOiPct}%` }}
                        />
                      </div>
                      <div className="flex items-center justify-between gap-2 text-heading">
                        <span className="text-faint">
                          LTP
                        </span>
                        <span className="truncate text-end font-mono font-medium tabular-nums text-foreground">
                          {formatLtpInr(c.ltp)}
                        </span>
                      </div>
                    </Fragment>
                  ) : (
                    <p className="py-2 text-center text-heading text-faint">
                      No contract
                    </p>
                  )}
                </div>
                <div
                  className={`min-w-0 space-y-1.5 rounded-md border border-down/25 bg-put-tint p-2 ${putItm ? itmLegCls : ""}`}
                >
                  <div className="text-center text-body font-semibold uppercase tracking-wide text-down">
                    Put
                  </div>
                  {p ? (
                    <Fragment>
                      <div className="flex items-center justify-between gap-2 text-heading">
                        <span className="text-faint">
                          LTP
                        </span>
                        <span className="truncate text-end font-mono font-medium tabular-nums text-foreground">
                          {formatLtpInr(p.ltp)}
                        </span>
                      </div>
                      <div
                        className="relative h-2 min-w-0 overflow-hidden rounded-full bg-track"
                        title={`Put OI ${formatOiLakh(putOi)}`}
                      >
                        <div
                          className="absolute top-0 h-full rounded-r-full bg-down"
                          style={{ left: 0, width: `${putOiPct}%` }}
                        />
                      </div>
                      <div className="flex items-center justify-between gap-2 text-heading text-muted">
                        <span className="shrink-0 text-faint">
                          OI (L)
                        </span>
                        <span className="truncate text-end font-mono tabular-nums">
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
                    <p className="py-2 text-center text-heading text-faint">
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
          "hidden min-w-0 md:block",
          mode === "strategyBuilder"
            ? "-mx-5 overflow-x-auto"
            : "max-h-[min(70vh,42rem)] overflow-x-auto overflow-y-auto overscroll-x-contain overscroll-y-auto rounded-md border border-border bg-panel",
        ].join(" ")}
      >
      <table className="w-full max-w-full border-collapse text-xs leading-snug tabular-nums text-muted sm:text-sm">
        <thead
          className={
            mode === "strategyBuilder"
              ? "sticky top-0 z-20 bg-panel"
              : "sticky top-0 z-20 bg-white dark:bg-[var(--panel)]"
          }
        >
          <tr className="border-b border-border-soft">
            <th
              colSpan={4}
              className="bg-call-head px-1 py-2 text-center text-xs font-semibold uppercase tracking-wide text-white sm:text-sm"
            >
              Calls
            </th>
            <th
              rowSpan={2}
              className="border-x border-border-soft bg-panel2 px-1 py-2 align-middle text-center text-xs font-medium uppercase tracking-wide text-muted sm:text-sm"
            >
              Strike
            </th>
            <th
              colSpan={4}
              className="bg-put-head px-1 py-2 text-center text-xs font-semibold uppercase tracking-wide text-white sm:text-sm"
            >
              Puts
            </th>
          </tr>
          <tr className="border-b border-border-soft text-body font-semibold uppercase tracking-wide sm:text-xs">
            <th className="min-w-[3.5rem] bg-call-tint px-0.5 py-1.5 text-center text-muted">
              {outerHeader}
            </th>
            <th className="bg-call-tint px-0.5 py-1.5 text-end text-muted">
              OI (L)
            </th>
            <th className="min-w-[11rem] bg-call-tint px-0 py-1.5 text-center text-muted sm:min-w-[12.5rem]">
              <span className="inline-flex items-center justify-center gap-1">
                <span
                  className="h-1 w-3.5 shrink-0 rounded-full bg-[var(--up)]"
                  aria-hidden
                />
                OI
              </span>
            </th>
            <th className="w-[4.5rem] max-w-[4.5rem] bg-call-tint px-0 py-1.5 pe-0.5 text-end text-muted sm:w-[4.75rem] sm:max-w-[4.75rem]">
              LTP
            </th>
            <th className="w-[4.5rem] max-w-[4.5rem] bg-put-tint px-0 py-1.5 ps-0.5 text-start text-muted sm:w-[4.75rem] sm:max-w-[4.75rem]">
              LTP
            </th>
            <th className="min-w-[11rem] bg-put-tint px-0 py-1.5 text-center text-muted sm:min-w-[12.5rem]">
              <span className="inline-flex items-center justify-center gap-1">
                <span
                  className="h-1 w-3.5 shrink-0 rounded-full bg-down"
                  aria-hidden
                />
                OI
              </span>
            </th>
            <th className="bg-put-tint px-0.5 py-1.5 text-start text-muted">
              OI (L)
            </th>
            <th className="min-w-[3.5rem] bg-put-tint px-0.5 py-1.5 text-center text-muted">
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

            const itmLegCls = "bg-panel2/70";
            const strikeAtmCls = isAtm
              ? "bg-atm-tint font-normal text-accent-strong ring-1 ring-accent/25"
              : "font-normal text-muted";

            const rowClick =
              mode === "trade" && onRowClick
                ? () => onRowClick(row)
                : undefined;
            const trCls =
              mode === "trade"
                ? "cursor-pointer border-b border-border-soft transition hover:bg-panel2"
                : "border-b border-border-soft";

            const trInteractiveCls =
              mode === "trade" && onRowClick
                ? " focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-accent"
                : "";

            return (
              <tr
                key={strike}
                data-atm-strike={isAtm ? "true" : undefined}
                className={trCls + trInteractiveCls}
                role={mode === "trade" && onRowClick ? "button" : undefined}
                tabIndex={mode === "trade" && onRowClick ? 0 : undefined}
                aria-label={
                  mode === "trade" && onRowClick
                    ? `Open order sheet for strike ${strike.toLocaleString("en-IN")}`
                    : undefined
                }
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
                      className={`px-0.5 py-1 text-end font-mono text-muted whitespace-nowrap ${callItm ? itmLegCls : ""}`}
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
                          className="absolute top-0 h-full rounded-l-full bg-up shadow-none"
                          style={{
                            right: 0,
                            width: `${callOiPct}%`,
                          }}
                        />
                      </div>
                    </td>
                    <td
                      className={`w-[4.5rem] max-w-[4.5rem] truncate px-0.5 py-1 pe-0.5 text-end font-mono text-xs whitespace-nowrap text-muted sm:w-[4.75rem] sm:max-w-[4.75rem] sm:text-sm ${callItm ? itmLegCls : ""}`}
                    >
                      {formatLtpInr(c.ltp)}
                    </td>
                  </>
                ) : (
                  <td colSpan={4} className="bg-panel2" />
                )}
                <td
                  className={`border-x border-border-soft bg-panel2 px-1 py-1 text-center font-mono text-xs font-normal tabular-nums whitespace-nowrap sm:text-sm ${strikeAtmCls}`}
                >
                  {strike.toLocaleString("en-IN")}
                  {isAtm ? (
                    <span className="ms-1 rounded-[4px] bg-accent px-1 py-px align-middle text-micro font-bold tracking-[.04em] text-accent-ink">
                      ATM
                    </span>
                  ) : null}
                </td>
                {p ? (
                  <>
                    <td
                      className={`w-[4.5rem] max-w-[4.5rem] truncate px-0.5 py-1 ps-0.5 text-start font-mono text-xs whitespace-nowrap text-muted sm:w-[4.75rem] sm:max-w-[4.75rem] sm:text-sm ${putItm ? itmLegCls : ""}`}
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
                          className="absolute top-0 h-full rounded-r-full bg-down shadow-none"
                          style={{
                            left: 0,
                            width: `${putOiPct}%`,
                          }}
                        />
                      </div>
                    </td>
                    <td
                      className={`px-0.5 py-1 text-start font-mono text-muted whitespace-nowrap ${putItm ? itmLegCls : ""}`}
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
                  <td colSpan={4} className="bg-panel2" />
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
