"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import type { FinancialYearOption } from "@/lib/performance-data";

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

const surface =
  "rounded-lg border border-zinc-300/90 bg-zinc-100 text-zinc-900 shadow-sm " +
  "dark:border-zinc-600/80 dark:bg-zinc-800 dark:text-zinc-50 dark:shadow-none";

export function FinancialYearDropdown({
  years,
  selectedYear,
  onSelect,
  labelId,
}: {
  years: FinancialYearOption[];
  selectedYear: string;
  onSelect: (y: FinancialYearOption) => void;
  /** Visible label element id for aria-labelledby */
  labelId: string;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const listId = useId();

  const close = useCallback(() => setOpen(false), []);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      const el = rootRef.current;
      if (el && e.target instanceof Node && !el.contains(e.target)) close();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, close]);

  if (years.length === 0) return null;

  const current = years.find((y) => y.year === selectedYear) ?? years[0];
  const display = current?.year ?? selectedYear;

  return (
    <div ref={rootRef} className="relative inline-block min-w-[8.5rem] text-left">
      <button
        type="button"
        className={[
          "flex w-full items-center justify-between gap-3 px-4 py-2.5 text-sm font-normal transition-colors",
          "hover:bg-zinc-200/80 dark:hover:bg-zinc-700/80",
          "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-500",
          surface,
        ].join(" ")}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={listId}
        aria-labelledby={labelId}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="truncate tabular-nums">{display}</span>
        <ChevronDown open={open} />
      </button>

      {open ? (
        <ul
          id={listId}
          role="listbox"
          aria-labelledby={labelId}
          className={[
            "absolute right-0 top-[calc(100%+6px)] z-30 min-w-full overflow-hidden py-1",
            surface,
          ].join(" ")}
        >
          {years.map((y) => {
            const selected = y.year === selectedYear;
            return (
              <li key={y.year} role="presentation">
                <button
                  type="button"
                  role="option"
                  aria-selected={selected}
                  className={[
                    "flex w-full items-center px-4 py-2.5 text-left text-sm font-normal transition-colors",
                    "text-zinc-900 dark:text-zinc-50",
                    selected
                      ? "bg-zinc-200/90 dark:bg-zinc-700/60"
                      : "hover:bg-zinc-200/70 dark:hover:bg-zinc-700/50",
                  ].join(" ")}
                  onClick={() => {
                    onSelect(y);
                    close();
                  }}
                >
                  <span className="tabular-nums">{y.year}</span>
                </button>
              </li>
            );
          })}
        </ul>
      ) : null}
    </div>
  );
}
