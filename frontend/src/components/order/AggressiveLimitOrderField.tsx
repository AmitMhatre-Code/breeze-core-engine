"use client";

import { useEffect } from "react";
import { InfoPopover } from "@/components/ui/InfoPopover";
import { aggressiveLimitPopoverParagraphs } from "@/lib/help/topic-content";
import { sb } from "@/lib/strategy-builder/ui";
import { useAggressiveLimitOrderEnabled } from "@/lib/use-aggressive-limit-order-enabled";

function LightningBoltIcon({ filled }: { filled?: boolean }) {
  return (
    <svg
      width="15"
      height="15"
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
  const enabled = useAggressiveLimitOrderEnabled();
  const isAggressive = enabled && aggressive;

  // ICICI has no native aggressive-limit support yet; deactivated behind
  // AGGRESSIVE_LIMIT_ORDER_ENABLED. Clear any stale toggle state defensively.
  useEffect(() => {
    if (!enabled && aggressive) onAggressiveChange(false);
  }, [enabled, aggressive, onAggressiveChange]);

  return (
    <label className={sb.fieldLabel}>
      <span className="inline-flex items-center gap-1.5">
        Price (₹)
        {enabled && (
          <InfoPopover
            title="Aggressive limit"
            ariaLabel="Aggressive limit help"
            learnMoreTopicId="aggressive-limit"
          >
            {paragraphs.map((p, i) => (
              <p key={i}>{p}</p>
            ))}
          </InfoPopover>
        )}
      </span>
      <div className="relative mt-1.5">
        <input
          type="number"
          min={0}
          step={0.05}
          disabled={isAggressive}
          aria-label="Limit price"
          className={`${sb.input} tabular-nums pr-11 disabled:cursor-not-allowed disabled:opacity-50`}
          value={isAggressive ? "" : price}
          onChange={(e) => onPriceChange(e.target.value)}
          placeholder={isAggressive ? "Aggressive Limit Order" : "0"}
        />
        {enabled && (
          <button
            type="button"
            aria-pressed={aggressive}
            aria-label={`${ariaLabel}. Toggle aggressive limit from LTP.`}
            title="Aggressive limit"
            onClick={() => onAggressiveChange(!aggressive)}
            className="absolute right-1.5 top-1/2 flex size-7 -translate-y-1/2 items-center justify-center rounded-[7px] transition focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
            style={{ color: "var(--amber)" }}
          >
            <LightningBoltIcon filled={aggressive} />
          </button>
        )}
      </div>
    </label>
  );
}
