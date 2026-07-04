"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { QuoteSourceBadge } from "@/components/market-data/QuoteSourceBadge";
import type { QuoteMeta, UnderlyingEntry } from "@/lib/strategy-builder/types";
import {
  useComboboxBlurClose,
  useListboxCombobox,
} from "@/lib/ui/use-listbox-combobox";

type Props = {
  underlyings: UnderlyingEntry[];
  value: string;
  onChange: (stockCode: string) => void;
  disabled?: boolean;
  /** `field` = full-width search input; `ticker` = compact symbol row like trading terminals. */
  variant?: "field" | "ticker";
  /** Spot / index level (e.g. from option chain after fetch). */
  spot?: number | null;
  /** When true, show a loading placeholder instead of spot. */
  loading?: boolean;
  /** Day change % when available (optional). */
  changePct?: number | null;
  /** Quote provenance for loaded chain (WebSocket / Bhavcopy / API). */
  quoteMeta?: QuoteMeta | null;
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
  loading = false,
  changePct = null,
  quoteMeta = null,
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

  const handleSelect = useCallback(
    (u: UnderlyingEntry) => {
      onChange(u.stock_code);
      closeDropdown();
    },
    [onChange, closeDropdown],
  );

  const { highlightIndex, listRef, handleKeyDown } = useListboxCombobox({
    open,
    options: filtered,
    onSelect: handleSelect,
    onClose: closeDropdown,
  });

  const handleInputBlur = useComboboxBlurClose(rootRef, [], closeDropdown);

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
    closeDropdown();
  };

  const inputDisplay = open ? q : value;

  const handleInputFocus = () => {
    if (disabled) return;
    setOpen(true);
    setQ(value);
  };

  const handleInputChange = (next: string) => {
    setQ(next);
    if (!open) setOpen(true);
  };

  const renderUnderlyingList = (
    variant: "default" | "chainBar",
    options: UnderlyingEntry[],
  ) => {
    const ulClass =
      variant === "chainBar"
        ? "mx-2 mb-2 max-h-[min(60vh,20rem)] overflow-y-auto overscroll-contain py-0.5 lg:max-h-[min(18rem,50vh)]"
        : "max-h-[min(60vh,20rem)] overflow-y-auto overscroll-contain py-1 lg:max-h-[min(18rem,50vh)]";

    return (
      <ul ref={listRef} className={ulClass}>
        {options.length === 0 ? (
          <li
            className={[
              "text-center text-sm text-muted",
              variant === "chainBar" ? "px-4 py-10" : "px-4 py-8",
            ].join(" ")}
          >
            No matches
          </li>
        ) : (
          options.map((u, index) => {
            const selected = u.stock_code === value;
            const highlighted = index === highlightIndex;
            const chainBar = variant === "chainBar";
            return (
              <li key={u.stock_code}>
                <button
                  type="button"
                  role="option"
                  tabIndex={-1}
                  aria-selected={selected}
                  data-combobox-index={index}
                  className={[
                    "flex w-full items-center justify-between gap-3 text-left text-sm transition",
                    chainBar ? "px-4 py-3" : "px-4 py-2.5",
                    selected
                      ? "bg-accent-tint"
                      : highlighted
                        ? "bg-panel2"
                        : "text-foreground hover:bg-panel2",
                  ].join(" ")}
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => select(u.stock_code)}
                >
                  <span className="min-w-0 flex-1">
                    <span
                      className={[
                        "block truncate font-semibold tracking-tight",
                        chainBar ? "text-sm uppercase tracking-wide" : "",
                        chainBar
                          ? selected || highlighted
                            ? "text-accent-strong"
                            : "text-foreground hover:text-accent-strong"
                          : "text-foreground",
                      ].join(" ")}
                    >
                      {u.stock_code}
                    </span>
                    {u.long_name ? (
                      <span
                        className={[
                          "mt-0.5 block truncate text-xs font-normal leading-snug text-faint",
                          chainBar ? "text-[13px] normal-case" : "",
                        ].join(" ")}
                      >
                        {u.long_name}
                      </span>
                    ) : null}
                  </span>
                  {selected ? (
                    <CheckIcon
                      className={
                        chainBar
                          ? "size-[1.125rem] shrink-0 text-accent-strong"
                          : "shrink-0 text-accent-strong"
                      }
                    />
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
  };

  const filterInputDefault = (
    <div className="border-b border-border-soft p-2">
      <label className="sr-only" htmlFor="option-chain-underlying-filter">
        Filter symbols
      </label>
      <div className="relative flex items-center">
        <SearchIcon className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-faint" />
        <input
          id="option-chain-underlying-filter"
          ref={inputRef}
          data-scrip-input
          type="search"
          autoComplete="off"
          disabled={disabled}
          value={open ? q : ""}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={handleKeyDown}
          onBlur={handleInputBlur}
          placeholder="Type stock name: SBIN, TCS etc."
          className="w-full rounded-[9px] border border-border bg-panel2 py-2 pl-9 pr-2 font-mono text-sm text-foreground outline-none placeholder:text-faint focus:border-accent focus:ring-1 focus:ring-accent/30"
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
      : "flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 rounded-[9px] border border-border bg-panel2 px-3 py-2 sm:gap-x-3";

    const tickerRow = (
      <div className={rowClass}>
        <label
          className={
            chainBar
              ? "flex min-w-0 max-w-full flex-1 items-center gap-x-2.5 sm:gap-x-3"
              : "flex min-w-0 max-w-full flex-1 items-center gap-x-2 sm:gap-x-3"
          }
        >
          <span className="sr-only">Search underlying</span>
          <SearchIcon className="pointer-events-none size-[1.125rem] shrink-0 text-faint" />
          <input
            id="option-chain-underlying-ticker"
            ref={inputRef}
            data-scrip-input
            type="search"
            autoComplete="off"
            disabled={disabled}
            value={inputDisplay}
            onChange={(e) => handleInputChange(e.target.value)}
            onFocus={handleInputFocus}
            onKeyDown={handleKeyDown}
            onBlur={handleInputBlur}
            placeholder="Select underlying"
            className="min-w-0 flex-1 truncate border-0 bg-transparent p-0 font-mono text-sm font-semibold tracking-tight text-foreground outline-none ring-0 placeholder:font-normal placeholder:tracking-normal placeholder:text-faint focus:ring-0 disabled:cursor-not-allowed disabled:text-faint"
          />
        </label>
        {loading ? (
          <span className="shrink-0 animate-pulse font-mono text-sm tabular-nums text-faint">
            …
          </span>
        ) : spot != null && Number.isFinite(spot) ? (
          <span className="shrink-0 font-mono text-sm font-semibold tabular-nums text-foreground">
            {formatSpot(spot)}
          </span>
        ) : chainBar ? null : (
          <span className="shrink-0 text-sm font-medium text-faint">—</span>
        )}
        {chainBar ? (
          changeStr ? (
            <span
              className={[
                "shrink-0 font-mono text-sm font-semibold tabular-nums",
                (changePct ?? 0) < 0 ? "text-down" : "text-up",
              ].join(" ")}
            >
              {changeStr}
            </span>
          ) : null
        ) : null}
      </div>
    );

    return (
      <div
        ref={rootRef}
        className={`relative min-w-0 flex-1 ${open ? "z-[300]" : "z-0"}`}
      >
        {chainBar ? (
          <div className="flex min-w-0 flex-1 flex-col gap-0.5">
            {tickerRow}
            {quoteMeta ? (
              <div className="flex w-full justify-end">
                <QuoteSourceBadge
                  meta={quoteMeta}
                  variant="compact"
                  className="shrink-0"
                />
              </div>
            ) : null}
          </div>
        ) : (
          tickerRow
        )}

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
                  ? "fixed inset-x-0 bottom-0 z-[300] flex max-h-[min(70vh,24rem)] flex-col overflow-hidden rounded-t-2xl border border-border bg-elevated shadow-pop lg:absolute lg:inset-x-auto lg:bottom-auto lg:left-0 lg:right-auto lg:top-full lg:mt-1.5 lg:max-h-[min(22rem,72vh)] lg:w-[min(calc(100vw-1.5rem),20rem)] lg:rounded-lg"
                  : "fixed inset-x-0 bottom-0 z-[300] flex max-h-[min(70vh,24rem)] flex-col overflow-hidden rounded-t-2xl border border-border bg-elevated shadow-pop lg:absolute lg:inset-x-auto lg:bottom-auto lg:left-0 lg:right-auto lg:top-full lg:mt-1 lg:w-[min(calc(100vw-2rem),22rem)] lg:max-h-[min(22rem,70vh)] lg:rounded-lg"
              }
              role="listbox"
              aria-label="Underlying symbols"
            >
              {chainBar ? (
                renderUnderlyingList("chainBar", filtered)
              ) : (
                <>
                  <div className="flex items-center justify-between border-b border-border-soft px-3 py-2 lg:hidden">
                    <span className="text-sm font-medium text-foreground">
                      Select underlying
                    </span>
                    <button
                      type="button"
                      className="rounded-lg p-2 text-muted hover:bg-panel2"
                      aria-label="Close"
                      onClick={closeDropdown}
                    >
                      <span className="text-lg leading-none" aria-hidden>
                        ×
                      </span>
                    </button>
                  </div>
                  {filterInputDefault}
                  {renderUnderlyingList("default", filtered)}
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
          "overflow-hidden border border-border bg-panel2 transition-colors",
          open ? "rounded-t-lg rounded-b-none" : "rounded-lg",
        ].join(" ")}
      >
        <label className="sr-only" htmlFor="option-chain-underlying-search">
          Search underlying
        </label>
        <div className="relative flex items-center">
          <SearchIcon className="pointer-events-none absolute left-3 top-1/2 size-[1.125rem] -translate-y-1/2 text-faint" />
          <input
            id="option-chain-underlying-search"
            ref={inputRef}
            data-scrip-input
            type="search"
            autoComplete="off"
            disabled={disabled}
            value={inputDisplay}
            onChange={(e) => handleInputChange(e.target.value)}
            onFocus={handleInputFocus}
            onKeyDown={handleKeyDown}
            onBlur={handleInputBlur}
            placeholder="Type stock name: SBIN, TCS etc."
            className="w-full border-0 bg-transparent py-2.5 pl-10 pr-3 font-mono text-sm text-foreground outline-none ring-0 placeholder:text-faint focus:ring-0 disabled:cursor-not-allowed disabled:text-faint"
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
            className="fixed inset-x-0 bottom-0 z-[300] max-h-[min(70vh,24rem)] overflow-hidden rounded-t-2xl border border-border bg-elevated shadow-pop lg:absolute lg:inset-x-auto lg:bottom-auto lg:left-0 lg:right-0 lg:top-full lg:max-h-[min(22rem,70vh)] lg:rounded-b-lg lg:rounded-t-none lg:border-t-0"
            role="listbox"
            aria-label="Underlying symbols"
          >
            <div className="flex items-center justify-between border-b border-border-soft px-3 py-2 lg:hidden">
              <span className="text-sm font-medium text-foreground">
                Select underlying
              </span>
              <button
                type="button"
                className="rounded-lg p-2 text-muted hover:bg-panel2"
                aria-label="Close"
                onClick={closeDropdown}
              >
                <span className="text-lg leading-none" aria-hidden>
                  ×
                </span>
              </button>
            </div>
            {renderUnderlyingList("default", filtered)}
          </div>
        </>
      ) : null}
    </div>
  );
}
