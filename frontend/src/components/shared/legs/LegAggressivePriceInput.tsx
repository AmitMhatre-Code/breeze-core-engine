"use client";

import { InfoPopover } from "@/components/ui/InfoPopover";
import { aggressiveLimitPopoverParagraphs } from "@/lib/help/topic-content";
import { sb } from "@/lib/strategy-builder/ui";

function LightningBoltIcon({ filled }: { filled?: boolean }) {
  return (
    <svg
      width="13"
      height="13"
      viewBox="0 0 24 24"
      fill={filled ? "currentColor" : "none"}
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
      <div className="relative">
        <input
          type="number"
          min={0}
          step={0.05}
          disabled={aggressive}
          aria-label="Limit price per unit"
          className={`${sb.tableInput} w-[6.5rem] min-w-0 pr-7 tabular-nums disabled:cursor-not-allowed disabled:opacity-50`}
          value={aggressive ? "" : premiumPerUnit != null ? premiumPerUnit : ""}
          onChange={(e) => {
            const v = parseFloat(e.target.value);
            onPriceChange(Number.isFinite(v) ? v : undefined);
          }}
          placeholder={aggressive ? "Aggressive Limit" : "0"}
        />
        <button
          type="button"
          aria-pressed={aggressive}
          aria-label={`${ariaLabel}. Toggle aggressive limit from LTP.`}
          title="Aggressive limit"
          onClick={() => onAggressiveChange(!aggressive)}
          className="absolute right-1 top-1/2 flex size-5 -translate-y-1/2 items-center justify-center rounded transition focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
          style={{ color: "var(--amber)" }}
        >
          <LightningBoltIcon filled={aggressive} />
        </button>
      </div>
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
  );
}
