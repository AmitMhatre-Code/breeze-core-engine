"use client";

import { QuoteSourceBadge } from "@/components/market-data/QuoteSourceBadge";
import { InfoPopover } from "@/components/strategy-builder/InfoPopover";
import { LegAggressivePriceInput } from "@/components/strategy-builder/LegAggressivePriceInput";
import { LegQuantityInput } from "@/components/strategy-builder/LegQuantityInput";
import { LegQuantityHeader } from "@/components/strategy-builder/LegQuantityHeader";
import { cloneLeg, LegRowActions } from "@/components/strategy-builder/LegRowActions";
import { LegRightToggle, LegSideToggle } from "@/components/strategy-builder/LegToggles";
import { StrikeSelectPill } from "@/components/strategy-builder/StrikeSelectPill";
import { formatIndianMoneyCompact } from "@/lib/format-money-in";
import { formatLegMargin } from "@/lib/strategy-builder/leg-ui-helpers";
import { sb } from "@/lib/strategy-builder/ui";
import type {
  BasketLegMarginEntry,
  OptionRight,
  OrderSide,
  QuoteMeta,
  StrategyLeg,
} from "@/lib/strategy-builder/types";

export function BasketLegsPanel({
  sectionNumber,
  strikes,
  chainBusy,
  chainReady,
  showOptionChain,
  onShowOptionChain,
  onHideOptionChain,
  lotSize,
  legs,
  onLegsChange,
  onAddLeg,
  onStrikeChange,
  onRightChange,
  onSideChange,
  onPriceChange,
  legMargins,
  spanBaselineLoading = false,
  totalsNetPremium,
  totalsMargin,
  onExecute,
  executeDisabled,
  addLegDisabled,
  quoteMeta = null,
}: {
  sectionNumber: number;
  strikes: number[];
  chainBusy: boolean;
  chainReady: boolean;
  showOptionChain: boolean;
  onShowOptionChain: () => void;
  onHideOptionChain: () => void;
  lotSize: number;
  legs: StrategyLeg[];
  onLegsChange: (updater: (prev: StrategyLeg[]) => StrategyLeg[]) => void;
  onAddLeg: () => void;
  onStrikeChange: (legId: string, strike: number) => void;
  onRightChange: (legId: string, right: OptionRight) => void;
  onSideChange: (legId: string, side: OrderSide) => void;
  onPriceChange: (legId: string, premiumPerUnit: number | undefined) => void;
  legMargins: Record<string, BasketLegMarginEntry>;
  spanBaselineLoading?: boolean;
  totalsNetPremium: number;
  totalsMargin: {
    hasPositiveLots: boolean;
    isFetching: boolean;
    netMargin: number | null;
  };
  onExecute: () => void;
  executeDisabled: boolean;
  addLegDisabled: boolean;
  quoteMeta?: QuoteMeta | null;
}) {
  return (
    <section id="basket-order-legs" className={`${sb.section} space-y-4`}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className={sb.sectionTitle}>{sectionNumber}. Legs</h2>
        <div className="flex flex-wrap items-center gap-2">
          {showOptionChain ? (
            <button
              type="button"
              onClick={onHideOptionChain}
              className={sb.btnSecondary}
            >
              Hide option chain
            </button>
          ) : (
            <button
              type="button"
              disabled={!chainReady}
              onClick={onShowOptionChain}
              className={sb.btnSecondary}
            >
              Pick from option chain
            </button>
          )}
          <button
            type="button"
            disabled={addLegDisabled}
            onClick={onAddLeg}
            className={sb.btnSecondary}
          >
            Add leg
          </button>
        </div>
      </div>

      {!chainReady ? (
        <p className="text-sm text-zinc-500 dark:text-zinc-400">
          {chainBusy ? "Loading contract details…" : "Waiting for option chain data…"}
        </p>
      ) : null}

      {legs.length === 0 ? (
        <p className="text-sm text-zinc-500 dark:text-zinc-400">
          Click{" "}
          <span className="font-medium text-zinc-700 dark:text-zinc-300">Add leg</span>{" "}
          to build your basket manually, or{" "}
          {showOptionChain ? (
            "pick Buy/Sell from the option chain above."
          ) : (
            <>
              <button
                type="button"
                disabled={!chainReady}
                onClick={onShowOptionChain}
                className="font-medium text-sky-700 underline underline-offset-2 hover:text-sky-800 disabled:cursor-not-allowed disabled:no-underline disabled:opacity-50 dark:text-sky-400 dark:hover:text-sky-300"
              >
                pick from the option chain
              </button>
              .
            </>
          )}
        </p>
      ) : (
        <>
          {quoteMeta ? (
            <div className="mb-1">
              <QuoteSourceBadge meta={quoteMeta} variant="footnote" />
            </div>
          ) : null}
          <div className="app-table-wrap">
          <table className="w-full min-w-[52rem] border-collapse text-left text-xs">
            <thead className="app-table-head">
              <tr>
                <th className="px-2 py-1.5 font-medium">Strike</th>
                <th className="px-2 py-1.5 font-medium">Type</th>
                <th className="px-2 py-1.5 font-medium">Position</th>
                <LegQuantityHeader />
                <th className="px-2 py-1.5 font-medium">Price</th>
                <th className="px-2 py-1.5 font-medium">Premium</th>
                <th className="px-2 py-1.5 font-medium">
                  <span className="inline-flex items-center gap-1">
                    Margin
                    <InfoPopover title="SPAN margin" ariaLabel="SPAN margin help">
                      Approximate margin from the exchange SPAN file for the quantity
                      entered.
                    </InfoPopover>
                  </span>
                </th>
                <th className="px-2 py-1.5 font-medium" />
              </tr>
            </thead>
            <tbody>
              {legs.map((l) => {
                const qtyU = l.lots > 0 ? Math.round(l.lots * lotSize) : 0;
                const aggressive = l.aggressiveLimit ?? false;
                const premTotal = aggressive
                  ? null
                  : (l.premiumPerUnit ?? 0) * qtyU;
                const legEntry = legMargins[l.id];
                return (
                  <tr key={l.id} className="app-table-row">
                    <td className="max-w-[8rem] overflow-hidden px-2 py-1.5">
                      <StrikeSelectPill
                        strikes={strikes}
                        value={l.strike}
                        onChange={(strike) => onStrikeChange(l.id, strike)}
                        busy={chainBusy}
                        layout="table"
                        hideLabel
                      />
                    </td>
                    <td className="px-2 py-1.5">
                      <LegRightToggle
                        value={l.right}
                        onChange={(right) => onRightChange(l.id, right)}
                      />
                    </td>
                    <td className="px-2 py-1.5">
                      <LegSideToggle
                        value={l.side}
                        onChange={(side) => onSideChange(l.id, side)}
                      />
                    </td>
                    <td className="px-2 py-1.5">
                      <LegQuantityInput
                        legId={l.id}
                        lots={l.lots}
                        lotSize={lotSize}
                        maxDigits={8}
                        snapWhileTyping
                        onLotsChange={(newLots) =>
                          onLegsChange((prev) =>
                            prev.map((x) =>
                              x.id === l.id ? { ...x, lots: newLots } : x,
                            ),
                          )
                        }
                        className={`${sb.tableInput} w-[8ch] max-w-[5.5rem] tabular-nums`}
                      />
                    </td>
                    <td className="px-2 py-1.5">
                      <LegAggressivePriceInput
                        aggressive={aggressive}
                        premiumPerUnit={l.premiumPerUnit}
                        ariaLabel={`Aggressive limit for ${l.strike} ${l.right}`}
                        onAggressiveChange={(checked) =>
                          onLegsChange((prev) =>
                            prev.map((x) =>
                              x.id === l.id
                                ? {
                                    ...x,
                                    aggressiveLimit: checked,
                                    ...(checked
                                      ? { premiumPerUnit: undefined }
                                      : {}),
                                  }
                                : x,
                            ),
                          )
                        }
                        onPriceChange={(premiumPerUnit) =>
                          onPriceChange(l.id, premiumPerUnit)
                        }
                      />
                    </td>
                    <td className="px-2 py-1.5 tabular-nums text-zinc-800 dark:text-zinc-200">
                      {premTotal == null
                        ? "—"
                        : formatIndianMoneyCompact(premTotal)}
                    </td>
                    <td className="px-2 py-1.5 tabular-nums text-zinc-800 dark:text-zinc-200">
                      {formatLegMargin(l, legEntry, spanBaselineLoading)}
                    </td>
                    <td className="px-2 py-1.5">
                      <LegRowActions
                        leg={l}
                        onClone={() =>
                          onLegsChange((prev) => [...prev, cloneLeg(l)])
                        }
                        onDelete={() =>
                          onLegsChange((prev) =>
                            prev.filter((x) => x.id !== l.id),
                          )
                        }
                      />
                    </td>
                  </tr>
                );
              })}
              <tr className="border-t border-zinc-200/80 bg-zinc-50/80 dark:border-zinc-700/80 dark:bg-zinc-900/40">
                <td
                  className="px-2 py-2 text-xs font-semibold uppercase tracking-wide text-zinc-600 dark:text-zinc-300"
                  colSpan={5}
                >
                  Totals
                </td>
                <td
                  className={`px-2 py-2 text-xs font-semibold tabular-nums ${
                    totalsNetPremium < 0
                      ? "text-red-700 dark:text-red-400"
                      : "text-zinc-900 dark:text-zinc-100"
                  }`}
                >
                  {formatIndianMoneyCompact(totalsNetPremium)}
                </td>
                <td className="px-2 py-2 text-xs font-semibold tabular-nums text-zinc-900 dark:text-zinc-100">
                  {!totalsMargin.hasPositiveLots
                    ? "—"
                    : totalsMargin.isFetching || spanBaselineLoading
                      ? "…"
                      : totalsMargin.netMargin != null &&
                          Number.isFinite(totalsMargin.netMargin)
                        ? formatIndianMoneyCompact(totalsMargin.netMargin)
                        : "—"}
                </td>
                <td className="px-2 py-2" />
              </tr>
            </tbody>
          </table>
        </div>
        </>
      )}

      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          disabled={executeDisabled}
          onClick={onExecute}
          className={sb.btnPrimary}
        >
          Execute
        </button>
      </div>
    </section>
  );
}
