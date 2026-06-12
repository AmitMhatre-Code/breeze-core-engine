"use client";

import { OutlookIcon } from "@/components/strategy-builder/OutlookIcon";
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
      className="flex flex-wrap items-center gap-3"
      role="group"
      aria-label="Filter by outlook"
    >
      <span className="text-xs font-medium text-zinc-600 dark:text-zinc-400">
        Select / Unselect Outlook
      </span>
      {ALL_OUTLOOKS.map((o) => {
        const on = selected.has(o);
        return (
          <button
            key={o}
            type="button"
            aria-pressed={on}
            aria-label={`${outlookPillLabel(o)} strategies`}
            title={outlookPillLabel(o)}
            onClick={() => toggle(o)}
            className={outlookFilterBtnClassName(o, on)}
          >
            <OutlookIcon outlook={o} className="size-5" uniformSize />
          </button>
        );
      })}
    </div>
  );
}
