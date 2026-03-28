"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { UnderlyingEntry } from "@/lib/strategy-builder/types";

type Props = {
  underlyings: UnderlyingEntry[];
  value: string;
  onChange: (stockCode: string) => void;
  disabled?: boolean;
  /** `field` = full-width search input; `ticker` = compact symbol row like trading terminals. */
  variant?: "field" | "ticker";
  /** Spot / index level (e.g. from option chain after fetch). */
  spot?: number | null;
  /** Day change % when available (optional). */
  changePct?: number | null;
  /**
   * When true with `variant="ticker"`, render as a flat strip (no box border) for embedding in the dark chain bar.
   */
  chainBar?: boolean;
};

function SearchIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <circle cx="11" cy="11" r="8" />
      <path d="m21 21-4.3-4.3" />
    </svg>
  );
}

function CheckIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M20 6 9 17l-5-5" />
    </svg>
  );
}

function formatSpot(n: number): string {
  return n.toLocaleString("en-IN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

export function OptionChainUnderlyingSearch({
  underlyings,
  value,
  onChange,
  disabled,
  variant = "field",
  spot = null,
  changePct = null,
  chainBar = false,
}: Props) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const rootRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const sorted = useMemo(
    () =>
      [...underlyings].sort((a, b) =>
        a.stock_code.localeCompare(b.stock_code),
      ),
    [underlyings],
  );

  const qn = q.trim().toLowerCase();
  const filtered = useMemo(() => {
    if (!qn) return sorted;
    return sorted.filter(
      (u) =>
        u.stock_code.toLowerCase().includes(qn) ||
        (u.long_name ?? "").toLowerCase().includes(qn),
    );
  }, [sorted, qn]);

  useEffect(() => {
    if (!open) return;
    const id = requestAnimationFrame(() => inputRef.current?.focus());
    return () => cancelAnimationFrame(id);
  }, [open]);

  const closeDropdown = useCallback(() => {
    setOpen(false);
    setQ("");
  }, []);

  useEffect(() => {
    if (!open) return;
    const fn = (e: MouseEvent) => {
      if (rootRef.current?.contains(e.target as Node)) return;
      closeDropdown();
    };
    document.addEventListener("mousedown", fn);
    return () => document.removeEventListener("mousedown", fn);
  }, [open, closeDropdown]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeDropdown();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, closeDropdown]);

  const select = (code: string) => {
    onChange(code);
    setOpen(false);
    setQ("");
  };

  const inputDisplay = open ? q : value;

  const openPicker = () => {
    if (disabled) return;
    setOpen(true);
    setQ(value);
  };

  const listSectionDefault = (
    <ul className="max-h-[min(60vh,20rem)] overflow-y-auto overscroll-contain py-1 lg:max-h-[min(18rem,50vh)]">
      {filtered.length === 0 ? (
        <li className="px-4 py-8 text-center text-sm text-zinc-500">
          No matches
        </li>
      ) : (
        filtered.map((u) => {
          const selected = u.stock_code === value;
          return (
            <li key={u.stock_code}>
              <button
                type="button"
                role="option"
                aria-selected={selected}
                className={[
                  "flex w-full items-center justify-between gap-3 px-4 py-2.5 text-left text-sm transition",
                  selected
                    ? "bg-sky-950/55 text-sky-400"
                    : "text-zinc-100 hover:bg-zinc-700/50",
                ].join(" ")}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => select(u.stock_code)}
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-semibold tracking-tight">
                    {u.stock_code}
                  </span>
                  {u.long_name ? (
                    <span
                      className={[
                        "mt-0.5 block truncate text-xs font-normal leading-snug",
                        selected ? "text-zinc-400" : "text-zinc-500",
                      ].join(" ")}
                    >
                      {u.long_name}
                    </span>
                  ) : null}
                </span>
                {selected ? (
                  <CheckIcon className="shrink-0 text-sky-400" />
                ) : (
                  <span className="size-[18px] shrink-0" aria-hidden />
                )}
              </button>
            </li>
          );
        })
      )}
    </ul>
  );

  /** Dark terminal-style list (chain bar picker). */
  const listSectionChainBar = (
    <ul className="mx-2 mb-2 max-h-[min(60vh,20rem)] overflow-y-auto overscroll-contain py-0.5 lg:max-h-[min(18rem,50vh)]">
      {filtered.length === 0 ? (
        <li className="px-4 py-10 text-center text-sm text-zinc-500">
          No matches
        </li>
      ) : (
        filtered.map((u) => {
          const selected = u.stock_code === value;
          return (
            <li key={u.stock_code}>
              <button
                type="button"
                role="option"
                aria-selected={selected}
                className={[
                  "flex w-full items-center justify-between gap-3 px-4 py-3 text-left text-sm transition",
                  selected
                    ? "bg-sky-100 dark:bg-zinc-600/35"
                    : "text-zinc-900 hover:bg-zinc-100 dark:text-zinc-100 dark:hover:bg-zinc-600/25",
                ].join(" ")}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => select(u.stock_code)}
              >
                <span className="min-w-0 flex-1">
                  <span
                    className={[
                      "block truncate text-sm font-semibold uppercase tracking-wide",
                      selected
                        ? "text-sky-800 dark:text-sky-400"
                        : "text-zinc-900 hover:text-sky-700 dark:text-zinc-100 dark:hover:text-sky-300",
                    ].join(" ")}
                  >
                    {u.stock_code}
                  </span>
                  {u.long_name ? (
                    <span className="mt-0.5 block truncate text-[11px] font-normal normal-case leading-snug text-zinc-500">
                      {u.long_name}
                    </span>
                  ) : null}
                </span>
                {selected ? (
                  <CheckIcon className="size-[1.125rem] shrink-0 text-sky-600 dark:text-sky-400" />
                ) : (
                  <span className="size-[18px] shrink-0" aria-hidden />
                )}
              </button>
            </li>
          );
        })
      )}
    </ul>
  );

  const filterInputDefault = (
    <div className="border-b border-zinc-700 p-2">
      <label className="sr-only" htmlFor="option-chain-underlying-filter">
        Filter symbols
      </label>
      <div className="relative flex items-center">
        <SearchIcon className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-zinc-500" />
        <input
          id="option-chain-underlying-filter"
          ref={inputRef}
          type="search"
          autoComplete="off"
          disabled={disabled}
          value={open ? q : ""}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Type stock name: SBIN, TCS etc."
          className="w-full rounded-md border border-zinc-600/80 bg-zinc-900/80 py-2 pl-9 pr-2 text-sm text-zinc-100 outline-none placeholder:text-zinc-500 focus:border-sky-500/50 focus:ring-1 focus:ring-sky-500/30"
        />
      </div>
    </div>
  );

  const filterInputChainBar = (
    <div className="border-b border-zinc-200 p-2.5 dark:border-zinc-600/50">
      <label className="sr-only" htmlFor="option-chain-underlying-filter-chain">
        Filter symbols
      </label>
      <div className="relative flex items-center">
        <SearchIcon className="pointer-events-none absolute left-3 top-1/2 size-[1.125rem] -translate-y-1/2 text-zinc-500" />
        <input
          id="option-chain-underlying-filter-chain"
          ref={inputRef}
          type="search"
          autoComplete="off"
          disabled={disabled}
          value={open ? q : ""}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Type stock name: SBIN, TCS etc."
          className="w-full rounded-lg border border-zinc-300 bg-white py-2.5 pl-10 pr-3 text-sm text-zinc-900 outline-none placeholder:text-zinc-500 focus:border-zinc-400 focus:ring-1 focus:ring-sky-500/30 dark:border-zinc-600/50 dark:bg-[#121214] dark:text-zinc-100 dark:focus:border-zinc-500 dark:focus:ring-sky-500/25"
        />
      </div>
    </div>
  );

  const changeStr =
    changePct != null && Number.isFinite(changePct)
      ? `${changePct >= 0 ? "+" : ""}${changePct.toFixed(2)}%`
      : null;

  if (variant === "ticker") {
    const rowClass = chainBar
      ? "flex min-w-0 flex-wrap items-center gap-x-2.5 gap-y-1 sm:gap-x-3"
      : "flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 rounded-md border border-zinc-200 bg-white px-3 py-2 shadow-sm sm:gap-x-3 dark:border-zinc-700 dark:bg-zinc-950/80";

    return (
      <div
        ref={rootRef}
        className={`relative min-w-0 flex-1 ${open ? "z-[300]" : "z-0"}`}
      >
        <div className={rowClass}>
          <button
            type="button"
            disabled={disabled}
            onClick={openPicker}
            className={
              chainBar
                ? "flex min-w-0 max-w-full items-center gap-x-2.5 text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-500/40 focus-visible:ring-offset-2 focus-visible:ring-offset-zinc-100 disabled:opacity-50 dark:focus-visible:ring-offset-[#1b1c1f] sm:gap-x-3"
                : "flex min-w-0 max-w-full items-center gap-x-2 text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-500/35 disabled:opacity-50 sm:gap-x-3"
            }
          >
            <SearchIcon
              className={
                chainBar
                  ? "pointer-events-none size-[1.125rem] shrink-0 text-zinc-500 dark:text-zinc-500"
                  : "pointer-events-none size-[1.125rem] shrink-0 text-zinc-500 dark:text-zinc-400"
              }
            />
            <span
              className={
                value
                  ? chainBar
                    ? "min-w-0 truncate text-sm font-semibold tracking-tight text-zinc-900 hover:text-sky-700 dark:text-white dark:hover:text-sky-300"
                    : "min-w-0 truncate text-sm font-semibold tracking-tight text-zinc-900 hover:text-sky-700 dark:text-zinc-100 dark:hover:text-sky-300"
                  : chainBar
                    ? "min-w-0 truncate text-sm font-normal tracking-normal text-zinc-500 hover:text-zinc-600 dark:text-zinc-500 dark:hover:text-zinc-400"
                    : "min-w-0 truncate text-sm font-normal tracking-normal text-zinc-400 hover:text-zinc-500 dark:text-zinc-500 dark:hover:text-zinc-400"
              }
            >
              {value || "Select underlying"}
            </span>
          </button>
          {spot != null && Number.isFinite(spot) ? (
            <span
              className={
                chainBar
                  ? "shrink-0 tabular-nums text-sm font-semibold text-zinc-900 dark:text-white"
                  : "shrink-0 tabular-nums text-sm font-semibold text-zinc-800 dark:text-zinc-100"
              }
            >
              {formatSpot(spot)}
            </span>
          ) : chainBar ? null : (
            <span className="shrink-0 text-sm font-medium text-zinc-400 dark:text-zinc-500">
              —
            </span>
          )}
          {chainBar ? (
            changeStr ? (
              <span
                className={
                  (changePct ?? 0) < 0
                    ? "shrink-0 text-sm font-semibold tabular-nums text-orange-700 dark:text-orange-400"
                    : "shrink-0 text-sm font-semibold tabular-nums text-emerald-700 dark:text-emerald-400"
                }
              >
                {changeStr}
              </span>
            ) : null
          ) : null}
        </div>

        {open && !disabled ? (
          <>
            <div
              className="fixed inset-0 z-[295] bg-black/50 lg:hidden"
              role="presentation"
              aria-hidden
              onClick={closeDropdown}
            />
            <div
              className={
                chainBar
                  ? "fixed inset-x-0 bottom-0 z-[300] flex max-h-[min(70vh,24rem)] flex-col overflow-hidden rounded-t-2xl border border-zinc-200 bg-white shadow-2xl dark:border-zinc-500/35 dark:bg-[#2c2d32] lg:absolute lg:inset-x-auto lg:bottom-auto lg:left-0 lg:right-auto lg:top-full lg:mt-1.5 lg:max-h-[min(22rem,72vh)] lg:w-[min(calc(100vw-1.5rem),20rem)] lg:rounded-lg lg:border-zinc-200 lg:dark:border-zinc-500/30 lg:shadow-xl"
                  : "fixed inset-x-0 bottom-0 z-[300] flex max-h-[min(70vh,24rem)] flex-col overflow-hidden rounded-t-2xl border border-zinc-600 bg-zinc-800 shadow-2xl lg:absolute lg:inset-x-auto lg:bottom-auto lg:left-0 lg:right-auto lg:top-full lg:mt-1 lg:w-[min(calc(100vw-2rem),22rem)] lg:max-h-[min(22rem,70vh)] lg:rounded-lg"
              }
              role="listbox"
              aria-label="Underlying symbols"
            >
              {chainBar ? (
                <>
                  {filterInputChainBar}
                  {listSectionChainBar}
                </>
              ) : (
                <>
                  <div className="flex items-center justify-between border-b border-zinc-700 px-3 py-2 lg:hidden">
                    <span className="text-sm font-medium text-zinc-200">
                      Select underlying
                    </span>
                    <button
                      type="button"
                      className="rounded-lg p-2 text-zinc-400 hover:bg-zinc-700/80"
                      aria-label="Close"
                      onClick={closeDropdown}
                    >
                      <span className="text-lg leading-none" aria-hidden>
                        ×
                      </span>
                    </button>
                  </div>
                  {filterInputDefault}
                  {listSectionDefault}
                </>
              )}
            </div>
          </>
        ) : null}
      </div>
    );
  }

  return (
    <div
      ref={rootRef}
      className={`relative min-w-[min(100%,16rem)] flex-1 lg:max-w-md ${open ? "z-[300]" : "z-0"}`}
    >
      <div
        className={[
          "overflow-hidden border border-zinc-600/90 bg-zinc-800 shadow-sm transition-colors",
          open ? "rounded-t-lg rounded-b-none" : "rounded-lg",
        ].join(" ")}
      >
        <label className="sr-only" htmlFor="option-chain-underlying-search">
          Search underlying
        </label>
        <div className="relative flex items-center">
          <SearchIcon className="pointer-events-none absolute left-3 top-1/2 size-[1.125rem] -translate-y-1/2 text-zinc-500" />
          <input
            id="option-chain-underlying-search"
            ref={inputRef}
            type="search"
            autoComplete="off"
            disabled={disabled}
            value={inputDisplay}
            onChange={(e) => {
              setQ(e.target.value);
              if (!open) setOpen(true);
            }}
            onFocus={() => {
              setOpen(true);
              setQ(value);
            }}
            placeholder="Type stock name: SBIN, TCS etc."
            className="w-full border-0 bg-transparent py-2.5 pl-10 pr-3 text-sm text-zinc-100 outline-none ring-0 placeholder:text-zinc-500 focus:ring-0 disabled:cursor-not-allowed disabled:opacity-50"
          />
        </div>
      </div>

      {open && !disabled ? (
        <>
          <div
            className="fixed inset-0 z-[295] bg-black/50 lg:hidden"
            role="presentation"
            aria-hidden
            onClick={closeDropdown}
          />
          <div
            className="fixed inset-x-0 bottom-0 z-[300] max-h-[min(70vh,24rem)] overflow-hidden rounded-t-2xl border border-zinc-600 bg-zinc-800 shadow-2xl lg:absolute lg:inset-x-auto lg:bottom-auto lg:left-0 lg:right-0 lg:top-full lg:max-h-[min(22rem,70vh)] lg:rounded-b-lg lg:rounded-t-none lg:border-t-0"
            role="listbox"
            aria-label="Underlying symbols"
          >
            <div className="flex items-center justify-between border-b border-zinc-700 px-3 py-2 lg:hidden">
              <span className="text-sm font-medium text-zinc-200">
                Select underlying
              </span>
              <button
                type="button"
                className="rounded-lg p-2 text-zinc-400 hover:bg-zinc-700/80"
                aria-label="Close"
                onClick={closeDropdown}
              >
                <span className="text-lg leading-none" aria-hidden>
                  ×
                </span>
              </button>
            </div>
            {listSectionDefault}
          </div>
        </>
      ) : null}
    </div>
  );
}
