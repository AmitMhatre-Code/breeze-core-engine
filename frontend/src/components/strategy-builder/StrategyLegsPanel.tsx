"use client";

import type { ReactNode } from "react";
import { InfoPopover } from "@/components/ui/InfoPopover";
import { LegAggressivePriceInput } from "@/components/shared/legs/LegAggressivePriceInput";
import { LegQuantityInput } from "@/components/shared/legs/LegQuantityInput";
import { LegQuantityHeader } from "@/components/shared/legs/LegQuantityHeader";
import { cloneLeg, LegRowActions } from "@/components/shared/legs/LegRowActions";
import { LegRightToggle, LegSideToggle } from "@/components/shared/legs/LegToggles";
import { StrikeSelectPill } from "@/components/shared/order/StrikeSelectPill";
import { formatIndianMoneyCompact } from "@/lib/format-money-in";
import {
  formatBuySellRatio,
  formatLegMargin,
  formatSignedLegPremium,
} from "@/lib/strategy-builder/leg-ui-helpers";
import { sb } from "@/lib/strategy-builder/ui";
import type {
  BasketLegMarginEntry,
  OptionRight,
  OrderSide,
  StrategyLeg,
} from "@/lib/strategy-builder/types";

export function StrategyLegsPanel({
  sectionTitle = "4. Legs",
  lotSize,
  legs,
  onLegsChange,
  onAddLeg,
  strikes,
  chainBusy,
  onStrikeChange,
  onRightChange,
  onSideChange,
  onPriceChange,
  onAggressiveChange,
  legMargins,
  totalsNetPremium,
  totalsMargin,
  onExecute,
  executeDisabled,
  marginWarnings,
  onCalculateMargins,
  calculatingMargins,
  calculateMarginsDisabled,
}: {
  sectionTitle?: string;
  lotSize: number;
  legs: StrategyLeg[];
  onLegsChange: (updater: (prev: StrategyLeg[]) => StrategyLeg[]) => void;
  onAddLeg?: () => void;
  strikes: number[];
  chainBusy: boolean;
  onStrikeChange: (legId: string, strike: number) => void;
  onRightChange: (legId: string, right: OptionRight) => void;
  onSideChange: (legId: string, side: OrderSide) => void;
  onPriceChange: (legId: string, premiumPerUnit: number | undefined) => void;
  onAggressiveChange: (legId: string, checked: boolean) => void;
  legMargins: Record<string, BasketLegMarginEntry>;
  totalsNetPremium: number;
  totalsMargin: {
    hasPositiveLots: boolean;
    isFetching: boolean;
    netMargin: number | null;
    marginBenefit?: number | null;
    elmRequirement?: number | null;
    elmIsIndex?: boolean;
    elmApproximate?: boolean;
  };
  onExecute: () => void;
  executeDisabled: boolean;
  marginWarnings?: string[];
  onCalculateMargins: () => void;
  calculatingMargins: boolean;
  calculateMarginsDisabled: boolean;
}) {
  const sortedLegs = [...legs].sort((a, b) => a.strike - b.strike);
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className={sb.sectionTitle}>{sectionTitle}</h2>
        {onAddLeg && legs.length > 0 ? (
          <button
            type="button"
            onClick={onAddLeg}
            className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-accent-strong bg-transparent px-3 py-1.5 text-xs font-bold text-accent-strong transition hover:bg-accent-strong hover:text-accent-ink"
          >
            <PlusIcon />
            Add leg
          </button>
        ) : null}
      </div>
      {legs.length === 0 ? (
        <p className="text-sm text-muted">
          Select a proposed trade to load legs here.
        </p>
      ) : (
        <>
          <div className="-mx-5 overflow-x-auto">
            <table className="w-full min-w-[52rem] border-collapse text-left text-xs">
              <thead className="app-table-head">
                <tr>
                  <th className="py-1.5 pl-5 pr-2 font-medium">Strike</th>
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
                  <th className="py-1.5 pl-2 pr-5 font-medium" />
                </tr>
              </thead>
              <tbody>
                {sortedLegs.map((l) => {
                  const qtyU = l.lots > 0 ? Math.round(l.lots * lotSize) : 0;
                  const aggressive = l.aggressiveLimit ?? false;
                  const premTotal = aggressive
                    ? null
                    : (l.premiumPerUnit ?? 0) * qtyU;
                  const legEntry = legMargins[l.id];
                  return (
                    <tr key={l.id} className="app-table-row">
                      <td className="max-w-[8rem] py-1.5 pl-5 pr-2">
                        <StrikeSelectPill
                          strikes={strikes}
                          value={l.strike}
                          onChange={(strike) => onStrikeChange(l.id, strike)}
                          busy={chainBusy && strikes.length === 0}
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
                            onAggressiveChange(l.id, checked)
                          }
                          onPriceChange={(premiumPerUnit) =>
                            onPriceChange(l.id, premiumPerUnit)
                          }
                        />
                      </td>
                      <td className="px-2 py-1.5 tabular-nums text-muted">
                        {formatBuySellRatio(l.buySellRatio)}
                      </td>
                      <td
                        className={`px-2 py-1.5 tabular-nums ${formatSignedLegPremium(premTotal, l.side).toneClass}`}
                      >
                        {formatSignedLegPremium(premTotal, l.side).text}
                      </td>
                      <td className="px-2 py-1.5 tabular-nums text-muted">
                        {formatLegMargin(l, legEntry, false)}
                      </td>
                      <td className="py-1.5 pl-2 pr-5">
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
          <div className="-mx-5 flex flex-wrap items-center justify-between gap-4 border-t border-border-soft bg-panel2 px-5 py-3.5">
            <div className="flex flex-wrap items-center gap-5">
              <TotalStat
                label="Net premium"
                value={formatIndianMoneyCompact(totalsNetPremium)}
                tone={totalsNetPremium < 0 ? "down" : totalsNetPremium > 0 ? "up" : "foreground"}
              />
              <TotalStat
                label="Net SPAN margin"
                value={
                  !totalsMargin.hasPositiveLots
                    ? "—"
                    : totalsMargin.isFetching
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
              <TotalStat
                label={
                  <span className="inline-flex items-center gap-1">
                    Basket ELM
                    {totalsMargin.elmRequirement != null && totalsMargin.elmIsIndex === false ? (
                      <InfoPopover title="Stock ELM approximate" ariaLabel="Stock ELM approximate help">
                        Uses a flat rate (5%, or 5.25% if deep out-of-the-money). The
                        exchange&apos;s actual ELM for stock options also factors in the
                        underlying&apos;s historical volatility, so the real figure may differ
                        from this estimate.
                      </InfoPopover>
                    ) : null}
                  </span>
                }
                value={
                  !totalsMargin.hasPositiveLots
                    ? "—"
                    : totalsMargin.isFetching
                      ? "…"
                      : totalsMargin.elmRequirement != null &&
                          Number.isFinite(totalsMargin.elmRequirement)
                        ? formatIndianMoneyCompact(totalsMargin.elmRequirement)
                        : "—"
                }
              />
            </div>
            <div className="flex items-center gap-2.5">
              <button
                type="button"
                disabled={calculateMarginsDisabled}
                onClick={onCalculateMargins}
                className={sb.btnPrimaryOutline}
              >
                {calculatingMargins ? "Calculating…" : "Calculate Margins"}
              </button>
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
          </div>
          {marginWarnings && marginWarnings.length > 0 ? (
            <p className="app-alert-error text-heading">{marginWarnings[0]}</p>
          ) : null}
        </>
      )}
    </div>
  );
}

function TotalStat({
  label,
  value,
  tone = "foreground",
}: {
  label: ReactNode;
  value: string;
  tone?: "foreground" | "up" | "down";
}) {
  const toneClass =
    tone === "up" ? "text-up" : tone === "down" ? "text-down" : "text-foreground";
  return (
    <div>
      <div className="text-body font-semibold uppercase tracking-wide text-faint">
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

function PlusIcon() {
  return (
    <svg
      width="13"
      height="13"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.4"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M12 5v14M5 12h14" />
    </svg>
  );
}
