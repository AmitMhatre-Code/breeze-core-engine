"use client";

import { LegPositionChip } from "@/components/strategy-builder/LegPositionChip";
import { LegQuantityInput } from "@/components/strategy-builder/LegQuantityInput";
import { MarginRefreshIconButton } from "@/components/strategy-builder/MarginRefreshIconButton";
import { InfoPopover } from "@/components/strategy-builder/InfoPopover";
import {
  formatNetPremiumCompactInr,
  formatOptionSymbolLabel,
} from "@/lib/strategy-builder/leg-ui-helpers";
import { sb } from "@/lib/strategy-builder/ui";
import type { StrategyLeg } from "@/lib/strategy-builder/types";
import { formatIndianMoneyCompact } from "@/lib/format-money-in";

export type LegMarginEntry = {
  lots: number;
  span: number | null;
  error?: string;
};

export function StrategyLegsPanel({
  sectionTitle = "4. Legs",
  stockCode,
  expiryDate,
  lotSize,
  legs,
  onLegsChange,
  legMarginCache,
  legMarginFetchingId,
  onFetchLegMargin,
  totalsNetPremium,
  totalsMargin,
  onExecute,
  executeDisabled,
}: {
  sectionTitle?: string;
  stockCode: string;
  expiryDate: string;
  lotSize: number;
  legs: StrategyLeg[];
  onLegsChange: (updater: (prev: StrategyLeg[]) => StrategyLeg[]) => void;
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
}) {
  return (
    <section id="strategy-builder-legs" className={`${sb.section} space-y-4`}>
      <h2 className={sb.sectionTitle}>{sectionTitle}</h2>
      {legs.length === 0 ? (
        <p className="text-sm text-zinc-500 dark:text-zinc-400">
          Select a proposed trade to load legs here.
        </p>
      ) : (
        <>
          <div className="app-table-wrap">
            <table className="w-full min-w-[62rem] border-collapse text-left text-xs">
              <thead className="app-table-head">
                <tr>
                  <th className="px-2 py-1.5 font-medium">Option</th>
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
                      <td className="max-w-[14rem] px-2 py-1.5 text-xs text-zinc-800 dark:text-zinc-200">
                        {formatOptionSymbolLabel(
                          stockCode,
                          expiryDate,
                          l.strike,
                          l.right,
                        )}
                      </td>
                      <td className="px-2 py-1.5">
                        <LegPositionChip side={l.side} />
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
                            onLegsChange((prev) =>
                              prev.map((x) =>
                                x.id === l.id
                                  ? {
                                      ...x,
                                      premiumPerUnit: Number.isFinite(v)
                                        ? v
                                        : undefined,
                                    }
                                  : x,
                              ),
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
                {legs.length > 0 ? (
                  <tr className="border-t border-zinc-200/80 bg-zinc-50/80 dark:border-zinc-700/80 dark:bg-zinc-900/40">
                    <td
                      className="px-2 py-2 text-xs font-semibold uppercase tracking-wide text-zinc-600 dark:text-zinc-300"
                      colSpan={6}
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
                ) : null}
              </tbody>
            </table>
          </div>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              disabled={executeDisabled}
              onClick={onExecute}
              className={sb.btnPrimary}
            >
              Execute Legs
            </button>
          </div>
        </>
      )}
    </section>
  );
}
