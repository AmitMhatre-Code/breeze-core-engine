"use client";

import { useMemo, useState, type ReactNode } from "react";
import { Checkbox } from "@/components/ui/Checkbox";
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
import type { ScaleMode } from "@/lib/strategy-builder/basket-scale";
import type {
  BasketLegMarginEntry,
  OptionRight,
  OrderSide,
  StrategyLeg,
} from "@/lib/strategy-builder/types";

export type BasketScaleControls = {
  mode: ScaleMode;
  onModeChange: (mode: ScaleMode) => void;
  marginModeAvailable: boolean;
  premiumModeAvailable: boolean;
  marginLakh: string;
  onMarginLakhChange: (value: string) => void;
  premiumRupees: string;
  onPremiumRupeesChange: (value: string) => void;
  includeElm: boolean;
  onIncludeElmChange: (value: boolean) => void;
  /** True when a premium target leans on a last-known mid (aggressive/unpriced leg). */
  premiumEstimated: boolean;
  onScale: () => void;
  scaling: boolean;
  disabled: boolean;
  warning: string | null;
};

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
  strikePendingIds,
  onAddLeg,
  onStrikeChange,
  onRightChange,
  onSideChange,
  onPriceChange,
  onAggressiveChange,
  legMargins,
  legBuySellRatios,
  totalsNetPremium,
  totalsMargin,
  onExecute,
  executeDisabled,
  addLegDisabled,
  marginError = null,
  onCalculateMargins,
  calculatingMargins,
  calculateMarginsDisabled,
  scaleControls,
}: {
  sectionLabel: string;
  strikes: number[];
  chainBusy: boolean;
  chainReady: boolean;
  onPickFromChain: () => void;
  lotSize: number;
  legs: StrategyLeg[];
  onLegsChange: (updater: (prev: StrategyLeg[]) => StrategyLeg[]) => void;
  /** Leg ids added via "Add leg" whose strike hasn't been confirmed yet. */
  strikePendingIds: Set<string>;
  onAddLeg: () => void;
  onStrikeChange: (legId: string, strike: number) => void;
  onRightChange: (legId: string, right: OptionRight) => void;
  onSideChange: (legId: string, side: OrderSide) => void;
  onPriceChange: (legId: string, premiumPerUnit: number | undefined) => void;
  onAggressiveChange: (legId: string, checked: boolean) => void;
  legMargins: Record<string, BasketLegMarginEntry>;
  legBuySellRatios: Record<string, number | string | null>;
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
  addLegDisabled: boolean;
  marginError?: string | null;
  onCalculateMargins: () => void;
  calculatingMargins: boolean;
  calculateMarginsDisabled: boolean;
  scaleControls: BasketScaleControls;
}) {
  const activeLegCount = legs.filter((l) => l.lots > 0).length;

  // Default is insertion order (a newly added leg lands at the bottom). Clicking the Strike
  // column header opts into ascending/descending; legs with an unconfirmed strike always sort
  // last regardless of direction, so a freshly added row never jumps mid-list.
  const [strikeSortDir, setStrikeSortDir] = useState<"asc" | "desc" | null>(null);
  const displayedLegs = useMemo(() => {
    if (!strikeSortDir) return legs;
    return [...legs].sort((a, b) => {
      const aPending = strikePendingIds.has(a.id);
      const bPending = strikePendingIds.has(b.id);
      if (aPending !== bPending) return aPending ? 1 : -1;
      if (aPending) return 0;
      return strikeSortDir === "asc" ? a.strike - b.strike : b.strike - a.strike;
    });
  }, [legs, strikeSortDir, strikePendingIds]);

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
                      <button
                        type="button"
                        onClick={() =>
                          setStrikeSortDir((prev) => (prev === "asc" ? "desc" : "asc"))
                        }
                        aria-label={`Sort by strike, ${strikeSortDir === "asc" ? "currently ascending" : strikeSortDir === "desc" ? "currently descending" : "unsorted"}`}
                        className="inline-flex items-center gap-1 transition hover:text-foreground"
                      >
                        Strike
                        <span aria-hidden className="text-[10px] leading-none">
                          {strikeSortDir === "asc" ? "▲" : strikeSortDir === "desc" ? "▼" : "⇅"}
                        </span>
                      </button>
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
                  {displayedLegs.map((l) => {
                    const strikePending = strikePendingIds.has(l.id);
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
                            value={strikePending ? null : l.strike}
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
                          {formatLegMargin(l, legEntry, false)}
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
        {marginError ? (
          <p className="mt-3 app-alert-error text-heading">{marginError}</p>
        ) : null}
      </div>

      {legs.length > 0 ? <BasketScaleRow controls={scaleControls} /> : null}

      <div className="flex flex-wrap items-center justify-between gap-4 border-t border-border-soft bg-panel2 px-[18px] py-3.5">
        <div className="flex flex-wrap items-center gap-6">
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
              totalsMargin.marginBenefit != null && Number.isFinite(totalsMargin.marginBenefit)
                ? formatIndianMoneyCompact(totalsMargin.marginBenefit)
                : "—"
            }
            tone="accent"
          />
          <TotalStat
            label={
              <span className="inline-flex items-center gap-1">
                Basket ELM
                {totalsMargin.elmRequirement != null && totalsMargin.elmIsIndex === false ? (
                  <InfoPopover title="Stock ELM approximate" ariaLabel="Stock ELM approximate help">
                    Uses a flat rate (5%, or 5.25% if deep out-of-the-money). The exchange&apos;s
                    actual ELM for stock options also factors in the underlying&apos;s historical
                    volatility, so the real figure may differ from this estimate.
                  </InfoPopover>
                ) : null}
              </span>
            }
            value={
              !totalsMargin.hasPositiveLots
                ? "—"
                : totalsMargin.isFetching
                  ? "…"
                  : totalsMargin.elmRequirement != null && Number.isFinite(totalsMargin.elmRequirement)
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
            className="inline-flex items-center gap-2 rounded-[10px] bg-accent-strong px-5 py-[11px] text-sm font-bold tracking-[.01em] text-accent-ink transition hover:brightness-[1.06] focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/45 disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50"
          >
            <ExecuteIcon />
            Execute basket · {activeLegCount} leg{activeLegCount === 1 ? "" : "s"}
          </button>
        </div>
      </div>
    </div>
  );
}

function BasketScaleRow({ controls }: { controls: BasketScaleControls }) {
  const {
    mode,
    onModeChange,
    marginModeAvailable,
    premiumModeAvailable,
    marginLakh,
    onMarginLakhChange,
    premiumRupees,
    onPremiumRupeesChange,
    includeElm,
    onIncludeElmChange,
    premiumEstimated,
    onScale,
    scaling,
    disabled,
    warning,
  } = controls;

  const isPremium = mode === "premium";

  return (
    <div className="border-t border-border-soft bg-panel px-[18px] py-3.5">
      <div className="flex flex-wrap items-end gap-x-5 gap-y-3">
        <div className="flex flex-col gap-1.5">
          <span className="text-micro font-bold uppercase tracking-[.06em] text-faint">
            Deploy target across basket
          </span>
          <div
            role="tablist"
            aria-label="Scale basket by"
            className="inline-flex h-9 w-fit rounded-[8px] border border-border p-0.5"
          >
            <ScaleModeTab
              active={mode === "margin"}
              disabled={!marginModeAvailable}
              onClick={() => onModeChange("margin")}
              label="Margin"
            />
            <ScaleModeTab
              active={mode === "premium"}
              disabled={!premiumModeAvailable}
              onClick={() => onModeChange("premium")}
              label="Premium"
            />
          </div>
        </div>

        <label className="flex flex-col gap-1.5">
          <span className="text-micro font-bold uppercase tracking-[.06em] text-faint">
            {isPremium ? "Target premium debit (₹)" : "Target margin (₹ lakh)"}
          </span>
          <input
            type="text"
            inputMode="decimal"
            value={isPremium ? premiumRupees : marginLakh}
            onChange={(e) =>
              isPremium
                ? onPremiumRupeesChange(e.target.value)
                : onMarginLakhChange(e.target.value)
            }
            placeholder={isPremium ? "e.g. 40000" : "e.g. 5"}
            className={`${sb.tableInput} h-9 w-[12ch] tabular-nums`}
          />
        </label>

        {!isPremium ? (
          <label className="mb-2 inline-flex cursor-pointer items-center gap-2 text-xs text-muted">
            <Checkbox
              checked={includeElm}
              onChange={onIncludeElmChange}
              aria-label="Include ELM in margin budget"
            />
            Include ELM
          </label>
        ) : null}

        <button
          type="button"
          disabled={disabled}
          onClick={onScale}
          className={`${sb.btnPrimaryOutline} mb-[1px]`}
        >
          {scaling
            ? "Scaling…"
            : isPremium
              ? "Scale to premium"
              : "Scale to margin"}
        </button>
      </div>

      {isPremium && premiumEstimated ? (
        <p className="mt-2 text-xs text-muted">
          Some legs have no fixed price — premium is estimated from the last-known
          mid, so the actual fill may differ.
        </p>
      ) : null}
      {warning ? (
        <p className="mt-2 rounded-md border border-amber/40 bg-amber-tint p-3 text-sm text-amber-on-tint">
          {warning}
        </p>
      ) : null}
    </div>
  );
}

function ScaleModeTab({
  active,
  disabled,
  onClick,
  label,
}: {
  active: boolean;
  disabled: boolean;
  onClick: () => void;
  label: string;
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      disabled={disabled}
      onClick={onClick}
      className={`rounded-[6px] px-3 py-1.5 text-xs font-semibold transition disabled:cursor-not-allowed disabled:opacity-40 ${
        active
          ? "bg-accent-strong text-accent-ink"
          : "text-muted hover:text-foreground"
      }`}
    >
      {label}
    </button>
  );
}

function TotalStat({
  label,
  value,
  tone = "foreground",
}: {
  label: ReactNode;
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
