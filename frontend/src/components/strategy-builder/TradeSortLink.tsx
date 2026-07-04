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
        className="text-[13px] font-normal text-accent-strong underline underline-offset-2 hover:text-accent"
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
          className="absolute right-0 top-full z-50 mt-1 min-w-[12rem] overflow-hidden rounded-lg border border-border bg-elevated py-1 shadow-pop"
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
                className={`flex w-full items-center gap-2 px-3 py-2 text-left text-sm transition hover:bg-panel2 ${
                  highlighted
                    ? "bg-accent-tint font-semibold text-accent-strong"
                    : active
                      ? "bg-panel2 text-foreground"
                      : "text-foreground"
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
