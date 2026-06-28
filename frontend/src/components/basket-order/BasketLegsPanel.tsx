"use client";

import type { LegMarginEntry } from "@/components/strategy-builder/StrategyLegsPanel";
import { InfoPopover } from "@/components/strategy-builder/InfoPopover";
import { LegQuantityInput } from "@/components/strategy-builder/LegQuantityInput";
import { MarginRefreshIconButton } from "@/components/strategy-builder/MarginRefreshIconButton";
import { StrikeSelectPill } from "@/components/strategy-builder/StrikeSelectPill";
import { formatNetPremiumCompactInr } from "@/lib/strategy-builder/leg-ui-helpers";
import { sb } from "@/lib/strategy-builder/ui";
import type { OptionRight, OrderSide, StrategyLeg } from "@/lib/strategy-builder/types";
import { formatIndianMoneyCompact } from "@/lib/format-money-in";

function legDeleteLabel(leg: StrategyLeg): string {
  const right = leg.right === "Call" ? "CE" : "PE";
  return `Delete leg ${leg.strike.toLocaleString("en-IN")} ${right} ${leg.side}`;
}

function SegmentedToggle<T extends string>({
  value,
  options,
  onChange,
  ariaLabel,
}: {
  value: T;
  options: { value: T; label: string; activeClass: string }[];
  onChange: (v: T) => void;
  ariaLabel: string;
}) {
  return (
    <div
      className="inline-flex rounded-md bg-zinc-100 p-0.5 ring-1 ring-zinc-200/80 dark:bg-zinc-800/80 dark:ring-zinc-700/80"
      role="group"
      aria-label={ariaLabel}
    >
      {options.map((opt) => (
        <button
          key={opt.value}
          type="button"
          aria-pressed={value === opt.value}
          onClick={() => onChange(opt.value)}
          className={`rounded px-2 py-0.5 text-[11px] font-semibold transition ${
            value === opt.value
              ? opt.activeClass
              : "text-zinc-600 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-200"
          }`}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}

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
  legMarginCache,
  legMarginFetchingId,
  onFetchLegMargin,
  totalsNetPremium,
  totalsMargin,
  onExecute,
  executeDisabled,
  addLegDisabled,
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
  legMarginCache: Record<string, LegMarginEntry>;
  legMarginFetchingId: string | null;
  onFetchLegMargin: (leg: StrategyLeg) => void;
  totalsNetPremium: number;
  totalsMargin: {
    sum: number;
    hasPositiveLots: boolean;
    hasMarginFetchInFlight: boolean;
    hasMissingFreshMargin: boolean;
  };
  onExecute: () => void;
  executeDisabled: boolean;
  addLegDisabled: boolean;
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
        <div className="app-table-wrap">
          <table className="w-full min-w-[72rem] border-collapse text-left text-xs">
            <thead className="app-table-head">
              <tr>
                <th className="px-2 py-1.5 font-medium">Strike</th>
                <th className="px-2 py-1.5 font-medium">Type</th>
                <th className="px-2 py-1.5 font-medium">Position</th>
                <th className="px-2 py-1.5 font-medium">Quantity</th>
                <th className="px-2 py-1.5 font-medium">Lot Size</th>
                <th className="px-2 py-1.5 font-medium">
                  <span className="inline-flex items-center gap-1">
                    Aggressive
                    <InfoPopover title="Aggressive limit" ariaLabel="Aggressive limit help">
                      ICICI sets the limit price from LTP. No manual price needed.
                    </InfoPopover>
                  </span>
                </th>
                <th className="px-2 py-1.5 font-medium">Price</th>
                <th className="px-2 py-1.5 font-medium">Premium</th>
                <th className="px-2 py-1.5 font-medium">Margin / Lot</th>
                <th className="px-2 py-1.5 font-medium">Margin</th>
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
                const legEntry = legMarginCache[l.id];
                const legMarginFresh = legEntry != null && legEntry.lots === l.lots;
                const marginPerLot =
                  legMarginFresh &&
                  legEntry.span != null &&
                  Number.isFinite(legEntry.span) &&
                  l.lots > 0
                    ? legEntry.span / l.lots
                    : null;
                return (
                  <tr key={l.id} className="app-table-row">
                    <td className="px-2 py-1.5">
                      <StrikeSelectPill
                        strikes={strikes}
                        value={l.strike}
                        onChange={(strike) => onStrikeChange(l.id, strike)}
                        busy={chainBusy}
                        layout="toolbar"
                        tone="default"
                        hideLabel
                        rootClassName="min-w-[7rem]"
                      />
                    </td>
                    <td className="px-2 py-1.5">
                      <SegmentedToggle
                        value={l.right}
                        ariaLabel="Call or Put"
                        options={[
                          {
                            value: "Call" as const,
                            label: "CE",
                            activeClass:
                              "bg-sky-100 text-sky-900 dark:bg-sky-950/50 dark:text-sky-300",
                          },
                          {
                            value: "Put" as const,
                            label: "PE",
                            activeClass:
                              "bg-violet-100 text-violet-900 dark:bg-violet-950/50 dark:text-violet-300",
                          },
                        ]}
                        onChange={(right) => onRightChange(l.id, right)}
                      />
                    </td>
                    <td className="px-2 py-1.5">
                      <SegmentedToggle
                        value={l.side}
                        ariaLabel="Buy or Sell"
                        options={[
                          {
                            value: "Buy" as const,
                            label: "Buy",
                            activeClass:
                              "bg-emerald-100 text-emerald-900 dark:bg-emerald-950/50 dark:text-emerald-300",
                          },
                          {
                            value: "Sell" as const,
                            label: "Sell",
                            activeClass:
                              "bg-red-100 text-red-900 dark:bg-red-950/50 dark:text-red-300",
                          },
                        ]}
                        onChange={(side) => onSideChange(l.id, side)}
                      />
                    </td>
                    <td className="px-2 py-1.5">
                      <LegQuantityInput
                        legId={l.id}
                        lots={l.lots}
                        lotSize={lotSize}
                        onLotsChange={(newLots) =>
                          onLegsChange((prev) =>
                            prev.map((x) =>
                              x.id === l.id ? { ...x, lots: newLots } : x,
                            ),
                          )
                        }
                        className={`${sb.tableInput} w-[7.5rem] tabular-nums`}
                      />
                    </td>
                    <td className="px-2 py-1.5 tabular-nums text-zinc-800 dark:text-zinc-200">
                      {lotSize.toLocaleString("en-IN")}
                    </td>
                    <td className="px-2 py-1.5">
                      <input
                        type="checkbox"
                        className="size-4 rounded border-zinc-300 text-emerald-600 focus:ring-emerald-500/40 dark:border-zinc-600"
                        checked={aggressive}
                        aria-label={`Aggressive limit for ${l.strike} ${l.right}`}
                        onChange={(e) => {
                          const checked = e.target.checked;
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
                          );
                        }}
                      />
                    </td>
                    <td className="px-2 py-1.5">
                      <input
                        type="number"
                        min={0}
                        step={0.05}
                        disabled={aggressive}
                        aria-label="Limit price per unit"
                        className={`${sb.tableInput} w-[6rem] tabular-nums disabled:cursor-not-allowed disabled:opacity-50`}
                        value={
                          aggressive
                            ? ""
                            : l.premiumPerUnit != null
                              ? l.premiumPerUnit
                              : ""
                        }
                        onChange={(e) => {
                          const v = parseFloat(e.target.value);
                          onPriceChange(
                            l.id,
                            Number.isFinite(v) ? v : undefined,
                          );
                        }}
                      />
                    </td>
                    <td className="px-2 py-1.5 tabular-nums text-zinc-800 dark:text-zinc-200">
                      {premTotal == null
                        ? "—"
                        : formatIndianMoneyCompact(premTotal)}
                    </td>
                    <td className="px-2 py-1.5 tabular-nums text-zinc-800 dark:text-zinc-200">
                      {l.lots <= 0 ? (
                        "—"
                      ) : legMarginFetchingId === l.id ? (
                        "…"
                      ) : marginPerLot != null && Number.isFinite(marginPerLot) ? (
                        formatIndianMoneyCompact(marginPerLot)
                      ) : legMarginFresh && legEntry?.error ? (
                        "—"
                      ) : (
                        <MarginRefreshIconButton
                          label="Fetch margin for this leg"
                          onClick={() => onFetchLegMargin(l)}
                        />
                      )}
                    </td>
                    <td className="px-2 py-1.5 tabular-nums text-zinc-800 dark:text-zinc-200">
                      {l.lots <= 0 ? (
                        "—"
                      ) : legMarginFetchingId === l.id ? (
                        "…"
                      ) : legMarginFresh &&
                        legEntry?.span != null &&
                        Number.isFinite(legEntry.span) ? (
                        formatIndianMoneyCompact(legEntry.span)
                      ) : legMarginFresh && legEntry?.error ? (
                        legEntry.error
                      ) : (
                        <MarginRefreshIconButton
                          label="Fetch margin for this leg"
                          onClick={() => onFetchLegMargin(l)}
                        />
                      )}
                    </td>
                    <td className="px-2 py-1.5">
                      <button
                        type="button"
                        className="text-red-600 dark:text-red-400"
                        aria-label={legDeleteLabel(l)}
                        onClick={() =>
                          onLegsChange((prev) =>
                            prev.filter((x) => x.id !== l.id),
                          )
                        }
                      >
                        Delete
                      </button>
                    </td>
                  </tr>
                );
              })}
              <tr className="border-t border-zinc-200/80 bg-zinc-50/80 dark:border-zinc-700/80 dark:bg-zinc-900/40">
                <td
                  className="px-2 py-2 text-xs font-semibold uppercase tracking-wide text-zinc-600 dark:text-zinc-300"
                  colSpan={7}
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
                  {formatNetPremiumCompactInr(totalsNetPremium)}
                </td>
                <td className="px-2 py-2 text-xs text-zinc-500 dark:text-zinc-400">
                  —
                </td>
                <td className="px-2 py-2 text-xs font-semibold tabular-nums text-zinc-900 dark:text-zinc-100">
                  {!totalsMargin.hasPositiveLots
                    ? "—"
                    : totalsMargin.hasMarginFetchInFlight
                      ? "…"
                      : totalsMargin.hasMissingFreshMargin
                        ? "—"
                        : formatIndianMoneyCompact(totalsMargin.sum)}
                </td>
                <td className="px-2 py-2" />
              </tr>
            </tbody>
          </table>
        </div>
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
