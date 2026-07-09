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
  read: "bg-up-tint text-up ring-up/25",
  funds: "bg-amber-tint text-amber-accent ring-amber-accent/25",
  trade: "bg-down-tint text-down ring-down/25",
  gtt: "bg-gtt-tint text-gtt ring-gtt/25",
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
          open ? "border-accent ring-2 ring-accent/30" : "",
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
              <span className="block truncate text-sm font-medium text-foreground">
                {selected.title}
              </span>
              <span className="mt-0.5 block truncate font-mono text-xs text-muted">
                {selected.method}
              </span>
            </>
          ) : (
            <span className="text-sm text-muted">Select an API…</span>
          )}
        </span>
        {selected ? (
          <span
            className={[
              "hidden shrink-0 rounded-full px-2 py-0.5 text-body font-semibold uppercase tracking-wide ring-1 ring-inset sm:inline",
              RISK_BADGE[selected.risk_level],
            ].join(" ")}
          >
            {RISK_GROUP_LABEL[selected.risk_level]}
          </span>
        ) : null}
        <ChevronDown open={open} />
      </button>

      {open ? (
        <div className="absolute left-0 right-0 top-[calc(100%+6px)] z-40 overflow-hidden rounded-[10px] border border-border bg-elevated shadow-pop">
          <div className="border-b border-border-soft p-2">
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
              <li className="px-3 py-6 text-center text-sm text-muted">
                No APIs match your search.
              </li>
            ) : (
              filteredGroups.map((g) => (
                <li key={g.level} role="presentation" className="mb-2 last:mb-0">
                  <div
                    className="sticky top-0 z-[1] bg-elevated px-2 py-1.5 text-heading font-semibold uppercase tracking-wider text-faint"
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
                              "flex w-full items-start gap-2 rounded-md px-2.5 py-2 text-left transition-colors",
                              isSelected || highlighted
                                ? "bg-accent-tint ring-1 ring-inset ring-accent/25"
                                : "hover:bg-panel2",
                            ].join(" ")}
                            onClick={() => {
                              onSelect(item.method);
                              close();
                              triggerRef.current?.focus();
                            }}
                          >
                            <span className="min-w-0 flex-1">
                              <span className="block text-sm font-medium text-foreground">
                                {item.title}
                              </span>
                              <span className="mt-0.5 block truncate font-mono text-heading text-muted">
                                {item.method}
                              </span>
                            </span>
                            <span
                              className={[
                                "mt-0.5 shrink-0 rounded-full px-1.5 py-0.5 text-body font-semibold uppercase tracking-wide ring-1 ring-inset",
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
