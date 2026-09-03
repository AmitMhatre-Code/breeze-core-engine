"use client";

import { useCallback, useId, useRef, useState } from "react";
import { useListboxMenu, useListboxOutsideClose } from "@/lib/ui/use-listbox-menu";

export type SelectOption<T extends string> = { value: T; label: string };

function ChevronDown({ open }: { open: boolean }) {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 16 16"
      fill="none"
      aria-hidden
      className={[
        "shrink-0 text-muted transition-transform duration-200",
        open ? "-rotate-180" : "",
      ].join(" ")}
    >
      <path
        d="M4 6l4 4 4-4"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/**
 * The app's listbox dropdown, as a reusable control.
 *
 * A native `<select>` cannot be styled to match `.app-input`: the browser draws its own
 * box and chevron, so it sits at a different height and weight from every input beside it.
 * The app already solves this with a button-triggered listbox (the settings and performance
 * pickers); this is that same pattern — `useListboxMenu` for keyboard nav, the trigger
 * wearing `.app-input` so it lines up with sibling fields — packaged rather than copied a
 * fifth time.
 */
export function Select<T extends string>({
  value,
  options,
  onChange,
  disabled,
  labelledBy,
  ariaLabel,
  className,
}: {
  value: T;
  options: ReadonlyArray<SelectOption<T>>;
  onChange: (value: T) => void;
  disabled?: boolean;
  /** Id of an external label element. Prefer this over `ariaLabel` when a label exists. */
  labelledBy?: string;
  ariaLabel?: string;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const listId = useId();

  const selected = options.find((o) => o.value === value) ?? options[0];
  const close = useCallback(() => setOpen(false), []);

  useListboxOutsideClose(open, rootRef, close);

  const { highlightIndex, handleTriggerKeyDown } = useListboxMenu({
    open,
    optionCount: options.length,
    onOpen: () => setOpen(true),
    onClose: () => {
      close();
      triggerRef.current?.focus();
    },
    onSelectIndex: (index) => {
      const opt = options[index];
      if (opt) onChange(opt.value);
      close();
      triggerRef.current?.focus();
    },
    triggerRef,
    listRef,
  });

  return (
    <div ref={rootRef} className={["relative w-full text-left", className].filter(Boolean).join(" ")}>
      <button
        ref={triggerRef}
        type="button"
        disabled={disabled}
        className={[
          "app-input flex items-center justify-between gap-3 text-left",
          "disabled:cursor-not-allowed disabled:opacity-50",
          open ? "border-accent-strong bg-panel" : "",
        ].join(" ")}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={listId}
        aria-labelledby={labelledBy}
        aria-label={labelledBy ? undefined : ariaLabel}
        onClick={() => setOpen((v) => !v)}
        onKeyDown={handleTriggerKeyDown}
      >
        <span className="truncate">{selected?.label ?? ""}</span>
        <ChevronDown open={open} />
      </button>

      {open ? (
        <ul
          ref={listRef}
          id={listId}
          role="listbox"
          aria-labelledby={labelledBy}
          aria-label={labelledBy ? undefined : ariaLabel}
          // Opening moves focus onto an option, so the handler has to live here as well as
          // on the trigger — otherwise arrow keys stop working the moment the list opens,
          // and only the option that happened to receive focus can be chosen.
          onKeyDown={handleTriggerKeyDown}
          className="absolute left-0 right-0 top-[calc(100%+6px)] z-40 overflow-hidden rounded-[10px] border border-border bg-elevated py-1 shadow-pop"
        >
          {options.map((opt, index) => {
            const isSelected = opt.value === value;
            const highlighted = index === highlightIndex;
            return (
              <li key={opt.value} role="presentation">
                <button
                  type="button"
                  role="option"
                  tabIndex={-1}
                  data-menu-index={index}
                  aria-selected={isSelected}
                  className={[
                    "flex w-full items-center px-3 py-2 text-left text-body transition-colors",
                    highlighted
                      ? "bg-accent-tint text-accent-on-tint"
                      : isSelected
                        ? "bg-panel2 font-medium text-text"
                        : "text-text hover:bg-panel2",
                  ].join(" ")}
                  onClick={() => {
                    onChange(opt.value);
                    close();
                    triggerRef.current?.focus();
                  }}
                >
                  {opt.label}
                </button>
              </li>
            );
          })}
        </ul>
      ) : null}
    </div>
  );
}
