import type { Outlook, StrategyLeg } from "@/lib/strategy-builder/types";

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

/** Readymade tiles in `Strategy Builder` section 2 (with icons). */
export type ReadymadeCardId =
  | "build-your-own"
  | "naked-shorts"
  | "covered-shorts";

export type StrategyCardId = TemplateId | ReadymadeCardId;

/** User-facing hover copy: one line per leg, aligned with `applyTemplate` / builder flows. */
export function strategySetupTooltipLines(
  id: StrategyCardId,
): readonly string[] {
  switch (id) {
    case "build-your-own":
      return ["Choose strikes and legs yourself"];
    case "naked-shorts":
      return ["Sell 1 CE (uncovered)", "Sell 1 PE (uncovered)"];
    case "covered-shorts":
      return ["Sell 1 CE (vs stock)", "Sell 1 PE (vs stock)"];
    case "bull_call_spread":
      return ["Buy 1 ATM CE", "Sell 1 OTM CE"];
    case "bear_call_spread":
      return ["Sell 1 ATM CE", "Buy 1 OTM CE"];
    case "bear_put_spread":
      return ["Buy 1 ATM PE", "Sell 1 OTM PE"];
    case "bull_put_spread":
      return ["Sell 1 ATM PE", "Buy 1 OTM PE"];
    case "long_straddle":
      return ["Buy 1 ATM CE", "Buy 1 ATM PE"];
    case "short_straddle":
      return ["Sell 1 ATM CE", "Sell 1 ATM PE"];
    case "long_strangle":
      return ["Buy 1 OTM CE", "Buy 1 OTM PE"];
    case "short_strangle":
      return ["Sell 1 OTM CE", "Sell 1 OTM PE"];
    case "long_call_butterfly":
      return ["Buy 1 ITM CE", "Sell 2 ATM CE", "Buy 1 OTM CE"];
    case "iron_condor":
      return ["Buy 1 outer PE", "Sell 1 inner PE", "Sell 1 inner CE", "Buy 1 outer CE"];
    case "iron_butterfly":
      return [
        "Buy 1 OTM PE",
        "Sell 1 ATM PE",
        "Sell 1 ATM CE",
        "Buy 1 OTM CE",
      ];
  }
}

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

function pickStrike(strikes: number[], atmIdx: number, offset: number): number | null {
  const i = atmIdx + offset;
  if (i < 0 || i >= strikes.length) return null;
  return strikes[i];
}

function midFromRow(
  row: { call?: Record<string, unknown> | null; put?: Record<string, unknown> | null } | undefined,
  right: "Call" | "Put",
): number | undefined {
  const side = right === "Call" ? row?.call : row?.put;
  if (!side) return undefined;
  const ltp = side.ltp;
  const bid = side.best_bid_price;
  const ask = side.best_offer_price;
  const ln = typeof ltp === "string" ? parseFloat(ltp) : typeof ltp === "number" ? ltp : NaN;
  if (Number.isFinite(ln) && ln > 0) return ln;
  const b = typeof bid === "string" ? parseFloat(bid) : typeof bid === "number" ? bid : NaN;
  const a = typeof ask === "string" ? parseFloat(ask) : typeof ask === "number" ? ask : NaN;
  if (Number.isFinite(b) && Number.isFinite(a) && a > 0 && b > 0) return 0.5 * (a + b);
  return undefined;
}

let _idSeq = 0;
function legId(): string {
  _idSeq += 1;
  return `leg-${_idSeq}`;
}

export function templatesForOutlook(outlook: Outlook): StrategyTemplateMeta[] {
  return STRATEGY_TEMPLATES.filter((t) => t.outlook.includes(outlook));
}

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

export type ApplyTemplateContext = {
  strikes: number[];
  atmIdx: number;
  rowsByStrike: Map<number, { call?: Record<string, unknown> | null; put?: Record<string, unknown> | null }>;
};

