import type { HelpCategory } from "@/lib/help/categories";
import { HELP_CATEGORY_LABELS } from "@/lib/help/categories";
import {
  getTopicById,
  helpTopics,
  type HelpTopic,
} from "@/lib/help/topics";
import { formatIsoDateDdMmmYyyy } from "@/lib/format-iso-date";

export function getTopicsByCategory(
  category: HelpCategory,
): HelpTopic[] {
  return helpTopics.filter((t) => t.category === category);
}

export function getGroupedTopics(): {
  category: HelpCategory;
  label: string;
  topics: HelpTopic[];
}[] {
  const order: HelpCategory[] = [
    "account",
    "orders",
    "quotes",
    "strategy",
    "settings",
    "product",
  ];
  return order
    .map((category) => ({
      category,
      label: HELP_CATEGORY_LABELS[category],
      topics: getTopicsByCategory(category),
    }))
    .filter((g) => g.topics.length > 0);
}

/** Short excerpt for aggressive-limit InfoPopovers */
export function aggressiveLimitPopoverParagraphs(): string[] {
  return getTopicById("aggressive-limit")?.body ?? [];
}
const popTopic = getTopicById("probability-of-profit");

export const POP_HELP_TITLE = popTopic?.title ?? "Probability of profit (PoP)";
export const POP_HELP_DEFINITION = popTopic?.body[0] ?? "";
export const POP_HELP_INCOME = popTopic?.body[1] ?? "";
export const POP_HELP_DIRECTIONAL = popTopic?.body[2] ?? "";
export const POP_HELP_DISCLAIMER = popTopic?.body[3] ?? "";

/** Quote source detail lines — kept in sync with quote-sources help topic */
export function quoteSourceDetailLine(
  source: "websocket" | "bhavcopy" | "icici_api",
  bhavcopyDate?: string | null,
  bhavcopyStale?: boolean,
): string {
  switch (source) {
    case "websocket":
      return "Prices and depth are streamed from the ICICI Breeze WebSocket during market hours. Values refresh automatically while you stay on this page.";
    case "bhavcopy": {
      const staleNote = bhavcopyStale
        ? " The market has since opened, so these prices no longer reflect the live market."
        : "";
      if (bhavcopyDate) {
        return `Closing prices from the NSE/BSE FO Bhavcopy for ${formatIsoDateDdMmmYyyy(bhavcopyDate)}. Open interest and depth reflect the last concluded session, not live market data.${staleNote}`;
      }
      return `Closing prices from the NSE/BSE FO Bhavcopy after market hours. Not live market data.${staleNote}`;
    }
    case "icici_api":
      return "Quotes were fetched via the ICICI Breeze REST API because live WebSocket or Bhavcopy data was unavailable.";
    default:
      return "Quote source could not be determined.";
  }
}

export { getTopicById, helpTopics };
