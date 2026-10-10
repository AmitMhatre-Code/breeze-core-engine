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
  MONTH_SHORT,
  formatIsoDateDdMmmYyyy,
  parseIsoDateParts,
  parseTypedDate,
  toIsoDate,
} from "@/lib/format-iso-date";
import { useCalendarKeyboard } from "@/lib/ui/use-calendar-keyboard";
import { usePopoverPlacement } from "@/lib/ui/use-popover-placement";

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

/** Years shown per page of the year grid. */
const YEARS_PER_PAGE = 12;

/** What the popover grid shows: a month's days, a year's months, or a page of years. */
type CalendarMode = "days" | "months" | "years";

function yearPageStart(year: number): number {
  return year - (((year % YEARS_PER_PAGE) + YEARS_PER_PAGE) % YEARS_PER_PAGE);
}

function Chevron({ dir }: { dir: "left" | "right" }) {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
      <path d={dir === "left" ? "m15 18-6-6 6-6" : "m9 18 6-6-6-6"} />
    </svg>
  );
}

const NAV_BUTTON =
  "inline-flex size-11 items-center justify-center rounded-lg text-muted transition hover:bg-panel2 hover:text-foreground sm:size-8";

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
  /** Latest date that may be chosen or typed (`YYYY-MM-DD`); later ones are refused. */
  max?: string;
  /** `compact` is the 34px monospace field used in toolbars, filter panels and dialogs. */
  size?: "default" | "compact";
  /** Accessible name for the field when no visible `<label>` wraps it. */
  ariaLabel?: string;
};

