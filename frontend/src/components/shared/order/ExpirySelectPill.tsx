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
  layout?: "default" | "toolbar";
  rootClassName?: string;
  hideLabel?: boolean;
  /** Toolbar layout only: show the full `DD-Mon-YYYY` value instead of the short `D Mon` chip. */
  fullDate?: boolean;
};

export function ExpirySelectPill({
  dates,
  value,
  onChange,
  disabled,
  layout = "default",
  rootClassName,
  hideLabel,
  fullDate,
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

  const toolbarLayout = layout === "toolbar";
  const expiryChip = value ? formatExpiryChipShort(value) : null;
  const closedDisplay = toolbarLayout
    ? value
      ? fullDate
        ? value
        : (expiryChip ?? value)
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
    if (toolbarLayout) {
      const minWidth = fullDate
        ? "min-w-0 sm:min-w-[12.5rem]"
        : "min-w-0 sm:min-w-[11rem]";
      return `flex ${minWidth} shrink sm:shrink-0 items-center justify-between gap-2 rounded-t-[3px] border-0 border-b border-muted bg-background dark:bg-elevated px-3.5 py-2.5 text-left text-sm text-foreground outline-none transition hover:border-accent focus-within:border-accent-strong focus-within:bg-panel disabled:cursor-not-allowed disabled:opacity-50`;
    }
    return "flex w-full min-w-0 items-center justify-between gap-2 rounded-t-[3px] border-0 border-b border-muted bg-background dark:bg-elevated px-3.5 py-2.5 text-left text-sm text-foreground outline-none transition hover:border-accent focus-within:border-accent-strong focus-within:bg-panel disabled:cursor-not-allowed disabled:opacity-50";
  })();

  const inputClass =
    "min-w-0 flex-1 truncate border-0 bg-transparent p-0 text-sm font-semibold text-foreground outline-none ring-0 placeholder:font-normal placeholder:text-faint focus:ring-0 disabled:cursor-not-allowed";

  const optionClass = (d: string, highlighted: boolean) => {
    return `flex w-full rounded-lg px-3 py-2 text-left font-mono text-sm transition ${
      highlighted
        ? "bg-panel2 text-foreground"
        : d === value
          ? "bg-accent-tint font-semibold text-accent-strong"
          : "text-foreground hover:bg-panel2"
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
            tabIndex={-1}
            aria-selected={false}
            className="flex w-full rounded-lg px-3 py-2 text-left text-sm font-medium text-muted transition hover:bg-panel2"
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => handleSelect("")}
          >
            Clear expiry
          </button>
        </li>
      ) : null}
      {options.length === 0 ? (
        <li className="px-3 py-6 text-center text-sm text-muted">
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
              tabIndex={-1}
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

  const mobilePanelClass = "absolute inset-0 z-[1] flex flex-col bg-panel";

  const desktopListboxClass = toolbarLayout
    ? "absolute left-0 top-full z-[300] mt-1 hidden max-h-[min(22rem,70vh)] w-52 min-w-[12rem] rounded-lg border border-border bg-elevated shadow-pop lg:flex lg:flex-col"
    : [
        "absolute left-0 top-full z-[300] mt-0 hidden max-h-[min(22rem,70vh)] w-full min-w-[18rem] max-w-lg border border-t-0 border-border bg-elevated shadow-pop lg:flex lg:flex-col",
        open ? "rounded-b-lg rounded-t-none" : "rounded-lg",
      ].join(" ");

  const mobileSearchClass =
    "w-full rounded-lg border border-border bg-panel2 px-3 py-2 font-mono text-sm text-foreground outline-none placeholder:text-faint focus:border-accent focus:ring-1 focus:ring-accent/30";

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
          <div className="flex shrink-0 items-center justify-between border-b border-border-soft px-3 py-2.5">
            <span className="text-sm font-semibold text-foreground">
              Choose expiry
            </span>
            <button
              type="button"
              className="rounded-lg p-2 text-muted hover:bg-panel2"
              aria-label="Close"
              onClick={closeDropdown}
            >
              <span className="text-xl leading-none" aria-hidden>
                ×
              </span>
            </button>
          </div>
          <div className="shrink-0 border-b border-border-soft px-3 py-2">
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
          ? `relative flex min-w-0 shrink sm:shrink-0 items-center gap-2 ${open ? "z-[300]" : "z-0"} ${rootClassName ?? ""}`
          : `relative min-w-[min(100%,12rem)] flex-1 lg:max-w-md ${open ? "z-[300]" : "z-0"} ${rootClassName ?? ""}`
      }
    >
      {toolbarLayout ? (
        hideLabel ? null : (
          <span className="whitespace-nowrap text-sm font-medium text-muted">
            Expiry
          </span>
        )
      ) : hideLabel ? null : (
        <span className="mb-1.5 block text-xs font-medium text-muted">
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
        <span className="shrink-0 text-faint" aria-hidden>
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
