"use client";

import { useEffect, useRef, useState } from "react";

type Props = {
  dates: string[];
  value: string;
  onChange: (expiryDisplay: string) => void;
  disabled?: boolean;
};

/** Custom listbox (same pattern as `UnderlyingSearchPill`) so fonts match; native `<select>` menus use OS styling. */
export function ExpirySelectPill({ dates, value, onChange, disabled }: Props) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  const pick = (d: string) => {
    onChange(d);
    setOpen(false);
  };

  useEffect(() => {
    if (!open) return;
    const fn = (e: MouseEvent) => {
      if (rootRef.current?.contains(e.target as Node)) return;
      setOpen(false);
    };
    document.addEventListener("mousedown", fn);
    return () => document.removeEventListener("mousedown", fn);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <div
      ref={rootRef}
      className={`relative min-w-[min(100%,12rem)] flex-1 lg:max-w-md ${open ? "z-[300]" : "z-0"}`}
    >
      <span className="mb-1.5 block text-xs font-medium text-zinc-600 dark:text-zinc-400">
        Expiry (earliest first)
      </span>
      <button
        type="button"
        disabled={disabled}
        aria-expanded={open}
        aria-haspopup="listbox"
        aria-label="Expiry date"
        onClick={() => !disabled && setOpen((o) => !o)}
        className="flex w-full min-w-0 items-center justify-between gap-2 rounded-xl border border-zinc-200 bg-white px-3.5 py-2.5 text-left text-sm text-zinc-900 shadow-sm outline-none transition hover:border-zinc-300 focus-visible:border-sky-500 focus-visible:ring-4 focus-visible:ring-sky-500/15 disabled:cursor-not-allowed disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:hover:border-zinc-600 dark:focus-visible:border-sky-400 dark:focus-visible:ring-sky-400/20"
      >
        <span className="min-w-0 flex-1 truncate">
          {value ? (
            <span className="font-semibold">{value}</span>
          ) : (
            <span className="text-zinc-400 dark:text-zinc-500">
              Select expiry…
            </span>
          )}
        </span>
        <span className="shrink-0 text-zinc-400" aria-hidden>
          ▾
        </span>
      </button>

      {open ? (
        <>
          <div
            className="fixed inset-0 z-[295] bg-black/45 lg:hidden"
            role="presentation"
            aria-hidden
            onClick={() => setOpen(false)}
          />
          <div
            className="fixed inset-0 z-[300] flex flex-col bg-zinc-50 dark:bg-zinc-950 lg:absolute lg:inset-x-auto lg:inset-y-auto lg:left-0 lg:top-full lg:z-[300] lg:mt-1.5 lg:max-h-[min(22rem,70vh)] lg:w-full lg:min-w-[18rem] lg:max-w-lg lg:rounded-xl lg:border lg:border-zinc-200 lg:bg-white lg:shadow-xl lg:dark:border-zinc-700 lg:dark:bg-zinc-900"
            role="listbox"
            aria-label="Expiry dates"
          >
            <div className="flex shrink-0 items-center justify-between border-b border-zinc-200 px-3 py-2.5 dark:border-zinc-800 lg:hidden">
              <span className="text-sm font-semibold text-zinc-900 dark:text-zinc-50">
                Choose expiry
              </span>
              <button
                type="button"
                className="rounded-lg p-2 text-zinc-500 hover:bg-zinc-200/80 dark:hover:bg-zinc-800"
                aria-label="Close"
                onClick={() => setOpen(false)}
              >
                <span className="text-xl leading-none" aria-hidden>
                  ×
                </span>
              </button>
            </div>

            <ul className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-1 lg:max-h-[min(18rem,50vh)]">
              {value ? (
                <li>
                  <button
                    type="button"
                    role="option"
                    aria-selected={false}
                    className="flex w-full rounded-lg px-3 py-2 text-left text-sm font-medium text-zinc-600 transition hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-800/80"
                    onClick={() => pick("")}
                  >
                    Clear expiry
                  </button>
                </li>
              ) : null}
              {dates.length === 0 ? (
                <li className="px-3 py-6 text-center text-sm text-zinc-500 dark:text-zinc-400">
                  No expiries for this underlying
                </li>
              ) : (
                dates.map((d) => (
                  <li key={d}>
                    <button
                      type="button"
                      role="option"
                      aria-selected={d === value}
                      className={`flex w-full rounded-lg px-3 py-2 text-left text-sm transition hover:bg-zinc-100 dark:hover:bg-zinc-800/80 ${
                        d === value
                          ? "bg-sky-50 text-sky-900 dark:bg-sky-950/40 dark:text-sky-100"
                          : "text-zinc-900 dark:text-zinc-100"
                      }`}
                      onClick={() => pick(d)}
                    >
                      <span className="font-semibold">{d}</span>
                    </button>
                  </li>
                ))
              )}
            </ul>
          </div>
        </>
      ) : null}
    </div>
  );
}
