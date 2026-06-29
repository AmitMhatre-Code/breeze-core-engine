"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { filterStrikes } from "@/lib/strategy-builder/strike-filter";
import { formatStrike } from "@/lib/strategy-builder/format-strike";
import {
  useComboboxBlurClose,
  useListboxCombobox,
} from "@/lib/ui/use-listbox-combobox";

type Props = {
  strikes: number[];
  value: number | null;
  onChange: (strike: number) => void;
  disabled?: boolean;
  busy?: boolean;
  tone?: "default" | "darkToolbar";
  layout?: "default" | "toolbar" | "table";
  rootClassName?: string;
  hideLabel?: boolean;
};

export function StrikeSelectPill({
  strikes,
  value,
  onChange,
  disabled,
  busy,
  tone = "default",
  layout = "default",
  rootClassName,
  hideLabel,
}: Props) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const rootRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const mobileInputRef = useRef<HTMLInputElement>(null);

  const closeDropdown = useCallback(() => {
    setOpen(false);
    setQ("");
  }, []);

  const filteredStrikes = useMemo(() => filterStrikes(strikes, q), [strikes, q]);

  const handleSelect = useCallback(
    (k: number) => {
      onChange(k);
      closeDropdown();
    },
    [onChange, closeDropdown],
  );

  const { highlightIndex, listRef, handleKeyDown } = useListboxCombobox({
    open,
    options: filteredStrikes,
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

  useEffect(() => {
    if (!open) return;
    const id = requestAnimationFrame(() => {
      if (window.matchMedia("(max-width: 1023px)").matches) {
        mobileInputRef.current?.focus();
      } else {
        inputRef.current?.focus();
      }
    });
    return () => cancelAnimationFrame(id);
  }, [open]);

  const darkToolbar = tone === "darkToolbar";
  const toolbarLayout = layout === "toolbar";
  const tableLayout = layout === "table";
  const inlineLayout = toolbarLayout || tableLayout;

  const valueLabel =
    value != null && Number.isFinite(value) ? formatStrike(value) : null;

  const disabledCombined = Boolean(disabled || busy || !strikes.length);

  const closedDisplay = busy ? "" : valueLabel ? valueLabel : "";

  const placeholder = busy
    ? "Loading strikes…"
    : strikes.length
      ? "Select strike…"
      : "—";

  const inputDisplay = open ? q : closedDisplay;

  const handleInputFocus = () => {
    if (disabledCombined) return;
    setOpen(true);
    setQ(closedDisplay.replace(/,/g, ""));
  };

  const handleInputChange = (next: string) => {
    const cleaned = next.replace(/[^\d.]/g, "");
    const dotParts = cleaned.split(".");
    const normalized =
      dotParts.length <= 1
        ? dotParts[0]?.slice(0, 8) ?? ""
        : `${dotParts[0]?.slice(0, 8) ?? ""}.${dotParts.slice(1).join("").slice(0, 2)}`;
    setQ(normalized);
    if (!open) setOpen(true);
  };

  const buttonClass = (() => {
    if (tableLayout) {
      return "flex w-full min-w-0 max-w-full items-center justify-between gap-1 rounded-md border border-zinc-200 bg-white px-2 py-1.5 text-left text-xs text-zinc-900 shadow-sm outline-none transition hover:border-zinc-300 focus-within:border-sky-500 focus-within:ring-2 focus-within:ring-sky-500/20 disabled:cursor-not-allowed disabled:border-zinc-200 disabled:bg-zinc-100 disabled:text-zinc-400 dark:disabled:border-zinc-700 dark:disabled:bg-zinc-800 dark:disabled:text-zinc-500 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:hover:border-zinc-600 dark:focus-within:border-sky-400 dark:focus-within:ring-sky-400/20";
    }
    if (toolbarLayout && darkToolbar) {
      return "flex min-w-[8.5rem] shrink-0 items-center gap-1 rounded border-0 bg-transparent py-1 pl-0 pr-0.5 text-left text-sm font-semibold text-zinc-900 outline-none transition hover:bg-zinc-200/60 dark:text-white dark:hover:bg-white/5 focus-within:ring-2 focus-within:ring-sky-500/40 disabled:cursor-not-allowed disabled:text-zinc-400 dark:disabled:text-zinc-500";
    }
    if (toolbarLayout) {
      return "flex min-w-[11rem] shrink-0 items-center justify-between gap-2 rounded-md border border-zinc-200 bg-white px-3.5 py-2.5 text-left text-sm text-zinc-900 shadow-sm outline-none transition hover:border-zinc-300 focus-within:border-sky-500 focus-within:ring-4 focus-within:ring-sky-500/15 disabled:cursor-not-allowed disabled:border-zinc-200 disabled:bg-zinc-100 disabled:text-zinc-400 dark:disabled:border-zinc-700 dark:disabled:bg-zinc-800 dark:disabled:text-zinc-500 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:hover:border-zinc-600 dark:focus-within:border-sky-400 dark:focus-within:ring-sky-400/20";
    }
    if (darkToolbar) {
      return [
        "flex w-full min-w-0 items-center justify-between gap-2 border border-zinc-300 bg-white px-3.5 py-2.5 text-left text-sm text-zinc-900 shadow-sm outline-none transition hover:border-zinc-400 focus-within:border-sky-500 focus-within:ring-2 focus-within:ring-sky-500/35 disabled:cursor-not-allowed disabled:border-zinc-200 disabled:bg-zinc-100 disabled:text-zinc-400 dark:disabled:border-zinc-600 dark:disabled:bg-zinc-900 dark:disabled:text-zinc-500 dark:border-zinc-600 dark:bg-zinc-800 dark:text-zinc-100 dark:hover:border-zinc-500",
        open ? "rounded-t-md rounded-b-none border-b-0" : "rounded-md",
      ].join(" ");
    }
    return "flex w-full min-w-0 items-center justify-between gap-2 rounded-md border border-zinc-200 bg-white px-3.5 py-2.5 text-left text-sm text-zinc-900 shadow-sm outline-none transition hover:border-zinc-300 focus-within:border-sky-500 focus-within:ring-4 focus-within:ring-sky-500/15 disabled:cursor-not-allowed disabled:border-zinc-200 disabled:bg-zinc-100 disabled:text-zinc-400 dark:disabled:border-zinc-700 dark:disabled:bg-zinc-800 dark:disabled:text-zinc-500 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:hover:border-zinc-600 dark:focus-within:border-sky-400 dark:focus-within:ring-sky-400/20";
  })();

  const inputClass = (() => {
    if (tableLayout) {
      return "min-w-0 flex-1 truncate border-0 bg-transparent p-0 text-xs font-semibold tabular-nums text-zinc-900 outline-none ring-0 placeholder:font-normal placeholder:text-zinc-400 focus:ring-0 disabled:cursor-not-allowed dark:text-zinc-100 dark:placeholder:text-zinc-500";
    }
    if (toolbarLayout && darkToolbar) {
      return "min-w-0 flex-1 truncate border-0 bg-transparent p-0 text-sm font-semibold tabular-nums text-zinc-900 outline-none ring-0 placeholder:font-normal placeholder:text-zinc-500 focus:ring-0 disabled:cursor-not-allowed dark:text-white dark:placeholder:text-zinc-500";
    }
    if (toolbarLayout) {
      return "min-w-0 flex-1 truncate border-0 bg-transparent p-0 text-sm font-semibold tabular-nums text-zinc-900 outline-none ring-0 placeholder:font-normal placeholder:text-zinc-400 focus:ring-0 disabled:cursor-not-allowed dark:text-zinc-100 dark:placeholder:text-zinc-500";
    }
    if (darkToolbar) {
      return "min-w-0 flex-1 truncate border-0 bg-transparent p-0 text-sm font-semibold tabular-nums text-zinc-900 outline-none ring-0 placeholder:font-normal placeholder:text-zinc-500 focus:ring-0 disabled:cursor-not-allowed dark:text-zinc-100 dark:placeholder:text-zinc-500";
    }
    return "min-w-0 flex-1 truncate border-0 bg-transparent p-0 text-sm font-semibold tabular-nums text-zinc-900 outline-none ring-0 placeholder:font-normal placeholder:text-zinc-400 focus:ring-0 disabled:cursor-not-allowed dark:text-zinc-100 dark:placeholder:text-zinc-500";
  })();

  const optionClass = (k: number, highlighted: boolean) => {
    if (darkToolbar) {
      return `flex w-full rounded-lg px-3 py-2 text-left text-sm transition ${
        highlighted
          ? "bg-sky-200 text-sky-950 dark:bg-sky-900/60 dark:text-sky-300"
          : value === k
            ? "bg-sky-100 text-sky-900 dark:bg-sky-950/55 dark:text-sky-400"
            : "text-zinc-900 hover:bg-zinc-100 dark:text-zinc-100 dark:hover:bg-zinc-700/50"
      }`;
    }
    return `flex w-full rounded-lg px-3 py-2 text-left text-sm transition hover:bg-zinc-100 dark:hover:bg-zinc-800/80 ${
      highlighted
        ? "bg-sky-100 text-sky-900 dark:bg-sky-900/50 dark:text-sky-200"
        : value === k
          ? "bg-sky-50 text-sky-900 dark:bg-sky-950/40 dark:text-sky-100"
          : "text-zinc-900 dark:text-zinc-100"
    }`;
  };

  const mobileSearchClass = darkToolbar
    ? "w-full rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm tabular-nums text-zinc-900 outline-none placeholder:text-zinc-500 focus:border-sky-500 focus:ring-1 focus:ring-sky-500/30 dark:border-zinc-600 dark:bg-zinc-800 dark:text-zinc-100"
    : "w-full rounded-lg border border-zinc-200 bg-white px-3 py-2 text-sm tabular-nums text-zinc-900 outline-none placeholder:text-zinc-500 focus:border-sky-500 focus:ring-1 focus:ring-sky-500/30 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100";

  const renderStrikeUl = (
    options: number[],
    ulRef?: React.RefObject<HTMLUListElement | null>,
  ) => (
    <ul
      ref={ulRef}
      className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-1 lg:max-h-[min(18rem,50vh)]"
    >
      {options.length === 0 ? (
        <li
          className={
            darkToolbar
              ? "px-3 py-6 text-center text-sm text-zinc-500 dark:text-zinc-500"
              : "px-3 py-6 text-center text-sm text-zinc-500 dark:text-zinc-400"
          }
        >
          {busy ? "Loading…" : strikes.length === 0 ? "No strikes" : "No matches"}
        </li>
      ) : (
        options.map((k, index) => (
          <li key={k}>
            <button
              type="button"
              role="option"
              tabIndex={-1}
              aria-selected={value === k}
              data-combobox-index={index}
              className={optionClass(k, index === highlightIndex)}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => handleSelect(k)}
            >
              <span className="font-semibold tabular-nums">
                {formatStrike(k)}
              </span>
            </button>
          </li>
        ))
      )}
    </ul>
  );

  return (
    <div
      ref={rootRef}
      className={
        tableLayout
          ? `relative w-full min-w-0 max-w-[7.5rem] ${open ? "z-[300]" : "z-0"} ${rootClassName ?? ""}`
          : toolbarLayout
            ? `relative flex shrink-0 items-center gap-2 ${open ? "z-[300]" : "z-0"} ${rootClassName ?? ""}`
            : `relative min-w-[min(100%,12rem)] flex-1 lg:max-w-md ${open ? "z-[300]" : "z-0"} ${rootClassName ?? ""}`
      }
    >
      {tableLayout ? null : toolbarLayout ? (
        hideLabel ? null : (
          <span
            className={
              darkToolbar
                ? "whitespace-nowrap text-sm font-medium text-zinc-600 dark:text-zinc-500"
                : "whitespace-nowrap text-xs font-medium text-zinc-600 dark:text-zinc-400"
            }
          >
            Strike
          </span>
        )
      ) : hideLabel ? null : (
        <span
          className={
            darkToolbar
              ? "mb-1.5 block text-xs font-medium text-zinc-600 dark:text-zinc-500"
              : "mb-1.5 block text-xs font-medium text-zinc-600 dark:text-zinc-400"
          }
        >
          Strike
        </span>
      )}
      <div className={buttonClass}>
        <input
          ref={inputRef}
          type="search"
          inputMode="numeric"
          autoComplete="off"
          disabled={disabledCombined}
          aria-expanded={open}
          aria-haspopup="listbox"
          aria-label="Strike price"
          value={inputDisplay}
          onChange={(e) => handleInputChange(e.target.value)}
          onFocus={handleInputFocus}
          onKeyDown={handleKeyDown}
          onBlur={handleInputBlur}
          placeholder={placeholder}
          className={inputClass}
        />
        <span
          className={
            tableLayout
              ? "shrink-0 text-[10px] text-zinc-400 dark:text-zinc-500"
              : toolbarLayout && darkToolbar
                ? "shrink-0 text-zinc-500 dark:text-zinc-400"
                : toolbarLayout
                  ? "shrink-0 text-zinc-400 dark:text-zinc-500"
                  : darkToolbar
                    ? "shrink-0 text-zinc-500 dark:text-zinc-500"
                    : "shrink-0 text-zinc-400"
          }
          aria-hidden
        >
          ▾
        </span>
      </div>

      {open ? (
        <>
          <div
            className="fixed inset-0 z-[295] bg-black/45 lg:hidden"
            role="presentation"
            aria-hidden
            onClick={closeDropdown}
          />
          <div
            className={
              darkToolbar && inlineLayout
                ? "fixed inset-0 z-[300] flex flex-col bg-white dark:bg-zinc-900 lg:absolute lg:inset-x-auto lg:inset-y-auto lg:left-0 lg:top-full lg:z-[300] lg:mt-1 lg:max-h-[min(22rem,70vh)] lg:w-52 lg:min-w-[12rem] lg:rounded-lg lg:border lg:border-zinc-200 lg:bg-white lg:shadow-xl lg:dark:border-zinc-600 lg:dark:bg-zinc-800"
                : inlineLayout
                  ? "fixed inset-0 z-[300] flex flex-col bg-zinc-50 dark:bg-zinc-950 lg:absolute lg:inset-x-auto lg:inset-y-auto lg:left-0 lg:top-full lg:z-[300] lg:mt-1 lg:max-h-[min(22rem,70vh)] lg:w-52 lg:min-w-[12rem] lg:rounded-md lg:border lg:border-zinc-200 lg:bg-white lg:shadow-xl lg:dark:border-zinc-700 lg:dark:bg-zinc-900"
                  : darkToolbar
                    ? "fixed inset-0 z-[300] flex flex-col bg-white dark:bg-zinc-900 lg:absolute lg:inset-x-auto lg:inset-y-auto lg:left-0 lg:top-full lg:z-[300] lg:mt-0 lg:max-h-[min(22rem,70vh)] lg:w-full lg:min-w-[18rem] lg:max-w-lg lg:rounded-b-lg lg:rounded-t-none lg:border lg:border-t-0 lg:border-zinc-200 lg:bg-white lg:shadow-xl lg:dark:border-zinc-600 lg:dark:bg-zinc-800"
                    : "fixed inset-0 z-[300] flex flex-col bg-zinc-50 dark:bg-zinc-950 lg:absolute lg:inset-x-auto lg:inset-y-auto lg:left-0 lg:top-full lg:z-[300] lg:mt-1.5 lg:max-h-[min(22rem,70vh)] lg:w-full lg:min-w-[18rem] lg:max-w-lg lg:rounded-md lg:border lg:border-zinc-200 lg:bg-white lg:shadow-xl lg:dark:border-zinc-700 lg:dark:bg-zinc-900"
            }
            role="listbox"
            aria-label="Strike prices"
          >
            <div
              className={
                darkToolbar
                  ? "flex shrink-0 items-center justify-between border-b border-zinc-200 px-3 py-2.5 dark:border-zinc-700 lg:hidden"
                  : "flex shrink-0 items-center justify-between border-b border-zinc-200 px-3 py-2.5 dark:border-zinc-800 lg:hidden"
              }
            >
              <span
                className={
                  darkToolbar
                    ? "text-sm font-semibold text-zinc-900 dark:text-zinc-100"
                    : "text-sm font-semibold text-zinc-900 dark:text-zinc-50"
                }
              >
                Choose strike
              </span>
              <button
                type="button"
                className={
                  darkToolbar
                    ? "rounded-lg p-2 text-zinc-500 hover:bg-zinc-200/80 dark:text-zinc-400 dark:hover:bg-zinc-700/80"
                    : "rounded-lg p-2 text-zinc-500 hover:bg-zinc-200/80 dark:hover:bg-zinc-800"
                }
                aria-label="Close"
                onClick={closeDropdown}
              >
                <span className="text-xl leading-none" aria-hidden>
                  ×
                </span>
              </button>
            </div>
            <div className="shrink-0 border-b border-zinc-200 px-3 py-2 dark:border-zinc-700 lg:hidden">
              <label className="sr-only" htmlFor="strike-select-mobile-filter">
                Filter strike prices
              </label>
              <input
                id="strike-select-mobile-filter"
                ref={mobileInputRef}
                type="search"
                inputMode="numeric"
                autoComplete="off"
                value={q}
                onChange={(e) => handleInputChange(e.target.value)}
                onKeyDown={handleKeyDown}
                onBlur={handleInputBlur}
                placeholder="Type strike…"
                className={mobileSearchClass}
              />
            </div>

            {renderStrikeUl(filteredStrikes, listRef)}
          </div>
        </>
      ) : null}
    </div>
  );
}
