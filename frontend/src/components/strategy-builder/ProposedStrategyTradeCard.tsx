"use client";

import { useMemo } from "react";
import { InfinitySymbol } from "@/components/shared/payoff/InfinitySymbol";
import type { SigmaSmiles } from "@/lib/strategy-builder/chainIv";
import { formatIndianMoneyCompact } from "@/lib/format-money-in";
import { strategyOutlook } from "@/lib/strategy-builder/templates";
import {
  computeTradePop,
  isUnlimitedMaxLoss,
  isUnlimitedMaxProfit,
} from "@/lib/strategy-builder/trade-metrics";
import type { Outlook, ProposedTrade } from "@/lib/strategy-builder/types";

const OUTLOOK_BADGE_TONE: Record<
  Outlook,
  { text: string; tint: string; solidBg: string; solidText: string }
> = {
  neutral: {
    text: "text-accent-strong",
    tint: "bg-accent-tint",
    solidBg: "bg-accent",
    solidText: "text-accent-ink",
  },
  bullish: {
    text: "text-up",
    tint: "bg-up-tint",
    solidBg: "bg-up",
    solidText: "text-accent-ink",
  },
  bearish: {
    text: "text-down",
    tint: "bg-down-tint",
    solidBg: "bg-down",
    solidText: "text-accent-ink",
  },
  volatile: {
    text: "text-gtt",
    tint: "bg-gtt-tint",
    solidBg: "bg-gtt",
    solidText: "text-accent-ink",
  },
};

function legPart(leg: ProposedTrade["legs"][number]): string {
  const abbr = leg.right === "Put" ? "PE" : "CE";
  return `${leg.side} ${leg.strike.toLocaleString("en-IN")} ${abbr}`;
}

/** 4-leg strategies (e.g. Iron Condor) pair into 2 wing lines: "Sell X PE / Buy Y PE"
 * and "Sell A CE / Buy B CE". 2-leg strategies get one line per leg. Single-leg
 * strategies (Naked CE/PE Short) get a padding blank line so every card is 2 leg-lines
 * tall, keeping tile heights consistent across 1/2/4-leg strategies. */
function legLines(legs: ProposedTrade["legs"]): string[] {
  if (legs.length === 1) {
    return [legPart(legs[0]), " "];
  }
  if (legs.length !== 4) {
    return legs.map(legPart);
  }
  const puts = legs.filter((l) => l.right === "Put");
  const calls = legs.filter((l) => l.right === "Call");
  const lines: string[] = [];
  if (puts.length) lines.push(puts.map(legPart).join(" / "));
  if (calls.length) lines.push(calls.map(legPart).join(" / "));
  return lines;
}

function Stat({
  label,
  value,
  tone = "foreground",
}: {
  label: string;
  value: React.ReactNode;
  tone?: "up" | "down" | "foreground";
}) {
  const toneClass =
    tone === "up" ? "text-up" : tone === "down" ? "text-down" : "text-foreground";
  return (
    <div>
      <div className="text-body font-bold uppercase tracking-wide text-faint">
        {label}
      </div>
      <div className={`font-mono text-sm font-bold tabular-nums ${toneClass}`}>
        {value}
      </div>
    </div>
  );
}

export function ProposedStrategyTradeCard({
  trade,
  lotSize,
  spot,
  atmIv,
  sigmaSmiles = null,
  expiryDate,
  selected,
  onSelect,
}: {
  trade: ProposedTrade;
  lotSize: number;
  spot: number | null;
  atmIv: number | null;
  /** Per-strike IV smile (skew-aware); falls back to flat `atmIv` where a strike lacks trusted anchors. */
  sigmaSmiles?: SigmaSmiles | null;
  expiryDate: string;
  selected: boolean;
  onSelect: () => void;
}) {
  const skipped = trade.status === "skipped";
  const relaxed = trade.compliance === "relaxed";
  const outlook = strategyOutlook(trade.strategy_id);
  const tone = outlook ? OUTLOOK_BADGE_TONE[outlook] : null;

  const pop = useMemo(
    () => computeTradePop(trade, spot, atmIv, expiryDate, lotSize, sigmaSmiles),
    [trade, spot, atmIv, expiryDate, lotSize, sigmaSmiles],
  );

  if (skipped) {
    return (
      <div className="w-full min-w-0 rounded-[12px] border border-border-soft bg-panel2 p-3.5 opacity-70">
        <span className="text-sm font-bold text-foreground">
          {trade.strategy_name}
        </span>
        <p className="mt-1 text-xs text-muted">
          {trade.skip_reason ?? "Not available"}
        </p>
      </div>
    );
  }

  return (
    <div
      className={`w-full min-w-0 rounded-[12px] border p-3.5 transition ${
        relaxed
          ? selected
            ? "border-amber-accent bg-amber-tint"
            : "border-amber-accent/40 bg-amber-tint/60 hover:border-amber-accent/70"
          : selected
            ? "border-[1.5px] border-accent bg-accent-tint"
            : "border-border bg-panel hover:border-accent/40"
      }`}
    >
      <div className="flex flex-col gap-2.5">
        <div className="flex items-center justify-between">
          <span className="text-sm font-bold text-foreground">
            {trade.strategy_name}
          </span>
          {tone ? (
            <span
              className={`shrink-0 rounded-[5px] px-1.5 py-0.5 text-body font-bold uppercase tracking-wide ${
                selected && !relaxed
                  ? `${tone.solidBg} ${tone.solidText}`
                  : `${tone.tint} ${tone.text}`
              }`}
            >
              {outlook}
            </span>
          ) : null}
        </div>

        <div className="font-mono text-heading leading-[1.7] text-muted">
          {legLines(trade.legs).map((line, i) => (
            <div key={i}>{line}</div>
          ))}
        </div>

        <div className="grid grid-cols-2 gap-2 border-t border-border-soft pt-2.5">
          <Stat
            label="Max profit"
            tone="up"
            value={
              isUnlimitedMaxProfit(trade.max_profit) ? (
                <InfinitySymbol />
              ) : trade.max_profit != null ? (
                formatIndianMoneyCompact(trade.max_profit)
              ) : (
                "—"
              )
            }
          />
          <Stat
            label="Max loss"
            tone="down"
            value={
              isUnlimitedMaxLoss(trade.max_loss) ? (
                <InfinitySymbol />
              ) : trade.max_loss != null ? (
                formatIndianMoneyCompact(trade.max_loss)
              ) : (
                "—"
              )
            }
          />
          <Stat
            label="POP"
            value={pop != null ? `${pop.toFixed(1)}%` : "—"}
          />
          <Stat
            label="Margin"
            value={
              trade.span_margin != null
                ? formatIndianMoneyCompact(trade.span_margin)
                : "—"
            }
          />
        </div>
      </div>

      <button
        type="button"
        className={
          selected
            ? "mt-2.5 w-full cursor-default rounded-lg bg-accent-strong py-2 text-heading font-bold text-accent-ink"
            : "mt-2.5 w-full rounded-lg border border-accent-strong bg-transparent py-2 text-heading font-bold text-accent-strong transition hover:bg-accent-strong hover:text-accent-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
        }
        onClick={onSelect}
        aria-pressed={selected}
      >
        {selected ? "Selected trade" : "Select trade"}
      </button>
    </div>
  );
}
