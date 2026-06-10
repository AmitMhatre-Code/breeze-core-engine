"use client";

import { useEffect, useRef, useState } from "react";
import { OutlookIcon } from "@/components/strategy-builder/OutlookIcon";
import {
  ALL_OUTLOOKS,
  outlookPillClassName,
  outlookPillLabel,
} from "@/lib/strategy-builder/templates";
import type { Outlook } from "@/lib/strategy-builder/types";
import { sb } from "@/lib/strategy-builder/ui";

export function OutlookFilterDropdown({
  selected,
  onChange,
}: {
  selected: Set<Outlook>;
  onChange: (next: Set<Outlook>) => void;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const allSelected = selected.size === ALL_OUTLOOKS.length;
  const summary = allSelected
    ? "All outlooks"
    : ALL_OUTLOOKS.filter((o) => selected.has(o))
        .map(outlookPillLabel)
        .join(", ");

  const toggle = (o: Outlook) => {
    const next = new Set(selected);
    if (next.has(o)) {
      if (next.size <= 1) return;
      next.delete(o);
    } else {
      next.add(o);
    }
    onChange(next);
  };

  return (
    <div ref={rootRef} className="relative min-w-[10rem]">
      <button
        type="button"
        className={`${sb.select} flex items-center justify-between gap-2 text-left`}
        aria-expanded={open}
        aria-haspopup="listbox"
        onClick={() => setOpen((v) => !v)}
      >
        <span className="truncate">{summary}</span>
        <span className="shrink-0 text-zinc-400" aria-hidden>
          ▾
        </span>
      </button>
      {open ? (
        <div
          className="absolute left-0 top-full z-50 mt-1 min-w-full overflow-hidden rounded-lg border border-zinc-200/90 bg-white py-1 shadow-lg ring-1 ring-zinc-950/5 dark:border-zinc-700 dark:bg-zinc-900 dark:ring-white/10"
          role="listbox"
          aria-multiselectable
        >
          {ALL_OUTLOOKS.map((o) => {
            const on = selected.has(o);
            return (
              <button
                key={o}
                type="button"
                role="option"
                aria-selected={on}
                className={`flex w-full items-center gap-2.5 px-3 py-2 text-left text-sm transition hover:bg-zinc-50 dark:hover:bg-zinc-800/80 ${
                  on ? "bg-sky-50/80 dark:bg-sky-950/30" : ""
                }`}
                onClick={() => toggle(o)}
              >
                <span
                  className={`flex size-5 shrink-0 items-center justify-center rounded border ${
                    on
                      ? "border-sky-500 bg-sky-500 text-white"
                      : "border-zinc-300 bg-white dark:border-zinc-600 dark:bg-zinc-950"
                  }`}
                  aria-hidden
                >
                  {on ? "✓" : ""}
                </span>
                <OutlookIcon outlook={o} className="size-4" />
                <span
                  className={`rounded-full px-2 py-0.5 text-xs font-medium ${outlookPillClassName(o)}`}
                >
                  {outlookPillLabel(o)}
                </span>
              </button>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}
