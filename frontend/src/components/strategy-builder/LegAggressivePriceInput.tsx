"use client";

import { InfoPopover } from "@/components/strategy-builder/InfoPopover";
import { sb } from "@/lib/strategy-builder/ui";

const AGGRESSIVE_HELP = (
  <>
    ICICI derives the limit price from the last traded price (LTP), within
    exchange daily price range. Buy orders use a higher price; sell orders use a
    lower price. Orders may partially fill, remain pending, or be rejected
    depending on market conditions.
  </>
);

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
          aria-label={`${ariaLabel}. Market aggressive limit from LTP.`}
          onClick={() => onAggressiveChange(!aggressive)}
          className={`rounded px-1.5 py-1 text-[10px] font-bold tracking-wide transition focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-500/40 ${
            aggressive
              ? "border border-amber-400 bg-amber-100 text-amber-900 dark:border-amber-600 dark:bg-amber-950/60 dark:text-amber-300"
              : `${sb.tableToggle} border`
          }`}
        >
          MKT
        </button>
        <InfoPopover
          title="Aggressive limit"
          ariaLabel="Aggressive limit help"
        >
          {AGGRESSIVE_HELP}
        </InfoPopover>
      </div>
    </div>
  );
}
