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
import { useCalendarKeyboard } from "@/lib/ui/use-calendar-keyboard";

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
  const triggerRef = useRef<HTMLButtonElement>(null);
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

  const selectDay = useCallback(
    (day: number) => {
      onChange(toIsoDate(viewY, viewM, day));
      setOpen(false);
    },
    [onChange, viewY, viewM],
  );

  const closeCalendar = useCallback(() => {
    setOpen(false);
    triggerRef.current?.focus();
  }, []);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      const el = rootRef.current;
      if (!el?.contains(e.target as Node)) closeCalendar();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeCalendar();
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, closeCalendar]);

  const selectedDay =
    parsed && parsed.y === viewY && parsed.m === viewM ? parsed.d : null;
  const todayParts = parseIsoDateParts(todayIso);
  const todayDay =
    todayParts && todayParts.y === viewY && todayParts.m === viewM
      ? todayParts.d
      : null;

  const { getDayButtonProps } = useCalendarKeyboard({
    open,
    cells,
    selectedDay,
    todayDay,
    onSelectDay: selectDay,
    onClose: closeCalendar,
    triggerRef,
  });

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
    <div ref={rootRef} className="relative inline-block min-w-0 self-start sm:self-auto">
      <label htmlFor={id} className="sr-only">
        {label}
      </label>
      <button
        ref={triggerRef}
        type="button"
        id={id}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={listboxId}
        className={[
          "flex h-[34px] items-center gap-2 rounded-[9px] border border-border bg-panel2 px-2.5 text-left font-mono text-heading tabular-nums text-foreground transition",
          "hover:border-accent/60 focus:outline-none focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-accent/25",
        ].join(" ")}
        onClick={() => {
          if (open) setOpen(false);
          else {
            syncViewToValueOrToday();
            setOpen(true);
          }
        }}
      >
        <CalendarGlyph className="shrink-0 text-faint" />
        {displayText ? (
          <span>{displayText}</span>
        ) : (
          <span className="text-faint">{placeholder}</span>
        )}
      </button>

      {open ? (
        <div
          id={listboxId}
          role="dialog"
          aria-label="Choose date"
          className="absolute left-0 top-[calc(100%+0.25rem)] z-50 w-72 rounded-[10px] border border-border bg-elevated p-3 shadow-pop"
        >
          <div className="mb-2 flex items-center justify-between gap-1">
            <button
              type="button"
              className="inline-flex size-8 shrink-0 items-center justify-center rounded-lg text-muted transition hover:bg-panel2 hover:text-foreground"
              aria-label="Previous month"
              onClick={goPrevMonth}
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
                <path d="m15 18-6-6 6-6" />
              </svg>
            </button>
            <span className="min-w-0 flex-1 text-center text-sm font-semibold text-foreground">
              {MONTH_LABELS[viewM - 1]} {viewY}
            </span>
            <button
              type="button"
              className="inline-flex size-8 shrink-0 items-center justify-center rounded-lg text-muted transition hover:bg-panel2 hover:text-foreground"
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
                className="py-1 text-body font-semibold uppercase tracking-wide text-faint"
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
                  {...getDayButtonProps(i, day)}
                  aria-label={formatIsoDateDdMmmYyyy(iso)}
                  className={[
                    "aspect-square rounded-lg text-sm font-medium tabular-nums transition",
                    isSelected
                      ? "bg-accent-strong text-accent-ink"
                      : "text-foreground hover:bg-panel2",
                    isToday && !isSelected
                      ? "ring-1 ring-inset ring-accent/50"
                      : "",
                  ].join(" ")}
                  onClick={() => selectDay(day)}
                >
                  {day}
                </button>
              );
            })}
          </div>

          <div className="mt-3 border-t border-border-soft pt-2">
            <button
              type="button"
              className="w-full rounded-lg py-1.5 text-xs font-medium text-accent-strong transition hover:bg-accent-tint"
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
