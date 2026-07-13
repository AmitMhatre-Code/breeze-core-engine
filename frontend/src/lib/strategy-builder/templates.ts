import type { Outlook } from "@/lib/strategy-builder/types";

export type TemplateId =
  | "bull_call_spread"
  | "bear_call_spread"
  | "bear_put_spread"
  | "bull_put_spread"
  | "long_straddle"
  | "short_straddle"
  | "long_strangle"
  | "short_strangle"
  | "long_call_butterfly"
  | "iron_condor"
  | "iron_butterfly";

export type StrategyTemplateMeta = {
  id: TemplateId;
  label: string;
  outlook: Outlook[];
  description: string;
  naked?: boolean;
};

export function outlookPillLabel(o: Outlook): string {
  switch (o) {
    case "bullish":
      return "Bullish";
    case "bearish":
      return "Bearish";
    case "neutral":
      return "Neutral";
    case "volatile":
      return "Volatility";
  }
}

export function outlookIconFillClass(o: Outlook): string {
  switch (o) {
    case "bullish":
      return "text-up";
    case "bearish":
      return "text-down";
    case "neutral":
      return "text-accent-strong";
    case "volatile":
      return "text-gtt";
  }
}

export function outlookPillClassName(o: Outlook): string {
  switch (o) {
    case "bullish":
      return "bg-up-tint text-up";
    case "bearish":
      return "bg-down-tint text-down";
    case "neutral":
      return "bg-accent-tint text-accent-strong";
    case "volatile":
      return "bg-gtt-tint text-gtt";
  }
}

/** Text-label pill toggle for outlook filters. */
export function outlookFilterBtnClassName(o: Outlook, on: boolean): string {
  const base =
    "inline-flex shrink-0 items-center justify-center rounded-md border px-2.5 py-1.5 text-hint font-semibold transition focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/40";
  if (!on) {
    return `${base} border-border bg-transparent text-muted hover:bg-panel2`;
  }
  return `${base} border-accent/40 bg-accent-tint text-accent-strong`;
}

export const STRATEGY_TEMPLATES: StrategyTemplateMeta[] = [
  {
    id: "bull_call_spread",
    label: "Bull Call Spread",
    outlook: ["bullish"],
    description: "Buy lower CE, sell higher CE",
  },
  {
    id: "bull_put_spread",
    label: "Bull Put Spread",
    outlook: ["bullish"],
    description: "Sell higher PE, buy lower PE",
  },
  {
    id: "bear_call_spread",
    label: "Bear Call Spread",
    outlook: ["bearish"],
    description: "Sell lower CE, buy higher CE",
  },
  {
    id: "bear_put_spread",
    label: "Bear Put Spread",
    outlook: ["bearish"],
    description: "Buy higher PE, sell lower PE",
  },
  {
    id: "long_straddle",
    label: "Long Straddle",
    outlook: ["volatile"],
    description: "Buy ATM CE and PE",
  },
  {
    id: "long_strangle",
    label: "Long Strangle",
    outlook: ["volatile"],
    description: "Buy OTM CE and PE",
  },
  {
    id: "short_straddle",
    label: "Short Straddle",
    outlook: ["neutral"],
    description: "Sell ATM CE and PE",
    naked: true,
  },
  {
    id: "short_strangle",
    label: "Short Strangle",
    outlook: ["neutral"],
    description: "Sell OTM CE and PE",
  },
  {
    id: "long_call_butterfly",
    label: "Long Call Butterfly",
    outlook: ["neutral"],
    description: "Long outer calls + short mid calls",
  },
  {
    id: "iron_condor",
    label: "Iron Condor",
    outlook: ["neutral"],
    description: "Short inner CE/PE spreads, long wings",
  },
  {
    id: "iron_butterfly",
    label: "Iron Butterfly",
    outlook: ["neutral"],
    description: "Short ATM straddle, long wings",
  },
];

const EXTRA_STRATEGY_OUTLOOK: Record<string, Outlook> = {
  naked_ce_short: "bearish",
  naked_pe_short: "bullish",
  long_call: "bullish",
  long_put: "bearish",
  long_butterfly: "neutral",
  long_condor: "volatile",
  long_call_butterfly: "neutral",
};

const strategyOutlookById = new Map<string, Outlook>(
  STRATEGY_TEMPLATES.flatMap((t) =>
    t.outlook.map((o) => [t.id, o] as const),
  ),
);

/** Primary outlook for propose-trades strategy_id (filter + tile icon). */
export function strategyOutlook(strategyId: string): Outlook | undefined {
  return (
    strategyOutlookById.get(strategyId) ??
    EXTRA_STRATEGY_OUTLOOK[strategyId]
  );
}

export const ALL_OUTLOOKS: Outlook[] = [
  "bullish",
  "bearish",
  "neutral",
  "volatile",
];