export function buildTemplateContext(
  chainRows: { strike_price: number; call?: unknown; put?: unknown }[],
  spot: number | null,
): ApplyTemplateContext | null {
  const strikes = chainRows.map((r) => r.strike_price).sort((a, b) => a - b);
  if (!strikes.length) return null;
  let atmIdx = 0;
  if (spot != null && Number.isFinite(spot)) {
    let best = Infinity;
    for (let i = 0; i < strikes.length; i++) {
      const d = Math.abs(strikes[i] - spot);
      if (d < best) {
        best = d;
        atmIdx = i;
      }
    }
  } else {
    atmIdx = Math.floor(strikes.length / 2);
  }
  const rowsByStrike = new Map<
    number,
    { call?: Record<string, unknown> | null; put?: Record<string, unknown> | null }
  >();
  for (const r of chainRows) {
    rowsByStrike.set(r.strike_price, {
      call: (r.call as Record<string, unknown> | null) ?? null,
      put: (r.put as Record<string, unknown> | null) ?? null,
    });
  }
  return { strikes, atmIdx, rowsByStrike };
}

export function applyTemplate(
  templateId: TemplateId,
  ctx: ApplyTemplateContext,
): StrategyLeg[] {
  const { strikes, atmIdx, rowsByStrike } = ctx;
  const legs: StrategyLeg[] = [];

  const add = (
    right: "Call" | "Put",
    side: "Buy" | "Sell",
    strike: number | null,
  ) => {
    if (strike == null) return;
    const row = rowsByStrike.get(strike);
    const prem = midFromRow(row, right);
    legs.push({
      id: legId(),
      right,
      side,
      strike,
      lots: 1,
      premiumPerUnit: prem,
    });
  };

  switch (templateId) {
    case "bull_call_spread":
      add("Call", "Buy", pickStrike(strikes, atmIdx, 0));
      add("Call", "Sell", pickStrike(strikes, atmIdx, 1));
      break;
    case "bear_call_spread":
      add("Call", "Sell", pickStrike(strikes, atmIdx, 0));
      add("Call", "Buy", pickStrike(strikes, atmIdx, 1));
      break;
    case "bear_put_spread":
      add("Put", "Buy", pickStrike(strikes, atmIdx, 0));
      add("Put", "Sell", pickStrike(strikes, atmIdx, -1));
      break;
    case "bull_put_spread":
      add("Put", "Sell", pickStrike(strikes, atmIdx, 0));
      add("Put", "Buy", pickStrike(strikes, atmIdx, -1));
      break;
    case "long_straddle":
      add("Call", "Buy", pickStrike(strikes, atmIdx, 0));
      add("Put", "Buy", pickStrike(strikes, atmIdx, 0));
      break;
    case "short_straddle":
      add("Call", "Sell", pickStrike(strikes, atmIdx, 0));
      add("Put", "Sell", pickStrike(strikes, atmIdx, 0));
      break;
    case "long_strangle":
      add("Call", "Buy", pickStrike(strikes, atmIdx, 1));
      add("Put", "Buy", pickStrike(strikes, atmIdx, -1));
      break;
    case "short_strangle":
      add("Call", "Sell", pickStrike(strikes, atmIdx, 1));
      add("Put", "Sell", pickStrike(strikes, atmIdx, -1));
      break;
    case "iron_condor": {
      const pl = pickStrike(strikes, atmIdx, -2);
      const ps = pickStrike(strikes, atmIdx, -1);
      const cs = pickStrike(strikes, atmIdx, 1);
      const cl = pickStrike(strikes, atmIdx, 2);
      add("Put", "Buy", pl);
      add("Put", "Sell", ps);
      add("Call", "Sell", cs);
      add("Call", "Buy", cl);
      break;
    }
    case "iron_butterfly": {
      const wingL = pickStrike(strikes, atmIdx, -1);
      const mid = pickStrike(strikes, atmIdx, 0);
      const wingR = pickStrike(strikes, atmIdx, 1);
      add("Put", "Buy", wingL);
      add("Put", "Sell", mid);
      add("Call", "Sell", mid);
      add("Call", "Buy", wingR);
      break;
    }
    default:
      break;
  }
  return legs;
}
