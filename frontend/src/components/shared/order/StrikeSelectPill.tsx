"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
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
  layout?: "default" | "toolbar" | "table";
  rootClassName?: string;
  hideLabel?: boolean;
  /** Shows a small "ATM" pill in the closed trigger next to the value. */
  atmBadge?: boolean;
};

export function StrikeSelectPill({
  strikes,
  value,
  onChange,
  disabled,
  busy,
  layout = "default",
  rootClassName,
  hideLabel,
  atmBadge,
}: Props) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const rootRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const mobileInputRef = useRef<HTMLInputElement>(null);
  const tablePortalRef = useRef<HTMLDivElement>(null);
  const [isDesktop, setIsDesktop] = useState(false);
  const [anchorRect, setAnchorRect] = useState<{
    top: number;
    left: number;
    width: number;
  } | null>(null);

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

  const handleInputBlur = useComboboxBlurClose(
    rootRef,
    [tablePortalRef],
    closeDropdown,
  );

  const tableLayout = layout === "table";

  useEffect(() => {
    if (!open) return;
    const fn = (e: MouseEvent) => {
      const t = e.target as Node;
      if (rootRef.current?.contains(t)) return;
      if (tablePortalRef.current?.contains(t)) return;
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

  // Table layout sits inside a horizontally-scrolling wrapper
  // (`overflow-x-auto`), which per the CSS overflow spec also clips the
  // vertical axis — an `absolute` dropdown gets cut off. Portal it to
  // `document.body` and position it with measured, viewport-fixed
  // coordinates instead so it escapes that clipping on desktop.
  useEffect(() => {
    if (!tableLayout) return;
    const mq = window.matchMedia("(min-width: 1024px)");
    const update = () => setIsDesktop(mq.matches);
    update();
    mq.addEventListener("change", update);
    return () => mq.removeEventListener("change", update);
  }, [tableLayout]);

  useEffect(() => {
    if (!open || !tableLayout || !isDesktop) return;
    const updateRect = () => {
      const rect = rootRef.current?.getBoundingClientRect();
      if (!rect) return;
      setAnchorRect({ top: rect.bottom + 4, left: rect.left, width: rect.width });
    };
    updateRect();
    window.addEventListener("scroll", updateRect, true);
    window.addEventListener("resize", updateRect);
    return () => {
      window.removeEventListener("scroll", updateRect, true);
      window.removeEventListener("resize", updateRect);
    };
  }, [open, tableLayout, isDesktop]);

  const toolbarLayout = layout === "toolbar";
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
      return "flex w-full min-w-0 max-w-full items-center justify-between gap-1 rounded-t-[2px] border-0 border-b border-muted bg-background dark:bg-elevated px-2 py-1.5 text-left text-xs text-foreground outline-none transition hover:border-accent focus-within:border-accent-strong focus-within:bg-panel disabled:cursor-not-allowed disabled:opacity-50";
    }
    if (toolbarLayout) {
      return "flex min-w-[11rem] shrink-0 items-center justify-between gap-2 rounded-t-[3px] border-0 border-b border-muted bg-background dark:bg-elevated px-3.5 py-2.5 text-left text-sm text-foreground outline-none transition hover:border-accent focus-within:border-accent-strong focus-within:bg-panel disabled:cursor-not-allowed disabled:opacity-50";
    }
    return "flex w-full min-w-0 items-center justify-between gap-2 rounded-t-[3px] border-0 border-b border-muted bg-background dark:bg-elevated px-3.5 py-2.5 text-left text-sm text-foreground outline-none transition hover:border-accent focus-within:border-accent-strong focus-within:bg-panel disabled:cursor-not-allowed disabled:opacity-50";
  })();

  const inputClass = tableLayout
    ? "min-w-0 flex-1 truncate border-0 bg-transparent p-0 font-mono text-xs font-semibold tabular-nums text-foreground outline-none ring-0 placeholder:font-normal placeholder:text-faint focus:ring-0 disabled:cursor-not-allowed"
    : "min-w-0 flex-1 truncate border-0 bg-transparent p-0 font-mono text-sm font-semibold tabular-nums text-foreground outline-none ring-0 placeholder:font-normal placeholder:text-faint focus:ring-0 disabled:cursor-not-allowed";

  const optionClass = (k: number, highlighted: boolean) => {
    return `flex w-full rounded-lg px-3 py-2 text-left font-mono text-sm transition ${
      highlighted
        ? "bg-panel2 text-foreground"
        : value === k
          ? "bg-accent-tint font-semibold text-accent-strong"
          : "text-foreground hover:bg-panel2"
    }`;
  };

  const mobileSearchClass =
    "w-full rounded-lg border border-border bg-panel2 px-3 py-2 font-mono text-sm tabular-nums text-foreground outline-none placeholder:text-faint focus:border-accent focus:ring-1 focus:ring-accent/30";

  const renderStrikeUl = (
    options: number[],
    ulRef?: React.RefObject<HTMLUListElement | null>,
  ) => (
    <ul
      ref={ulRef}
      className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-1 lg:max-h-[min(18rem,50vh)]"
    >
      {options.length === 0 ? (
        <li className="px-3 py-6 text-center text-sm text-muted">
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

  const tableDesktopPanel =
    tableLayout && isDesktop && open && anchorRect ? (
      <div
        ref={tablePortalRef}
        role="listbox"
        aria-label="Strike prices"
        style={{
          position: "fixed",
          top: anchorRect.top,
          left: anchorRect.left,
          width: Math.max(anchorRect.width, 208),
        }}
        className="z-[300] flex max-h-[min(22rem,70vh)] min-w-[13rem] flex-col rounded-lg border border-border bg-elevated shadow-pop"
      >
        {renderStrikeUl(filteredStrikes, listRef)}
      </div>
    ) : null;

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
          <span className="whitespace-nowrap text-sm font-medium text-muted">
            Strike
          </span>
        )
      ) : hideLabel ? null : (
        <span className="mb-1.5 block text-xs font-medium text-muted">
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
        {atmBadge && !open && valueLabel ? (
          <span className="shrink-0 rounded-[4px] bg-accent-tint px-1.5 py-0.5 text-micro font-bold tracking-[.04em] text-accent-strong">
            ATM
          </span>
        ) : null}
        <span
          className={tableLayout ? "shrink-0 text-body text-faint" : "shrink-0 text-faint"}
          aria-hidden
        >
          ▾
        </span>
      </div>

      {tableDesktopPanel ? createPortal(tableDesktopPanel, document.body) : null}

      {open && !(tableLayout && isDesktop) ? (
        <>
          <div
            className="fixed inset-0 z-[295] bg-black/45 lg:hidden"
            role="presentation"
            aria-hidden
            onClick={closeDropdown}
          />
          <div
            className={
              inlineLayout
                ? "fixed inset-0 z-[300] flex flex-col bg-panel lg:absolute lg:inset-x-auto lg:inset-y-auto lg:left-0 lg:top-full lg:z-[300] lg:mt-1 lg:max-h-[min(22rem,70vh)] lg:w-52 lg:min-w-[12rem] lg:rounded-lg lg:border lg:border-border lg:bg-elevated lg:shadow-pop"
                : [
                    "fixed inset-0 z-[300] flex flex-col bg-panel lg:absolute lg:inset-x-auto lg:inset-y-auto lg:left-0 lg:top-full lg:z-[300] lg:mt-0 lg:max-h-[min(22rem,70vh)] lg:w-full lg:min-w-[18rem] lg:max-w-lg lg:border lg:border-t-0 lg:border-border lg:bg-elevated lg:shadow-pop",
                    open ? "lg:rounded-b-lg lg:rounded-t-none" : "lg:rounded-lg",
                  ].join(" ")
            }
            role="listbox"
            aria-label="Strike prices"
          >
            <div className="flex shrink-0 items-center justify-between border-b border-border-soft px-3 py-2.5 lg:hidden">
              <span className="text-sm font-semibold text-foreground">
                Choose strike
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
            <div className="shrink-0 border-b border-border-soft px-3 py-2 lg:hidden">
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
