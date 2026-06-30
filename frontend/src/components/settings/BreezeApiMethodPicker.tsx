"use client";

import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";

import {
  RISK_GROUP_LABEL,
  type BreezeApiCatalogEntry,
} from "@/lib/breeze-api-tester";
import {
  useListboxMenu,
  useListboxOutsideClose,
} from "@/lib/ui/use-listbox-menu";

type ApiGroup = {
  level: BreezeApiCatalogEntry["risk_level"];
  label: string;
  items: BreezeApiCatalogEntry[];
};

const RISK_BADGE: Record<BreezeApiCatalogEntry["risk_level"], string> = {
  read: "bg-emerald-500/15 text-emerald-700 ring-emerald-500/25 dark:bg-emerald-500/20 dark:text-emerald-300",
  funds: "bg-amber-500/15 text-amber-800 ring-amber-500/25 dark:bg-amber-500/20 dark:text-amber-200",
  trade: "bg-red-500/15 text-red-800 ring-red-500/25 dark:bg-red-500/20 dark:text-red-200",
  gtt: "bg-violet-500/15 text-violet-800 ring-violet-500/25 dark:bg-violet-500/20 dark:text-violet-200",
};

const RISK_BADGE_SHORT: Record<BreezeApiCatalogEntry["risk_level"], string> = {
  read: "Read",
  funds: "Funds",
  trade: "Trade",
  gtt: "GTT",
};