export function DatePicker({
  id,
  value,
  onChange,
  disabled,
  className,
  placeholder = "dd-MMM-yyyy",
  max,
  size = "default",
  ariaLabel,
}: DatePickerProps) {
  const rootRef = useRef<HTMLDivElement>(null);
  const fieldRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const popoverId = useId();
  const [open, setOpen] = useState(false);
  const { popoverRef, style: popoverStyle } = usePopoverPlacement(open, fieldRef);
  const compact = size === "compact";
  const afterMax = useCallback((iso: string) => Boolean(max) && iso > max!, [max]);

  const parsed = useMemo(() => parseIsoDateParts(value), [value]);
  const [mode, setMode] = useState<CalendarMode>("days");

  // The field is typeable: `draft` is the text as typed, committed on Enter or blur.
  const [draft, setDraft] = useState(() => (parsed ? formatIsoDateDdMmmYyyy(value) : ""));
  const [invalid, setInvalid] = useState(false);
  const [draftFor, setDraftFor] = useState(value);
  if (draftFor !== value) {
    // A new value from outside (a calendar pick, a reset) replaces whatever was typed.
    setDraftFor(value);
    setDraft(parsed ? formatIsoDateDdMmmYyyy(value) : "");
    setInvalid(false);
  }

  const [view, setView] = useState(() => {
    if (parsed) return { y: parsed.y, m: parsed.m };
    const now = new Date();
    return { y: now.getFullYear(), m: now.getMonth() + 1 };
  });

  const syncView = useCallback(() => {
    setMode("days");
    if (parsed) setView({ y: parsed.y, m: parsed.m });
    else {
      const now = new Date();
      setView({ y: now.getFullYear(), m: now.getMonth() + 1 });
    }
  }, [parsed]);

  /** Commit the typed text; returns false (and leaves it for correcting) when unreadable. */
  const commitDraft = useCallback((): boolean => {
    const text = draft.trim();
    if (!text) {
      setDraft(parsed ? formatIsoDateDdMmmYyyy(value) : "");
      setInvalid(false);
      return true;
    }
    const iso = parseTypedDate(text);
    if (!iso || afterMax(iso)) {
      setInvalid(true);
      return false;
    }
    setDraft(formatIsoDateDdMmmYyyy(iso));
    setInvalid(false);
    const p = parseIsoDateParts(iso);
    if (p) setView({ y: p.y, m: p.m });
    if (iso !== value) onChange(iso);
    return true;
  }, [draft, parsed, value, onChange, afterMax]);

  const cells = useMemo(() => monthMatrix(view.y, view.m), [view.y, view.m]);

  const selectDay = useCallback(
    (day: number) => {
      const iso = toIsoDate(view.y, view.m, day);
      if (afterMax(iso)) return;
      onChange(iso);
      setOpen(false);
    },
    [onChange, view.y, view.m, afterMax],
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
    // Captured and stopped, so Escape closes only the calendar and not the dialog or
    // filter panel the picker sits in (both close on their own Escape listeners).
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.stopPropagation();
      closeCalendar();
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey, true);
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
    open: open && mode === "days",
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
    if (afterMax(toIsoDate(y, m, d))) return;
    onChange(toIsoDate(y, m, d));
    setView({ y, m });
    setMode("days");
    setOpen(false);
  }, [onChange, afterMax]);

  const yearStart = yearPageStart(view.y);
  const todayYm = todayParts ? todayParts.y * 12 + todayParts.m : null;

  return (
    <div
      ref={rootRef}
      className={["relative", compact ? "" : "min-w-[11rem]", className].filter(Boolean).join(" ")}
    >
      <div
        ref={fieldRef}
        className={[
          "flex h-11 w-full items-center rounded-[9px] border bg-panel2 text-foreground transition",
          compact ? "gap-2 px-2.5 font-mono text-heading sm:h-[34px]" : "gap-2.5 px-3 text-sm sm:h-10",
          invalid
            ? "border-down focus-within:ring-2 focus-within:ring-down/25"
            : "border-border hover:border-accent/60 focus-within:border-accent focus-within:ring-2 focus-within:ring-accent/25",
          disabled ? "pointer-events-none opacity-45" : "",
        ].join(" ")}
      >
        <button
          ref={triggerRef}
          type="button"
          disabled={disabled}
          aria-label="Open calendar"
          aria-haspopup="dialog"
          aria-expanded={open}
          aria-controls={popoverId}
          className={[
            "flex shrink-0 items-center justify-center rounded-[6px] transition hover:text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/40",
            compact ? "size-6 text-faint" : "size-7 bg-panel text-muted",
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
          <CalendarIcon />
        </button>
        <input
          id={id}
          type="text"
          inputMode="text"
          autoComplete="off"
          spellCheck={false}
          // Compact fields size to a dd-MMM-yyyy date rather than the browser's 20-character
          // default, which overflowed the 240px Activity filter panel.
          size={compact ? 11 : undefined}
          disabled={disabled}
          value={draft}
          placeholder={placeholder}
          aria-label={ariaLabel}
          aria-invalid={invalid || undefined}
          title="Type a date, e.g. 01-Jan-2020 or 01/01/2020"
          className={[
            "flex-1 bg-transparent tabular-nums text-foreground outline-none placeholder:font-normal placeholder:text-faint",
            compact ? "min-w-[11ch]" : "min-w-0 font-medium",
          ].join(" ")}
          onChange={(e) => {
            setDraft(e.target.value);
            setInvalid(false);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              if (commitDraft()) setOpen(false);
            }
          }}
          onBlur={() => {
            // Leaving the field with unreadable text puts the last good date back.
            if (!commitDraft()) {
              setDraft(parsed ? formatIsoDateDdMmmYyyy(value) : "");
              setInvalid(false);
            }
          }}
        />
      </div>

      {open ? (
        <div
          id={popoverId}
          role="dialog"
          aria-label="Choose date"
          ref={popoverRef}
          style={popoverStyle}
          // Most pickers sit inside a <label>. A click on a button that the click itself
          // removes (a year or month in the grids, the heading as it switches) reaches the
          // label from a detached target, and Safari then treats it as a click on the label
          // and forwards it to the calendar toggle, closing the calendar mid-pick. Cancelling
          // the default stops the label acting; the buttons here have no default of their own.
          onClick={(e) => e.preventDefault()}
          className="z-50 w-72 max-w-[calc(100vw-1rem)] rounded-[10px] border border-border bg-elevated p-3 font-sans text-sm shadow-pop"
        >
          <div className="mb-2 flex items-center justify-between gap-1">
            <button
              type="button"
              className={NAV_BUTTON}
              aria-label={
                mode === "days" ? "Previous month" : mode === "months" ? "Previous year" : "Previous years"
              }
              onClick={() =>
                setView((v) =>
                  mode === "days"
                    ? v.m <= 1
                      ? { y: v.y - 1, m: 12 }
                      : { ...v, m: v.m - 1 }
                    : { ...v, y: v.y - (mode === "months" ? 1 : YEARS_PER_PAGE) },
                )
              }
            >
              <Chevron dir="left" />
            </button>
            {mode === "years" ? (
              <span className="min-w-0 flex-1 text-center text-sm font-semibold text-foreground">
                {yearStart}–{yearStart + YEARS_PER_PAGE - 1}
              </span>
            ) : (
              <button
                type="button"
                className="min-w-0 flex-1 rounded-lg py-1 text-center text-sm font-semibold text-foreground transition hover:bg-panel2"
                aria-label={mode === "days" ? "Choose month and year" : "Choose year"}
                onClick={() => setMode(mode === "days" ? "months" : "years")}
              >
                {mode === "days" ? `${MONTH_LABELS[view.m - 1]} ${view.y}` : view.y}
              </button>
            )}
            <button
              type="button"
              className={NAV_BUTTON}
              aria-label={mode === "days" ? "Next month" : mode === "months" ? "Next year" : "Next years"}
              onClick={() =>
                setView((v) =>
                  mode === "days"
                    ? v.m >= 12
                      ? { y: v.y + 1, m: 1 }
                      : { ...v, m: v.m + 1 }
                    : { ...v, y: v.y + (mode === "months" ? 1 : YEARS_PER_PAGE) },
                )
              }
            >
              <Chevron dir="right" />
            </button>
          </div>

          {mode === "months" ? (
            <div className="grid grid-cols-3 gap-1">
              {MONTH_SHORT.map((label, i) => {
                const m = i + 1;
                const isSelected = parsed?.y === view.y && parsed?.m === m;
                const isCurrent = todayYm === view.y * 12 + m;
                return (
                  <button
                    key={label}
                    type="button"
                    disabled={afterMax(toIsoDate(view.y, m, 1))}
                    aria-label={`${MONTH_LABELS[i]} ${view.y}`}
                    aria-pressed={isSelected}
                    className={[
                      "rounded-lg py-2.5 text-sm font-medium transition disabled:pointer-events-none disabled:opacity-35",
                      isSelected ? "bg-accent-strong text-accent-ink" : "text-foreground hover:bg-panel2",
                      isCurrent && !isSelected ? "ring-1 ring-inset ring-accent/50" : "",
                    ].join(" ")}
                    onClick={() => {
                      setView((v) => ({ ...v, m }));
                      setMode("days");
                    }}
                  >
                    {label}
                  </button>
                );
              })}
            </div>
          ) : mode === "years" ? (
            <div className="grid grid-cols-3 gap-1">
              {Array.from({ length: YEARS_PER_PAGE }, (_, i) => yearStart + i).map((y) => {
                const isSelected = parsed?.y === y;
                const isCurrent = todayParts?.y === y;
                return (
                  <button
                    key={y}
                    type="button"
                    disabled={afterMax(toIsoDate(y, 1, 1))}
                    aria-pressed={isSelected}
                    className={[
                      "rounded-lg py-2.5 text-sm font-medium tabular-nums transition disabled:pointer-events-none disabled:opacity-35",
                      isSelected ? "bg-accent-strong text-accent-ink" : "text-foreground hover:bg-panel2",
                      isCurrent && !isSelected ? "ring-1 ring-inset ring-accent/50" : "",
                    ].join(" ")}
                    onClick={() => {
                      setView((v) => ({ ...v, y }));
                      setMode("months");
                    }}
                  >
                    {y}
                  </button>
                );
              })}
            </div>
          ) : (
            <>
            <div className="mb-1.5 grid grid-cols-7 gap-0.5 text-center">
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
                const iso = toIsoDate(view.y, view.m, day);
                const isToday = iso === todayIso;
                const isSelected =
                  parsed && parsed.y === view.y && parsed.m === view.m && parsed.d === day;
                // aria-disabled, not disabled: a disabled button cannot take focus, which
                // would strand arrow-key navigation on the day before it.
                const outOfRange = afterMax(iso);
                return (
                  <button
                    key={`${view.y}-${view.m}-${day}`}
                    type="button"
                    {...getDayButtonProps(i, day)}
                    aria-label={formatIsoDateDdMmmYyyy(iso)}
                    aria-disabled={outOfRange || undefined}
                    className={[
                      "aspect-square rounded-lg text-sm font-medium tabular-nums transition",
                      isSelected
                        ? "bg-accent-strong text-accent-ink"
                        : outOfRange
                          ? "cursor-not-allowed text-faint opacity-50"
                          : "text-foreground hover:bg-panel2",
                      isToday && !isSelected ? "ring-1 ring-inset ring-accent/50" : "",
                    ].join(" ")}
                    onClick={() => selectDay(day)}
                  >
                    {day}
                  </button>
                );
              })}
            </div>
            </>
          )}

          <div className="mt-3 border-t border-border-soft pt-2">
            <button
              type="button"
              className="w-full rounded-lg py-1.5 text-xs font-medium text-accent-strong transition hover:bg-accent-tint disabled:pointer-events-none disabled:opacity-35"
              disabled={afterMax(todayIso)}
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
