"use client";

import { useCallback, useRef, useState } from "react";
import type { FinancialYearOption } from "@/lib/performance-data";
import {
  useListboxMenu,
  useListboxOutsideClose,
} from "@/lib/ui/use-listbox-menu";

function ChevronDown({ open }: { open: boolean }) {
  return (
    <svg
      width="12"
      height="12"
      viewBox="0 0 12 12"
      fill="none"
      aria-hidden
      className={[
        "shrink-0 text-current opacity-80 transition-transform duration-200",
        open ? "-rotate-180" : "",
      ].join(" ")}
    >
      <path
        d="M2.5 4.25L6 7.75l3.5-3.5"
        stroke="currentColor"
        strokeWidth="1.25"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg
      width="13"
      height="13"
      viewBox="0 0 12 12"
      fill="none"
      aria-hidden
      className="shrink-0 text-accent-strong"
    >
      <path
        d="M2.5 6.25L4.75 8.5L9.5 3.5"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function FinancialYearDropdown({
  years,
  selectedYear,
  onSelect,
  labelId,
}: {
  years: FinancialYearOption[];
  selectedYear: string;
  onSelect: (y: FinancialYearOption) => void;
  labelId: string;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const listId = "financial-year-listbox";

  const close = useCallback(() => setOpen(false), []);

  useListboxOutsideClose(open, rootRef, close);

  const { highlightIndex, handleTriggerKeyDown } = useListboxMenu({
    open,
    optionCount: years.length,
    onOpen: () => setOpen(true),
    onClose: () => {
      close();
      triggerRef.current?.focus();
    },
    onSelectIndex: (index) => {
      const y = years[index];
      if (y) onSelect(y);
      close();
      triggerRef.current?.focus();
    },
    triggerRef,
    listRef,
  });

  if (years.length === 0) return null;

  const current = years.find((y) => y.year === selectedYear) ?? years[0];
  const display = current?.year ?? selectedYear;

  return (
    <div className="flex items-center gap-2.5">
      <span
        id={labelId}
        className="text-[10.5px] font-semibold uppercase tracking-[.06em] text-faint"
      >
        Financial year
      </span>
      <div ref={rootRef} className="relative inline-block min-w-[8.5rem] text-left">
        <button
          ref={triggerRef}
          type="button"
          className={[
            "flex w-full items-center justify-between gap-3 rounded-[9px] border px-3.5 py-2 font-mono text-sm text-foreground transition",
            open
              ? "border-accent bg-panel2 ring-2 ring-accent/25"
              : "border-border bg-panel2 hover:border-accent/60",
          ].join(" ")}
          aria-haspopup="listbox"
          aria-expanded={open}
          aria-controls={listId}
          aria-labelledby={labelId}
          onClick={() => setOpen((v) => !v)}
          onKeyDown={handleTriggerKeyDown}
        >
          <span className="truncate tabular-nums">{display}</span>
          <ChevronDown open={open} />
        </button>

        {open ? (
          <ul
            ref={listRef}
            id={listId}
            role="listbox"
            aria-labelledby={labelId}
            className="absolute right-0 top-[calc(100%+6px)] z-30 min-w-full overflow-hidden rounded-[10px] border border-border bg-elevated py-1 shadow-pop"
          >
            {years.map((y, index) => {
              const selected = y.year === selectedYear;
              const highlighted = index === highlightIndex;
              return (
                <li key={y.year} role="presentation">
                  <button
                    type="button"
                    role="option"
                    tabIndex={-1}
                    data-menu-index={index}
                    aria-selected={selected}
                    className={[
                      "flex w-full items-center justify-between gap-2 px-3.5 py-2 text-left font-mono text-sm transition-colors",
                      selected
                        ? "bg-accent-tint font-semibold text-foreground"
                        : highlighted
                          ? "bg-panel2 text-foreground"
                          : "text-muted hover:bg-panel2",
                    ].join(" ")}
                    onClick={() => {
                      onSelect(y);
                      close();
                    }}
                  >
                    <span className="tabular-nums">{y.year}</span>
                    {selected ? <CheckIcon /> : null}
                  </button>
                </li>
              );
            })}
          </ul>
        ) : null}
      </div>
    </div>
  );
}
