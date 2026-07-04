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
    marginBenefit?: number | null;
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
            className="inline-flex items-center justify-center rounded-lg border border-accent/40 bg-transparent px-3.5 py-2 text-xs font-semibold text-accent-strong transition hover:bg-accent-tint focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/25 disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50"
          >
            Add leg
          </button>
        </div>
      </div>

      {!chainReady ? (
        <p className="text-sm text-muted">
          {chainBusy ? "Loading contract details…" : "Waiting for option chain data…"}
        </p>
      ) : null}

      {legs.length === 0 ? (
        <p className="text-sm text-muted">
          Click <span className="font-medium text-foreground">Add leg</span> to
          build your basket manually, or{" "}
          {showOptionChain ? (
            "pick Buy/Sell from the option chain above."
          ) : (
            <>
              <button
                type="button"
                disabled={!chainReady}
                onClick={onShowOptionChain}
                className="font-medium text-accent-strong underline underline-offset-2 hover:text-accent disabled:cursor-not-allowed disabled:no-underline disabled:opacity-50"
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
                  <tr
                    key={l.id}
                    className="app-table-row relative z-0 [&:has([aria-expanded=true])]:z-[300]"
                  >
                    <td className="max-w-[8rem] px-2 py-1.5">
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
                    <td className="px-2 py-1.5 font-mono tabular-nums text-foreground">
                      {premTotal == null
                        ? "—"
                        : formatIndianMoneyCompact(premTotal)}
                    </td>
                    <td className="px-2 py-1.5 font-mono tabular-nums text-foreground">
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
        </>
      )}

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
              totalsMargin.marginBenefit != null && Number.isFinite(totalsMargin.marginBenefit)
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
          Execute basket · {legs.filter((l) => l.lots > 0).length} leg
          {legs.filter((l) => l.lots > 0).length === 1 ? "" : "s"}
        </button>
      </div>
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