function ChevronDown({ open }: { open: boolean }) {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 16 16"
      fill="none"
      aria-hidden
      className={[
        "shrink-0 text-zinc-500 transition-transform duration-200 dark:text-zinc-400",
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

function matchesQuery(entry: BreezeApiCatalogEntry, q: string): boolean {
  if (!q) return true;
  const needle = q.toLowerCase();
  return (
    entry.title.toLowerCase().includes(needle) || entry.method.toLowerCase().includes(needle)
  );
}

export function BreezeApiMethodPicker({
  groups,
  selectedMethod,
  onSelect,
}: {
  groups: ApiGroup[];
  selectedMethod: string;
  onSelect: (method: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const listId = useId();
  const labelId = useId();

  const selected = useMemo(() => {
    for (const g of groups) {
      const hit = g.items.find((e) => e.method === selectedMethod);
      if (hit) return hit;
    }
    return null;
  }, [groups, selectedMethod]);

  const filteredGroups = useMemo(() => {
    const q = query.trim();
    return groups
      .map((g) => ({
        ...g,
        items: g.items.filter((item) => matchesQuery(item, q)),
      }))
      .filter((g) => g.items.length > 0);
  }, [groups, query]);

  const flatItems = useMemo(
    () => filteredGroups.flatMap((g) => g.items),
    [filteredGroups],
  );

  const indexByMethod = useMemo(() => {
    const map = new Map<string, number>();
    flatItems.forEach((item, index) => map.set(item.method, index));
    return map;
  }, [flatItems]);

  const close = useCallback(() => {
    setOpen(false);
    setQuery("");
  }, []);

  useListboxOutsideClose(open, rootRef, close);

  const { highlightIndex, handleTriggerKeyDown } = useListboxMenu({
    open,
    optionCount: flatItems.length,
    onOpen: () => setOpen(true),
    onClose: () => {
      close();
      triggerRef.current?.focus();
    },
    onSelectIndex: (index) => {
      const item = flatItems[index];
      if (item) {
        onSelect(item.method);
        close();
        triggerRef.current?.focus();
      }
    },
    triggerRef,
    listRef,
  });

  useEffect(() => {
    if (!open) return;
    const t = window.setTimeout(() => searchRef.current?.focus(), 0);
    return () => window.clearTimeout(t);
  }, [open]);

  return (
    <div ref={rootRef} className="relative w-full text-left">
      <span id={labelId} className="app-text-muted">
        API
      </span>

      <button
        ref={triggerRef}
        type="button"
        className={[
          "app-input mt-1.5 flex items-center gap-3 py-2.5 text-left",
          open
            ? "border-sky-500/60 ring-2 ring-sky-500/30 dark:border-sky-400/45 dark:ring-sky-400/25"
            : "",
        ].join(" ")}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={listId}
        aria-labelledby={labelId}
        onClick={() => setOpen((v) => !v)}
        onKeyDown={handleTriggerKeyDown}
      >
        <span className="min-w-0 flex-1">
          {selected ? (
            <>
              <span className="block truncate text-sm font-medium text-zinc-900 dark:text-zinc-100">
                {selected.title}
              </span>
              <span className="mt-0.5 block truncate font-mono text-xs text-zinc-500 dark:text-zinc-400">
                {selected.method}
              </span>
            </>
          ) : (
            <span className="text-sm text-zinc-500 dark:text-zinc-400">Select an API…</span>
          )}
        </span>
        {selected ? (
          <span
            className={[
              "hidden shrink-0 rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ring-1 ring-inset sm:inline",
              RISK_BADGE[selected.risk_level],
            ].join(" ")}
          >
            {RISK_GROUP_LABEL[selected.risk_level]}
          </span>
        ) : null}
        <ChevronDown open={open} />
      </button>

      {open ? (
        <div
          className={[
            "absolute left-0 right-0 top-[calc(100%+6px)] z-40 overflow-hidden rounded-md border shadow-lg",
            "border-zinc-200 bg-white dark:border-zinc-700 dark:bg-zinc-900",
          ].join(" ")}
        >
          <div className="border-b border-zinc-200/80 p-2 dark:border-zinc-800">
            <input
              ref={searchRef}
              type="search"
              value={query}
              placeholder="Search APIs…"
              aria-label="Search APIs"
              className="app-input py-2"
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>

          <ul
            ref={listRef}
            id={listId}
            role="listbox"
            aria-labelledby={labelId}
            className="max-h-[min(22rem,55vh)] overflow-y-auto overscroll-contain p-1.5"
          >
            {filteredGroups.length === 0 ? (
              <li className="px-3 py-6 text-center text-sm text-zinc-500 dark:text-zinc-400">
                No APIs match your search.
              </li>
            ) : (
              filteredGroups.map((g) => (
                <li key={g.level} role="presentation" className="mb-2 last:mb-0">
                  <div
                    className="sticky top-0 z-[1] px-2 py-1.5 text-[11px] font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-500"
                    aria-hidden
                  >
                    {g.label}
                  </div>
                  <ul className="space-y-0.5">
                    {g.items.map((item) => {
                      const menuIndex = indexByMethod.get(item.method) ?? 0;
                      const isSelected = item.method === selectedMethod;
                      const highlighted = menuIndex === highlightIndex;
                      return (
                        <li key={item.method} role="presentation">
                          <button
                            type="button"
                            role="option"
                            aria-selected={isSelected}
                            data-menu-index={menuIndex}
                            tabIndex={-1}
                            className={[
                              "flex w-full items-start gap-2 rounded-sm px-2.5 py-2 text-left transition-colors",
                              isSelected || highlighted
                                ? "bg-sky-50 ring-1 ring-inset ring-sky-500/25 dark:bg-sky-950/40 dark:ring-sky-400/30"
                                : "hover:bg-zinc-100 dark:hover:bg-zinc-800/80",
                            ].join(" ")}
                            onClick={() => {
                              onSelect(item.method);
                              close();
                              triggerRef.current?.focus();
                            }}
                          >
                            <span className="min-w-0 flex-1">
                              <span className="block text-sm font-medium text-zinc-900 dark:text-zinc-100">
                                {item.title}
                              </span>
                              <span className="mt-0.5 block truncate font-mono text-[11px] text-zinc-500 dark:text-zinc-400">
                                {item.method}
                              </span>
                            </span>
                            <span
                              className={[
                                "mt-0.5 shrink-0 rounded-full px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide ring-1 ring-inset",
                                RISK_BADGE[item.risk_level],
                              ].join(" ")}
                            >
                              {RISK_BADGE_SHORT[item.risk_level]}
                            </span>
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                </li>
              ))
            )}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
