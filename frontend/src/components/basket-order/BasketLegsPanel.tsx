"use client";

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

const thCls =
  "px-2.5 py-2 text-left text-micro font-bold uppercase tracking-[.06em] text-faint";
const thClsEnd = `${thCls} text-right`;

export function BasketLegsPanel({
  sectionLabel,
  strikes,
  chainBusy,
  chainReady,
  onPickFromChain,
  lotSize,
  legs,
  onLegsChange,
  onAddLeg,
  onStrikeChange,
  onRightChange,
  onSideChange,
  onPriceChange,
  onAggressiveChange,
  legMargins,
  legBuySellRatios,
  spanBaselineLoading = false,
  totalsNetPremium,
  totalsMargin,
  onExecute,
  executeDisabled,
  addLegDisabled,
  marginError = null,
  marginWarnings = [],
}: {
  sectionLabel: string;
  strikes: number[];
  chainBusy: boolean;
  chainReady: boolean;
  onPickFromChain: () => void;
  lotSize: number;
  legs: StrategyLeg[];
  onLegsChange: (updater: (prev: StrategyLeg[]) => StrategyLeg[]) => void;
  onAddLeg: () => void;
  onStrikeChange: (legId: string, strike: number) => void;
  onRightChange: (legId: string, right: OptionRight) => void;
  onSideChange: (legId: string, side: OrderSide) => void;
  onPriceChange: (legId: string, premiumPerUnit: number | undefined) => void;
  onAggressiveChange: (legId: string, checked: boolean) => void;
  legMargins: Record<string, BasketLegMarginEntry>;
  legBuySellRatios: Record<string, number | string | null>;
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
  addLegDisabled: boolean;
  marginError?: string | null;
  marginWarnings?: string[];
}) {
  const activeLegCount = legs.filter((l) => l.lots > 0).length;
  const sortedLegs = [...legs].sort((a, b) => a.strike - b.strike);

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border-soft px-[18px] py-3.5">
        <span className="text-hint font-bold uppercase tracking-[.07em] text-faint">
          {sectionLabel}
        </span>
        <div className="flex flex-col items-end gap-1">
          <button
            type="button"
            disabled={addLegDisabled}
            onClick={onAddLeg}
            className="inline-flex items-center gap-1.5 rounded-[7px] border border-accent/40 bg-transparent px-3 py-[7px] text-xs font-semibold text-accent-strong transition hover:bg-accent-tint focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/25 disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50"
          >
            <PlusIcon />
            Add leg
          </button>
          <span className="text-xs text-muted">or pick from{" "}
            <button
            type="button"
            disabled={!chainReady}
            onClick={onPickFromChain}
            className="text-xs text-accent-strong underline underline-offset-2 hover:text-accent disabled:cursor-not-allowed disabled:no-underline disabled:opacity-50"
            >
            options chain
            </button>
          </span>
        </div>
      </div>

      <div className="px-[18px] py-4">
        {!chainReady ? (
          <p className="text-sm text-muted">
            {chainBusy ? "Loading contract details…" : "Waiting for option chain data…"}
          </p>
        ) : null}

        {legs.length === 0 ? (
          <p className="text-sm text-muted">No legs added yet</p>
        ) : (
          <>
            <div className="-mx-[18px] overflow-x-auto">
              <table className="w-full min-w-[58rem] border-collapse text-left text-xs">
                <thead>
                  <tr className="border-b border-border-soft">
                    <th className="py-2 pl-[18px] pr-2.5 text-left text-micro font-bold uppercase tracking-[.06em] text-faint">
                      Strike
                    </th>
                    <th className={thCls}>Type</th>
                    <th className={thCls}>Position</th>
                    <LegQuantityHeader />
                    <th className={thCls}>Price ₹</th>
                    <th className={thClsEnd}>B:S</th>
                    <th className={thClsEnd}>Premium</th>
                    <th className={thClsEnd}>
                      <span className="inline-flex items-center justify-end gap-1">
                        Margin
                        <InfoPopover title="SPAN margin" ariaLabel="SPAN margin help">
                          Approximate margin from the exchange SPAN file for the quantity
                          entered.
                        </InfoPopover>
                      </span>
                    </th>
                    <th className="w-16" />
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
                      <tr
                        key={l.id}
                        className="relative z-0 border-b border-border-soft [&:has([aria-expanded=true])]:z-[300]"
                      >
                        <td className="max-w-[8rem] py-2 pl-[18px] pr-2.5">
                          <StrikeSelectPill
                            strikes={strikes}
                            value={l.strike}
                            onChange={(strike) => onStrikeChange(l.id, strike)}
                            busy={chainBusy && strikes.length === 0}
                            layout="table"
                            hideLabel
                          />
                        </td>
                        <td className="px-2.5 py-2">
                          <LegRightToggle
                            value={l.right}
                            onChange={(right) => onRightChange(l.id, right)}
                          />
                        </td>
                        <td className="px-2.5 py-2">
                          <LegSideToggle
                            value={l.side}
                            onChange={(side) => onSideChange(l.id, side)}
                          />
                        </td>
                        <td className="px-2.5 py-2">
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
                        <td className="px-2.5 py-2">
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
                        <td className="px-2.5 py-2 text-right font-mono tabular-nums text-muted">
                          {formatBuySellRatio(legBuySellRatios[l.id])}
                        </td>
                        <td
                          className={`px-2.5 py-2 text-right font-mono tabular-nums ${formatSignedLegPremium(premTotal, l.side).toneClass}`}
                        >
                          {formatSignedLegPremium(premTotal, l.side).text}
                        </td>
                        <td className="px-2.5 py-2 text-right font-mono tabular-nums text-muted">
                          {formatLegMargin(l, legEntry, spanBaselineLoading)}
                        </td>
                        <td className="py-2 pl-2.5 pr-[18px] text-center">
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
          </>
        )}
        {marginWarnings.length > 0 ? (
          <p className="mt-3 app-alert-error text-heading">{marginWarnings[0]}</p>
        ) : marginError ? (
          <p className="mt-3 app-alert-error text-heading">{marginError}</p>
        ) : null}
      </div>

      <div className="flex flex-wrap items-center justify-between gap-4 border-t border-border-soft bg-panel2 px-[18px] py-3.5">
        <div className="flex flex-wrap items-center gap-6">
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
              totalsMargin.marginBenefit != null && Number.isFinite(totalsMargin.marginBenefit)
                ? formatIndianMoneyCompact(totalsMargin.marginBenefit)
                : "—"
            }
            tone="accent"
          />
        </div>
        <button
          type="button"
          disabled={executeDisabled}
          onClick={onExecute}
          className="inline-flex items-center gap-2 rounded-[10px] bg-accent-strong px-5 py-[11px] text-sm font-bold tracking-[.01em] text-accent-ink transition hover:brightness-[1.06] focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/45 disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50"
        >
          <ExecuteIcon />
          Execute basket · {activeLegCount} leg{activeLegCount === 1 ? "" : "s"}
        </button>
      </div>
    </div>
  );
}

function TotalStat({
  label,
  value,
  tone = "foreground",
}: {
  label: string;
  value: string;
  tone?: "foreground" | "up" | "down" | "accent";
}) {
  const toneClass =
    tone === "up"
      ? "text-up"
      : tone === "down"
        ? "text-down"
        : tone === "accent"
          ? "text-accent-strong"
          : "text-foreground";
  return (
    <div>
      <div className="text-micro font-bold uppercase tracking-[.06em] text-faint">
        {label}
      </div>
      <div className={`mt-0.5 font-mono text-[17px] font-bold tabular-nums ${toneClass}`}>
        {value}
      </div>
    </div>
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

function ExecuteIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="currentColor"
      aria-hidden
    >
      <polygon points="5 3 19 12 5 21 5 3" />
    </svg>
  );
}
