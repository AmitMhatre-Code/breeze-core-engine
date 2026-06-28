"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type RefObject,
} from "react";
import { createPortal } from "react-dom";
import {
  filterExpiryDates,
  formatExpiryChipShort,
} from "@/lib/strategy-builder/expiry";
import {
  useComboboxBlurClose,
  useListboxCombobox,
} from "@/lib/ui/use-listbox-combobox";

type Props = {
  dates: string[];
  value: string;
  onChange: (expiryDisplay: string) => void;
  disabled?: boolean;
  tone?: "default" | "darkToolbar";
  layout?: "default" | "toolbar";
  rootClassName?: string;
  hideLabel?: boolean;
};

export function ExpirySelectPill({
  dates,
  value,
  onChange,
  disabled,
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
  const mobileListRef = useRef<HTMLUListElement>(null);
  const mobilePortalRef = useRef<HTMLDivElement>(null);
  const portalReady = useSyncExternalStore(
    () => () => {},
    () => true,
    () => false,
  );

  const closeDropdown = useCallback(() => {
    setOpen(false);
    setQ("");
  }, []);

  const filteredDates = useMemo(
    () => filterExpiryDates(dates, q),
    [dates, q],
  );

  const handleSelect = useCallback(
    (d: string) => {
      onChange(d);
      closeDropdown();
    },
    [onChange, closeDropdown],
  );

  const { highlightIndex, listRef, handleKeyDown } = useListboxCombobox({
    open,
    options: filteredDates,
    onSelect: handleSelect,
    onClose: closeDropdown,
    extraListRefs: [mobileListRef],
  });

  const handleInputBlur = useComboboxBlurClose(
    rootRef,
    [mobilePortalRef],
    closeDropdown,
  );

  useEffect(() => {
    if (!open) return;
    const fn = (e: MouseEvent) => {
      const t = e.target as Node;
      if (rootRef.current?.contains(t)) return;
      if (mobilePortalRef.current?.contains(t)) return;
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
  const expiryChip = value ? formatExpiryChipShort(value) : null;
  const closedDisplay = toolbarLayout
    ? value
      ? (expiryChip ?? value)
      : ""
    : value;
  const inputDisplay = open ? q : closedDisplay;

  const handleInputFocus = () => {
    if (disabled) return;
    setOpen(true);
    setQ(closedDisplay);
  };

  const handleInputChange = (next: string) => {
    setQ(next);
    if (!open) setOpen(true);
  };

  const buttonClass = (() => {
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
    if (toolbarLayout && darkToolbar) {
      return "min-w-0 flex-1 truncate border-0 bg-transparent p-0 text-sm font-semibold text-zinc-900 outline-none ring-0 placeholder:font-normal placeholder:text-zinc-500 focus:ring-0 disabled:cursor-not-allowed dark:text-white dark:placeholder:text-zinc-500";
    }
    if (toolbarLayout) {
      return "min-w-0 flex-1 truncate border-0 bg-transparent p-0 text-sm font-semibold text-zinc-900 outline-none ring-0 placeholder:font-normal placeholder:text-zinc-400 focus:ring-0 disabled:cursor-not-allowed dark:text-zinc-100 dark:placeholder:text-zinc-500";
    }
    if (darkToolbar) {
      return "min-w-0 flex-1 truncate border-0 bg-transparent p-0 text-sm font-semibold text-zinc-900 outline-none ring-0 placeholder:font-normal placeholder:text-zinc-500 focus:ring-0 disabled:cursor-not-allowed dark:text-zinc-100 dark:placeholder:text-zinc-500";
    }
    return "min-w-0 flex-1 truncate border-0 bg-transparent p-0 text-sm text-zinc-900 outline-none ring-0 placeholder:font-normal placeholder:text-zinc-400 focus:ring-0 disabled:cursor-not-allowed dark:text-zinc-100 dark:placeholder:text-zinc-500";
  })();

  const optionClass = (d: string, highlighted: boolean) => {
    if (darkToolbar) {
      return `flex w-full rounded-lg px-3 py-2 text-left text-sm transition ${
        highlighted
          ? "bg-sky-200 text-sky-950 dark:bg-sky-900/60 dark:text-sky-300"
          : d === value
            ? "bg-sky-100 text-sky-900 dark:bg-sky-950/55 dark:text-sky-400"
            : "text-zinc-900 hover:bg-zinc-100 dark:text-zinc-100 dark:hover:bg-zinc-700/50"
      }`;
    }
    return `flex w-full rounded-lg px-3 py-2 text-left text-sm transition hover:bg-zinc-100 dark:hover:bg-zinc-800/80 ${
      highlighted
        ? "bg-sky-100 text-sky-900 dark:bg-sky-900/50 dark:text-sky-200"
        : d === value
          ? "bg-sky-50 text-sky-900 dark:bg-sky-950/40 dark:text-sky-100"
          : "text-zinc-900 dark:text-zinc-100"
    }`;
  };

  const renderExpiryUl = (
    options: string[],
    ulRef?: RefObject<HTMLUListElement | null>,
  ) => (
    <ul
      ref={ulRef}
      className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-1 lg:max-h-[min(18rem,50vh)]"
    >
      {value ? (
        <li>
          <button
            type="button"
            role="option"
            aria-selected={false}
            className={
              darkToolbar
                ? "flex w-full rounded-lg px-3 py-2 text-left text-sm font-medium text-zinc-600 transition hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-700/50"
                : "flex w-full rounded-lg px-3 py-2 text-left text-sm font-medium text-zinc-600 transition hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-800/80"
            }
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => handleSelect("")}
          >
            Clear expiry
          </button>
        </li>
      ) : null}
      {options.length === 0 ? (
        <li
          className={
            darkToolbar
              ? "px-3 py-6 text-center text-sm text-zinc-500 dark:text-zinc-500"
              : "px-3 py-6 text-center text-sm text-zinc-500 dark:text-zinc-400"
          }
        >
          {dates.length === 0
            ? "No expiries for this underlying"
            : "No matches"}
        </li>
      ) : (
        options.map((d, index) => (
          <li key={d}>
            <button
              type="button"
              role="option"
              aria-selected={d === value}
              data-combobox-index={index}
              className={optionClass(d, index === highlightIndex)}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => handleSelect(d)}
            >
              <span className="font-semibold">{d}</span>
            </button>
          </li>
        ))
      )}
    </ul>
  );

  const mobilePanelClass =
    darkToolbar && toolbarLayout
      ? "absolute inset-0 z-[1] flex flex-col bg-white dark:bg-zinc-900"
      : toolbarLayout
        ? "absolute inset-0 z-[1] flex flex-col bg-zinc-50 dark:bg-zinc-950"
        : darkToolbar
          ? "absolute inset-0 z-[1] flex flex-col bg-white dark:bg-zinc-900"
          : "absolute inset-0 z-[1] flex flex-col bg-zinc-50 dark:bg-zinc-950";

  const desktopListboxClass =
    darkToolbar && toolbarLayout
      ? "absolute left-0 top-full z-[300] mt-1 hidden max-h-[min(22rem,70vh)] w-52 min-w-[12rem] rounded-lg border border-zinc-200 bg-white shadow-xl dark:border-zinc-600 dark:bg-zinc-800 lg:flex lg:flex-col"
      : toolbarLayout
        ? "absolute left-0 top-full z-[300] mt-1 hidden max-h-[min(22rem,70vh)] w-52 min-w-[12rem] rounded-md border border-zinc-200 bg-white shadow-xl dark:border-zinc-700 dark:bg-zinc-900 lg:flex lg:flex-col"
        : darkToolbar
          ? "absolute left-0 top-full z-[300] mt-0 hidden max-h-[min(22rem,70vh)] w-full min-w-[18rem] max-w-lg rounded-b-lg rounded-t-none border border-t-0 border-zinc-200 bg-white shadow-xl dark:border-zinc-600 dark:bg-zinc-800 lg:flex lg:flex-col"
          : "absolute left-0 top-full z-[300] mt-1.5 hidden max-h-[min(22rem,70vh)] w-full min-w-[18rem] max-w-lg rounded-md border border-zinc-200 bg-white shadow-xl dark:border-zinc-700 dark:bg-zinc-900 lg:flex lg:flex-col";

  const mobileSearchClass = darkToolbar
    ? "w-full rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-900 outline-none placeholder:text-zinc-500 focus:border-sky-500 focus:ring-1 focus:ring-sky-500/30 dark:border-zinc-600 dark:bg-zinc-800 dark:text-zinc-100"
    : "w-full rounded-lg border border-zinc-200 bg-white px-3 py-2 text-sm text-zinc-900 outline-none placeholder:text-zinc-500 focus:border-sky-500 focus:ring-1 focus:ring-sky-500/30 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100";

  const mobileLayer =
    portalReady && open ? (
      <div ref={mobilePortalRef} className="fixed inset-0 z-[520] lg:hidden">
        <div
          className="absolute inset-0 z-0 bg-black/45"
          role="presentation"
          aria-hidden
          onClick={closeDropdown}
        />
        <div
          className={mobilePanelClass}
          role="listbox"
          aria-label="Expiry dates"
        >
          <div
            className={
              darkToolbar
                ? "flex shrink-0 items-center justify-between border-b border-zinc-200 px-3 py-2.5 dark:border-zinc-700"
                : "flex shrink-0 items-center justify-between border-b border-zinc-200 px-3 py-2.5 dark:border-zinc-800"
            }
          >
            <span
              className={
                darkToolbar
                  ? "text-sm font-semibold text-zinc-900 dark:text-zinc-100"
                  : "text-sm font-semibold text-zinc-900 dark:text-zinc-50"
              }
            >
              Choose expiry
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
          <div className="shrink-0 border-b border-zinc-200 px-3 py-2 dark:border-zinc-700">
            <label className="sr-only" htmlFor="expiry-select-mobile-filter">
              Filter expiry dates
            </label>
            <input
              id="expiry-select-mobile-filter"
              ref={mobileInputRef}
              type="search"
              autoComplete="off"
              value={q}
              onChange={(e) => handleInputChange(e.target.value)}
              onKeyDown={handleKeyDown}
              onBlur={handleInputBlur}
              placeholder="Type expiry date…"
              className={mobileSearchClass}
            />
          </div>
          {renderExpiryUl(filteredDates, mobileListRef)}
        </div>
      </div>
    ) : null;

  return (
    <div
      ref={rootRef}
      className={
        toolbarLayout
          ? `relative flex shrink-0 items-center gap-2 ${open ? "z-[300]" : "z-0"} ${rootClassName ?? ""}`
          : `relative min-w-[min(100%,12rem)] flex-1 lg:max-w-md ${open ? "z-[300]" : "z-0"} ${rootClassName ?? ""}`
      }
    >
      {toolbarLayout ? (
        hideLabel ? null : (
          <span
            className={
              darkToolbar
                ? "whitespace-nowrap text-sm font-medium text-zinc-600 dark:text-zinc-500"
                : "whitespace-nowrap text-xs font-medium text-zinc-600 dark:text-zinc-400"
            }
          >
            Expiry
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
          Expiry (earliest first)
        </span>
      )}
      <div className={buttonClass}>
        <input
          ref={inputRef}
          type="search"
          autoComplete="off"
          disabled={disabled}
          aria-expanded={open}
          aria-haspopup="listbox"
          aria-label="Expiry date"
          value={inputDisplay}
          onChange={(e) => handleInputChange(e.target.value)}
          onFocus={handleInputFocus}
          onKeyDown={handleKeyDown}
          onBlur={handleInputBlur}
          placeholder="Select expiry…"
          className={inputClass}
        />
        <span
          className={
            toolbarLayout && darkToolbar
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
          {mobileLayer ? createPortal(mobileLayer, document.body) : null}
          <div
            className={desktopListboxClass}
            role="listbox"
            aria-label="Expiry dates"
          >
            {renderExpiryUl(filteredDates, listRef)}
          </div>
        </>
      ) : null}
    </div>
  );
}
