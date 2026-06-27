"use client";

import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  formatIsoDateDdMmmYyyy,
  parseIsoDateParts,
  toIsoDate,
} from "@/lib/format-iso-date";

const WEEKDAY_LABELS = ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"] as const;

const MONTH_LABELS = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
] as const;

function monthMatrix(year: number, month: number): (number | null)[] {
  const dim = new Date(year, month, 0).getDate();
  const startPad = new Date(year, month - 1, 1).getDay();
  const cells: (number | null)[] = [];
  for (let i = 0; i < startPad; i++) cells.push(null);
  for (let d = 1; d <= dim; d++) cells.push(d);
  while (cells.length % 7 !== 0) cells.push(null);
  return cells;
}

function CalendarGlyph({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <rect x="3" y="4" width="18" height="18" rx="2" />
      <path d="M16 2v4M8 2v4M3 10h18" />
    </svg>
  );
}

type OrderBookDatePopoverProps = {
  id: string;
  label: string;
  value: string;
  onChange: (iso: string) => void;
  placeholder?: string;
};

export function OrderBookDatePopover({
  id,
  label,
  value,
  onChange,
  placeholder = "Select date",
}: OrderBookDatePopoverProps) {
  const listboxId = useId();
  const rootRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);

  const parsed = useMemo(() => parseIsoDateParts(value), [value]);

  const [view, setView] = useState(() => {
    const p = parseIsoDateParts(value);
    if (p) return { y: p.y, m: p.m };
    const d = new Date();
    return { y: d.getFullYear(), m: d.getMonth() + 1 };
  });

  const { y: viewY, m: viewM } = view;

  const syncViewToValueOrToday = useCallback(() => {
    const p = parseIsoDateParts(value);
    if (p) setView({ y: p.y, m: p.m });
    else {
      const d = new Date();
      setView({ y: d.getFullYear(), m: d.getMonth() + 1 });
    }
  }, [value]);

  const cells = useMemo(
    () => monthMatrix(viewY, viewM),
    [viewY, viewM],
  );

  const t = new Date();
  const todayIso = toIsoDate(
    t.getFullYear(),
    t.getMonth() + 1,
    t.getDate(),
  );

  const goPrevMonth = useCallback(() => {
    setView((v) =>
      v.m <= 1 ? { y: v.y - 1, m: 12 } : { ...v, m: v.m - 1 },
    );
  }, []);

  const goNextMonth = useCallback(() => {
    setView((v) =>
      v.m >= 12 ? { y: v.y + 1, m: 1 } : { ...v, m: v.m + 1 },
    );
  }, []);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      const el = rootRef.current;
      if (!el?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const selectDay = useCallback(
    (day: number) => {
      onChange(toIsoDate(viewY, viewM, day));
      setOpen(false);
    },
    [onChange, viewY, viewM],
  );

  const goToday = useCallback(() => {
    const d = new Date();
    const y = d.getFullYear();
    const m = d.getMonth() + 1;
    const day = d.getDate();
    onChange(toIsoDate(y, m, day));
    setView({ y, m });
    setOpen(false);
  }, [onChange]);

  const displayText = parsed ? formatIsoDateDdMmmYyyy(value) : null;

  return (
    <div ref={rootRef} className="relative min-w-0 flex-1">
      <label htmlFor={id} className="sr-only">
        {label}
      </label>
      <CalendarGlyph className="pointer-events-none absolute left-3 top-1/2 z-[2] size-4 -translate-y-1/2 text-zinc-400 dark:text-zinc-500" />
      <button
        type="button"
        id={id}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={listboxId}
        className={[
          "flex h-11 w-full min-w-[11rem] items-center rounded-md border border-zinc-200 bg-white py-0 pl-10 pr-3 text-left text-sm tabular-nums text-zinc-900 shadow-sm transition",
          "hover:border-zinc-300 focus:outline-none focus-visible:border-sky-500 focus-visible:ring-2 focus-visible:ring-sky-500/25",
          "dark:border-zinc-600 dark:bg-zinc-800 dark:text-zinc-100 dark:hover:border-zinc-500 dark:focus-visible:border-sky-400 dark:focus-visible:ring-sky-400/25",
        ].join(" ")}
        onClick={() => {
          if (open) setOpen(false);
          else {
            syncViewToValueOrToday();
            setOpen(true);
          }
        }}
      >
        {displayText ? (
          <span>{displayText}</span>
        ) : (
          <span className="text-zinc-400 dark:text-zinc-500">{placeholder}</span>
        )}
      </button>

      {open ? (
        <div
          id={listboxId}
          role="dialog"
          aria-label="Choose date"
          className="absolute left-0 top-[calc(100%+0.25rem)] z-50 w-[min(100%,18rem)] rounded-md border border-zinc-200/90 bg-white p-3 shadow-lg ring-1 ring-zinc-950/[0.04] dark:border-zinc-700 dark:bg-zinc-900 dark:ring-white/[0.06]"
        >
          <div className="mb-2 flex items-center justify-between gap-1">
            <button
              type="button"
              className="inline-flex size-8 shrink-0 items-center justify-center rounded-lg text-zinc-600 transition hover:bg-zinc-100 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
              aria-label="Previous month"
              onClick={goPrevMonth}
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
                <path d="m15 18-6-6 6-6" />
              </svg>
            </button>
            <span className="min-w-0 flex-1 text-center text-sm font-semibold text-zinc-900 dark:text-zinc-100">
              {MONTH_LABELS[viewM - 1]} {viewY}
            </span>
            <button
              type="button"
              className="inline-flex size-8 shrink-0 items-center justify-center rounded-lg text-zinc-600 transition hover:bg-zinc-100 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
              aria-label="Next month"
              onClick={goNextMonth}
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
                <path d="m9 18 6-6-6-6" />
              </svg>
            </button>
          </div>

          <div className="mb-2 grid grid-cols-7 gap-0.5 text-center">
            {WEEKDAY_LABELS.map((w) => (
              <div
                key={w}
                className="py-1 text-[10px] font-semibold uppercase tracking-wide text-zinc-400 dark:text-zinc-500"
              >
                {w}
              </div>
            ))}
          </div>

          <div className="grid grid-cols-7 gap-0.5">
            {cells.map((day, i) => {
              if (day == null) {
                return <div key={`e-${i}`} className="aspect-square" />;
              }
              const iso = toIsoDate(viewY, viewM, day);
              const isToday = iso === todayIso;
              const isSelected = parsed && iso === value.trim();
              return (
                <button
                  key={`${viewY}-${viewM}-${day}`}
                  type="button"
                  className={[
                    "aspect-square rounded-lg text-sm font-medium tabular-nums transition",
                    isSelected
                      ? "bg-sky-600 text-white shadow-sm dark:bg-sky-500"
                      : "text-zinc-800 hover:bg-zinc-100 dark:text-zinc-200 dark:hover:bg-zinc-800",
                    isToday && !isSelected
                      ? "ring-1 ring-inset ring-sky-500/50 dark:ring-sky-400/40"
                      : "",
                  ].join(" ")}
                  onClick={() => selectDay(day)}
                >
                  {day}
                </button>
              );
            })}
          </div>

          <div className="mt-3 border-t border-zinc-200/80 pt-2 dark:border-zinc-700/80">
            <button
              type="button"
              className="w-full rounded-lg py-1.5 text-xs font-medium text-sky-700 transition hover:bg-sky-50 dark:text-sky-300 dark:hover:bg-sky-950/50"
              onClick={goToday}
            >
              Today
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
