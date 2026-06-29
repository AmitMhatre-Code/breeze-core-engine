"use client";

import { InfoPopover } from "@/components/strategy-builder/InfoPopover";
import { aggressiveLimitPopoverParagraphs } from "@/lib/help/topic-content";

export function AggressiveLimitOrderField({
  id,
  checked,
  onChange,
  disabled = false,
  className = "",
}: {
  id?: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
  className?: string;
}) {
  const paragraphs = aggressiveLimitPopoverParagraphs();

  return (
    <label
      htmlFor={id}
      className={`flex cursor-pointer items-start gap-2 text-sm text-zinc-700 dark:text-zinc-300 ${className}`}
    >
      <input
        id={id}
        type="checkbox"
        className="mt-0.5 size-4 shrink-0 rounded border-zinc-300 text-emerald-600 focus:ring-emerald-500/40 disabled:cursor-not-allowed disabled:opacity-50 dark:border-zinc-600"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span className="inline-flex flex-wrap items-center gap-1.5">
        <span className="font-medium">Aggressive limit order</span>
        <InfoPopover
          title="Aggressive limit order"
          ariaLabel="Aggressive limit order help"
          learnMoreTopicId="aggressive-limit"
        >
          {paragraphs.map((p, i) => (
            <p key={i}>{p}</p>
          ))}
        </InfoPopover>
      </span>
    </label>
  );
}
