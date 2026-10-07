"use client";

import { focusActivity } from "@/lib/bot-attention";
import { feedToneClass, type FeedSummary } from "@/lib/scalper-audit";
import type { BotType } from "@/lib/use-bots";

/** The card's warning triangle: something on this bot's session row in Activity is worth
 *  reading. The sentence itself is the tooltip, and a click opens Activity on this bot. */
export function AttentionMarker({ botType, summary }: { botType: BotType; summary: FeedSummary | null }) {
  if (!summary || summary.tone === "ok") return null;
  return (
    <button
      type="button"
      onClick={() => focusActivity(botType)}
      title={`${summary.text} See Activity for details.`}
      aria-label={`${summary.text} Show in Activity.`}
      className={`grid size-4 place-items-center rounded transition hover:opacity-80 focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/45 ${feedToneClass(summary.tone)}`}
    >
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" className="size-3.5" aria-hidden>
        <path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" />
        <path d="M12 9v4M12 17h.01" strokeLinecap="round" />
      </svg>
    </button>
  );
}
