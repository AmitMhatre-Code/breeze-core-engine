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
  for (let day = 1; day <= dim; day++) cells.push(day);
  while (cells.length % 7 !== 0) cells.push(null);
  return cells;
}

function CalendarIcon({ className }: { className?: string }) {
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

type DatePickerProps = {
  id?: string;
  value: string;
  onChange: (isoDate: string) => void;
  disabled?: boolean;
  className?: string;
  placeholder?: string;
};

export function DatePicker({
  id,
  value,
  onChange,
  disabled,
  className,
  placeholder = "dd-MMM-yyyy",
}: DatePickerProps) {
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const popoverId = useId();
  const [open, setOpen] = useState(false);

  const parsed = useMemo(() => parseIsoDateParts(value), [value]);

  const [view, setView] = useState(() => {
    if (parsed) return { y: parsed.y, m: parsed.m };
    const now = new Date();
    return { y: now.getFullYear(), m: now.getMonth() + 1 };
  });

  const syncView = useCallback(() => {
    if (parsed) setView({ y: parsed.y, m: parsed.m });
    else {
      const now = new Date();
      setView({ y: now.getFullYear(), m: now.getMonth() + 1 });
    }
  }, [parsed]);

  const cells = useMemo(() => monthMatrix(view.y, view.m), [view.y, view.m]);

  const selectDay = useCallback(
    (day: number) => {
      onChange(toIsoDate(view.y, view.m, day));
      setOpen(false);
    },
    [onChange, view.y, view.m],
  );

  const closeCalendar = useCallback(() => {
    setOpen(false);
    triggerRef.current?.focus();
  }, []);

  const todayIso = useMemo(() => {
    const t = new Date();
    return toIsoDate(t.getFullYear(), t.getMonth() + 1, t.getDate());
  }, []);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) closeCalendar();
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
    parsed && parsed.y === view.y && parsed.m === view.m ? parsed.d : null;
  const todayParts = parseIsoDateParts(todayIso);
  const todayDay =
    todayParts && todayParts.y === view.y && todayParts.m === view.m
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
    const t = new Date();
    const y = t.getFullYear();
    const m = t.getMonth() + 1;
    const d = t.getDate();
    onChange(toIsoDate(y, m, d));
    setView({ y, m });
    setOpen(false);
  }, [onChange]);

  const displayText = parsed ? formatIsoDateDdMmmYyyy(value) : null;

  return (
    <div
      ref={rootRef}
      className={["relative min-w-[11rem]", className].filter(Boolean).join(" ")}
    >
      <button
        ref={triggerRef}
        type="button"
        id={id}
        disabled={disabled}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={popoverId}
        className={[
          "flex h-10 w-full items-center gap-2.5 rounded-md border px-3 text-left text-sm transition",
          "border-zinc-200 bg-white text-zinc-900 shadow-sm",
          "hover:border-zinc-300 focus:outline-none focus-visible:border-sky-500 focus-visible:ring-2 focus-visible:ring-sky-500/25",
          "disabled:pointer-events-none disabled:opacity-45",
          "dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100",
          "dark:hover:border-zinc-600 dark:focus-visible:border-sky-400 dark:focus-visible:ring-sky-400/25",
        ].join(" ")}
        onClick={() => {
          if (disabled) return;
          if (open) setOpen(false);
          else {
            syncView();
            setOpen(true);
          }
        }}
      >
        <span className="flex size-7 shrink-0 items-center justify-center rounded-sm bg-zinc-100 text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400">
          <CalendarIcon />
        </span>
        {displayText ? (
          <span className="font-medium tabular-nums">{displayText}</span>
        ) : (
          <span className="text-zinc-400 dark:text-zinc-500">{placeholder}</span>
        )}
      </button>

      {open ? (
        <div
          id={popoverId}
          role="dialog"
          aria-label="Choose date"
          className="absolute left-0 top-[calc(100%+0.35rem)] z-50 w-[min(100%,18.5rem)] rounded-md border border-zinc-200/90 bg-white/98 p-3 shadow-xl ring-1 ring-zinc-950/5 backdrop-blur-md dark:border-zinc-700 dark:bg-zinc-900/95 dark:ring-white/6 dark:shadow-[0_24px_80px_rgb(0_0_0/0.45)]"
        >
          <div className="mb-2 flex items-center justify-between gap-1">
            <button
              type="button"
              className="inline-flex size-8 items-center justify-center rounded-md text-zinc-600 transition hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
              aria-label="Previous month"
              onClick={() =>
                setView((v) => (v.m <= 1 ? { y: v.y - 1, m: 12 } : { ...v, m: v.m - 1 }))
              }
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
                <path d="m15 18-6-6 6-6" />
              </svg>
            </button>
            <span className="min-w-0 flex-1 text-center text-sm font-semibold text-zinc-900 dark:text-zinc-100">
              {MONTH_LABELS[view.m - 1]} {view.y}
            </span>
            <button
              type="button"
              className="inline-flex size-8 items-center justify-center rounded-md text-zinc-600 transition hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
              aria-label="Next month"
              onClick={() =>
                setView((v) => (v.m >= 12 ? { y: v.y + 1, m: 1 } : { ...v, m: v.m + 1 }))
              }
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
                <path d="m9 18 6-6-6-6" />
              </svg>
            </button>
          </div>

          <div className="mb-1.5 grid grid-cols-7 gap-0.5 text-center">
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
              const iso = toIsoDate(view.y, view.m, day);
              const isToday = iso === todayIso;
              const isSelected =
                parsed && parsed.y === view.y && parsed.m === view.m && parsed.d === day;
              return (
                <button
                  key={`${view.y}-${view.m}-${day}`}
                  type="button"
                  {...getDayButtonProps(i, day)}
                  aria-label={formatIsoDateDdMmmYyyy(iso)}
                  className={[
                    "aspect-square rounded-md text-sm font-medium tabular-nums transition",
                    isSelected
                      ? "bg-sky-600 text-white shadow-[0_0_12px_rgb(14_165_233/0.35)] dark:bg-sky-500"
                      : "text-zinc-800 hover:bg-zinc-100 dark:text-zinc-200 dark:hover:bg-zinc-800",
                    isToday && !isSelected
                      ? "ring-1 ring-inset ring-sky-500/45 dark:ring-sky-400/35"
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
              className="w-full rounded-md py-1.5 text-xs font-medium text-sky-700 transition hover:bg-sky-50 dark:text-sky-300 dark:hover:bg-sky-950/40"
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
