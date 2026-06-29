"use client";

import { InfoPopover } from "@/components/strategy-builder/InfoPopover";
import { aggressiveLimitPopoverParagraphs } from "@/lib/help/topic-content";
import { sb } from "@/lib/strategy-builder/ui";

function LightningBoltIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z" />
    </svg>
  );
}

export function AggressiveLimitOrderField({
  aggressive,
  price,
  onAggressiveChange,
  onPriceChange,
  ariaLabel = "Aggressive limit order",
}: {
  aggressive: boolean;
  price: string;
  onAggressiveChange: (checked: boolean) => void;
  onPriceChange: (price: string) => void;
  ariaLabel?: string;
}) {
  const paragraphs = aggressiveLimitPopoverParagraphs();

  return (
    <label className={sb.fieldLabel}>
      Limit price (₹)
      <div className="flex items-center gap-2">
        <input
          type="number"
          min={0}
          step={0.05}
          disabled={aggressive}
          aria-label="Limit price"
          className={`${sb.input} min-w-0 flex-1 tabular-nums disabled:cursor-not-allowed disabled:opacity-50`}
          value={aggressive ? "" : price}
          onChange={(e) => onPriceChange(e.target.value)}
          placeholder="0"
        />
        <div className="inline-flex shrink-0 items-center gap-0.5">
          <button
            type="button"
            aria-pressed={aggressive}
            aria-label={`${ariaLabel}. Toggle aggressive limit from LTP.`}
            title="Aggressive limit"
            onClick={() => onAggressiveChange(!aggressive)}
            className={`rounded p-1.5 transition focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-500/40 ${
              aggressive
                ? "border border-amber-400 bg-amber-100 text-amber-900 dark:border-amber-600 dark:bg-amber-950/60 dark:text-amber-300"
                : `${sb.tableToggle} border text-zinc-600 dark:text-zinc-300`
            }`}
          >
            <LightningBoltIcon />
          </button>
          <InfoPopover
            title="Aggressive limit"
            ariaLabel="Aggressive limit help"
            learnMoreTopicId="aggressive-limit"
          >
            {paragraphs.map((p, i) => (
              <p key={i}>{p}</p>
            ))}
          </InfoPopover>
        </div>
      </div>
    </label>
  );
}
