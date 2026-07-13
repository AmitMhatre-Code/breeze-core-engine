"use client";

import {
  ALL_OUTLOOKS,
  outlookFilterBtnClassName,
  outlookPillLabel,
} from "@/lib/strategy-builder/templates";
import type { Outlook } from "@/lib/strategy-builder/types";

export function OutlookFilterButtons({
  selected,
  onChange,
}: {
  selected: Set<Outlook>;
  onChange: (next: Set<Outlook>) => void;
}) {
  const toggle = (o: Outlook) => {
    const next = new Set(selected);
    if (next.has(o)) next.delete(o);
    else next.add(o);
    onChange(next);
  };

  return (
    <div
      className="flex items-center gap-1.5"
      role="group"
      aria-label="Filter by outlook"
    >
      {ALL_OUTLOOKS.map((o) => {
        const on = selected.has(o);
        return (
          <button
            key={o}
            type="button"
            aria-pressed={on}
            aria-label={`${outlookPillLabel(o)} strategies`}
            onClick={() => toggle(o)}
            className={outlookFilterBtnClassName(o, on)}
          >
            {outlookPillLabel(o)}
          </button>
        );
      })}
    </div>
  );
}
