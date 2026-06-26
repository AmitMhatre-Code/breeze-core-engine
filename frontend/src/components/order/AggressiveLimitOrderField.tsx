"use client";

import { InfoPopover } from "@/components/strategy-builder/InfoPopover";

type Props = {
  id?: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
  className?: string;
};

const HELP = (
  <>
    ICICI derives the limit price from the last traded price (LTP), within
    exchange daily price range. Buy orders use a higher price; sell orders use a
    lower price. Orders may partially fill, remain pending, or be rejected
    depending on market conditions.
  </>
);

export function AggressiveLimitOrderField({
  id,
  checked,
  onChange,
  disabled = false,
  className = "",
}: Props) {
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
        <InfoPopover title="Aggressive limit order" ariaLabel="Aggressive limit order help">
          {HELP}
        </InfoPopover>
      </span>
    </label>
  );
}
