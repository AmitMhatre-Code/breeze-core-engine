"use client";

import { useRef, useState } from "react";
import { POP_SORT_LABEL } from "@/lib/strategy-builder/pop-help";
import {
  useListboxMenu,
  useListboxOutsideClose,
} from "@/lib/ui/use-listbox-menu";

export type TradeSortKey = "score" | "server" | "pop" | "net_premium" | "max_loss";

const SORT_OPTIONS: { key: TradeSortKey; label: string }[] = [
  { key: "score", label: "Score (high → low)" },
  { key: "server", label: "Server order" },
  { key: "pop", label: POP_SORT_LABEL },
  { key: "net_premium", label: "Net Premium (high → low)" },
  { key: "max_loss", label: "Max Loss (low → high)" },
];

export function TradeSortLink({
  value,
  onChange,
}: {
  value: TradeSortKey;
  onChange: (key: TradeSortKey) => void;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const listId = "trade-sort-listbox";

  const close = () => setOpen(false);

  useListboxOutsideClose(open, rootRef, close);

  const { highlightIndex, handleTriggerKeyDown } = useListboxMenu({
    open,
    optionCount: SORT_OPTIONS.length,
    onOpen: () => setOpen(true),
    onClose: () => {
      close();
      triggerRef.current?.focus();
    },
    onSelectIndex: (index) => {
      const opt = SORT_OPTIONS[index];
      if (opt) onChange(opt.key);
      close();
      triggerRef.current?.focus();
    },
    triggerRef,
    listRef,
  });

  return (
    <div ref={rootRef} className="relative">
      <button
        ref={triggerRef}
        type="button"
        className="text-[11px] font-normal text-sky-600 underline underline-offset-2 hover:text-sky-500 dark:text-sky-400 dark:hover:text-sky-300"
        aria-expanded={open}
        aria-haspopup="listbox"
        aria-controls={listId}
        onClick={() => setOpen((v) => !v)}
        onKeyDown={handleTriggerKeyDown}
      >
        sort by
      </button>
      {open ? (
        <div
          ref={listRef}
          id={listId}
          className="absolute right-0 top-full z-50 mt-1 min-w-[12rem] overflow-hidden rounded-lg border border-zinc-200/90 bg-white py-1 shadow-lg ring-1 ring-zinc-950/5 dark:border-zinc-700 dark:bg-zinc-900 dark:ring-white/10"
          role="listbox"
        >
          {SORT_OPTIONS.map((opt, index) => {
            const active = value === opt.key;
            const highlighted = index === highlightIndex;
            return (
              <button
                key={opt.key}
                type="button"
                role="option"
                tabIndex={-1}
                data-menu-index={index}
                aria-selected={active}
                className={`flex w-full items-center gap-2 px-3 py-2 text-left text-sm transition hover:bg-zinc-50 dark:hover:bg-zinc-800/80 ${
                  highlighted
                    ? "bg-sky-100 text-sky-900 dark:bg-sky-900/50 dark:text-sky-200"
                    : active
                      ? "bg-sky-50/80 text-sky-800 dark:bg-sky-950/30 dark:text-sky-200"
                      : "text-zinc-700 dark:text-zinc-200"
                }`}
                onClick={() => {
                  onChange(opt.key);
                  close();
                }}
              >
                <span className="w-4 shrink-0 text-center text-xs" aria-hidden>
                  {active ? "✓" : ""}
                </span>
                {opt.label}
              </button>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}
