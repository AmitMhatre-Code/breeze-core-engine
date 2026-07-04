"use client";

import { QuoteSourceBadge } from "@/components/market-data/QuoteSourceBadge";
import { InfoPopover } from "@/components/strategy-builder/InfoPopover";
import { LegAggressivePriceInput } from "@/components/strategy-builder/LegAggressivePriceInput";
import { LegQuantityInput } from "@/components/strategy-builder/LegQuantityInput";
import { LegQuantityHeader } from "@/components/strategy-builder/LegQuantityHeader";
import { cloneLeg, LegRowActions } from "@/components/strategy-builder/LegRowActions";
import { LegRightToggle, LegSideToggle } from "@/components/strategy-builder/LegToggles";
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

function formatBuySellRatio(raw: number | string | null | undefined): string {
  if (raw == null || raw === "NA") return "—";
  const n = typeof raw === "number" ? raw : parseFloat(String(raw));
  return Number.isFinite(n) ? n.toFixed(4) : "—";
}

export function StrategyLegsPanel({
  sectionTitle = "4. Legs",
  lotSize,
  legs,
  onLegsChange,
  onRightChange,
  onSideChange,
  onPriceChange,
  legMargins,
  spanBaselineLoading = false,
  totalsNetPremium,
  totalsMargin,
  onExecute,
  executeDisabled,
  quoteMeta = null,
}: {
  sectionTitle?: string;
  lotSize: number;
  legs: StrategyLeg[];
  onLegsChange: (updater: (prev: StrategyLeg[]) => StrategyLeg[]) => void;
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
    marginBenefit?: number | null;
  };
  onExecute: () => void;
  executeDisabled: boolean;
  quoteMeta?: QuoteMeta | null;
}) {
  return (
    <section id="strategy-builder-legs" className={`${sb.section} space-y-4`}>
      <h2 className={sb.sectionTitle}>{sectionTitle}</h2>
      {legs.length === 0 ? (
        <p className="text-sm text-muted">
          Select a proposed trade to load legs here.
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
                  <th className="px-2 py-1.5 font-medium">B:S</th>
                  <th className="px-2 py-1.5 font-medium">Premium</th>
                  <th className="px-2 py-1.5 font-medium">
                    <span className="inline-flex items-center gap-1">
                      Margin
                      <InfoPopover title="SPAN margin" ariaLabel="SPAN margin help">
                        Portfolio SPAN from the exchange risk file (scenario scan
                        with hedge benefit). Sell legs show standalone margin; buys
                        are ₹0. Totals include net option value when spot is known.
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
                      <td className="px-2 py-1.5 tabular-nums text-foreground">
                        {l.strike.toLocaleString("en-IN")}
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
                          className={`${sb.tableInput} w-[10ch] min-w-[7rem] max-w-[8rem] tabular-nums`}
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
                      <td className="px-2 py-1.5 tabular-nums text-muted">
                        {formatBuySellRatio(l.buySellRatio)}
                      </td>
                      <td className="px-2 py-1.5 tabular-nums text-foreground">
                        {premTotal == null
                          ? "—"
                          : formatIndianMoneyCompact(premTotal)}
                      </td>
                      <td className="px-2 py-1.5 tabular-nums text-foreground">
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
              </tbody>
            </table>
          </div>
          <div className={`${sb.stickyBar} flex flex-wrap items-center justify-between gap-4`}>
            <div className="flex flex-wrap items-center gap-5">
              <TotalStat
                label="Net premium"
                value={formatIndianMoneyCompact(totalsNetPremium)}
                tone={totalsNetPremium < 0 ? "down" : "foreground"}
              />
              <TotalStat
                label="Net SPAN margin"
                value={
                  !totalsMargin.hasPositiveLots
                    ? "—"
                    : totalsMargin.isFetching || spanBaselineLoading
                      ? "…"
                      : totalsMargin.netMargin != null && Number.isFinite(totalsMargin.netMargin)
                        ? formatIndianMoneyCompact(totalsMargin.netMargin)
                        : "—"
                }
              />
              <TotalStat
                label="Margin benefit"
                value={
                  totalsMargin.marginBenefit != null &&
                  Number.isFinite(totalsMargin.marginBenefit)
                    ? formatIndianMoneyCompact(totalsMargin.marginBenefit)
                    : "—"
                }
                tone="up"
              />
            </div>
            <button
              type="button"
              disabled={executeDisabled}
              onClick={onExecute}
              className={`${sb.btnPrimary} gap-2`}
            >
              <ExecuteIcon />
              Execute strategy · {legs.filter((l) => l.lots > 0).length} leg
              {legs.filter((l) => l.lots > 0).length === 1 ? "" : "s"}
            </button>
          </div>
        </>
      )}
    </section>
  );
}

function TotalStat({
  label,
  value,
  tone = "foreground",
}: {
  label: string;
  value: string;
  tone?: "foreground" | "up" | "down";
}) {
  const toneClass =
    tone === "up" ? "text-up" : tone === "down" ? "text-down" : "text-foreground";
  return (
    <div>
      <div className="text-[12px] font-semibold uppercase tracking-wide text-faint">
        {label}
      </div>
      <div className={`mt-0.5 font-mono text-sm font-semibold tabular-nums ${toneClass}`}>
        {value}
      </div>
    </div>
  );
}

function ExecuteIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
      <polygon points="5 3 19 12 5 21 5 3" />
    </svg>
  );
}
