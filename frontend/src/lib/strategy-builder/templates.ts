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
      return "text-emerald-500";
    case "bearish":
      return "text-rose-500";
    case "neutral":
      return "text-sky-500";
    case "volatile":
      return "text-violet-500";
  }
}

export function outlookPillClassName(o: Outlook): string {
  switch (o) {
    case "bullish":
      return "bg-emerald-500/15 text-emerald-800 ring-1 ring-emerald-600/25 dark:text-emerald-200 dark:ring-emerald-400/20";
    case "bearish":
      return "bg-rose-500/15 text-rose-800 ring-1 ring-rose-600/25 dark:text-rose-200 dark:ring-rose-400/20";
    case "neutral":
      return "bg-zinc-500/15 text-zinc-800 ring-1 ring-zinc-600/25 dark:text-zinc-200 dark:ring-zinc-400/20";
    case "volatile":
      return "bg-violet-500/15 text-violet-800 ring-1 ring-violet-600/25 dark:text-violet-200 dark:ring-violet-400/20";
  }
}

/** Bordered icon toggle for outlook filters (fixed 40×40px). */
export function outlookFilterBtnClassName(o: Outlook, on: boolean): string {
  const base =
    "inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border transition focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-500/40";
  if (!on) {
    return `${base} border-zinc-200 bg-white/40 opacity-50 shadow-sm hover:border-zinc-300 hover:bg-zinc-50 hover:opacity-75 dark:border-zinc-700 dark:bg-zinc-900/40 dark:hover:border-zinc-600 dark:hover:bg-zinc-800/60`;
  }
  switch (o) {
    case "bullish":
      return `${base} border-emerald-500 bg-emerald-500/15 opacity-100 shadow-sm ring-1 ring-emerald-500/25 dark:border-emerald-400 dark:bg-emerald-500/20 dark:ring-emerald-400/20`;
    case "bearish":
      return `${base} border-rose-500 bg-rose-500/15 opacity-100 shadow-sm ring-1 ring-rose-500/25 dark:border-rose-400 dark:bg-rose-500/20 dark:ring-rose-400/20`;
    case "neutral":
      return `${base} border-sky-500 bg-sky-500/15 opacity-100 shadow-sm ring-1 ring-sky-500/25 dark:border-sky-400 dark:bg-sky-500/20 dark:ring-sky-400/20`;
    case "volatile":
      return `${base} border-violet-500 bg-violet-500/15 opacity-100 shadow-sm ring-1 ring-violet-500/25 dark:border-violet-400 dark:bg-violet-500/20 dark:ring-violet-400/20`;
  }
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
