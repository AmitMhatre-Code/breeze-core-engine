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

export function LegAggressivePriceInput({
  aggressive,
  premiumPerUnit,
  ariaLabel,
  onAggressiveChange,
  onPriceChange,
}: {
  aggressive: boolean;
  premiumPerUnit: number | undefined;
  ariaLabel: string;
  onAggressiveChange: (checked: boolean) => void;
  onPriceChange: (premiumPerUnit: number | undefined) => void;
}) {
  const paragraphs = aggressiveLimitPopoverParagraphs();

  return (
    <div className="flex items-center gap-1">
      <input
        type="number"
        min={0}
        step={0.05}
        disabled={aggressive}
        aria-label="Limit price per unit"
        className={`${sb.tableInput} w-[5rem] min-w-0 tabular-nums disabled:cursor-not-allowed disabled:opacity-50`}
        value={
          aggressive ? "" : premiumPerUnit != null ? premiumPerUnit : ""
        }
        onChange={(e) => {
          const v = parseFloat(e.target.value);
          onPriceChange(Number.isFinite(v) ? v : undefined);
        }}
      />
      <div className="inline-flex shrink-0 items-center gap-0.5">
        <button
          type="button"
          aria-pressed={aggressive}
          aria-label={`${ariaLabel}. Toggle aggressive limit from LTP.`}
          title="Aggressive limit"
          onClick={() => onAggressiveChange(!aggressive)}
          className={`rounded p-1 transition focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 ${
            aggressive
              ? "border border-amber-accent bg-amber-tint text-amber-accent"
              : `${sb.tableToggle} border text-muted`
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
  );
}
