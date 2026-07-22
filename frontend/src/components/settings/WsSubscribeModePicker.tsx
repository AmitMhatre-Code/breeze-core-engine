"use client";

import { useCallback, useId, useRef, useState } from "react";

import {
  WS_SUBSCRIBE_MODE_OPTIONS,
  type WsSubscribeMode,
} from "@/lib/breeze-api-playground-ws-subscribe";
import {
  useListboxMenu,
  useListboxOutsideClose,
} from "@/lib/ui/use-listbox-menu";

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

export function WsSubscribeModePicker({
  value,
  onChange,
}: {
  value: WsSubscribeMode;
  onChange: (mode: WsSubscribeMode) => void;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const listId = useId();
  const labelId = useId();

  const options = WS_SUBSCRIBE_MODE_OPTIONS;
  const selected = options.find((o) => o.value === value) ?? options[0];

  const close = useCallback(() => setOpen(false), []);

  useListboxOutsideClose(open, rootRef, close);

  const { highlightIndex, handleTriggerKeyDown } = useListboxMenu({
    open,
    optionCount: options.length,
    onOpen: () => setOpen(true),
    onClose: () => {
      close();
      triggerRef.current?.focus();
    },
    onSelectIndex: (index) => {
      const opt = options[index];
      if (opt) onChange(opt.value);
      close();
      triggerRef.current?.focus();
    },
    triggerRef,
    listRef,
  });

  return (
    <div ref={rootRef} className="relative w-full text-left">
      <span id={labelId} className="app-text-muted">
        Subscribe mode
      </span>

      <button
        ref={triggerRef}
        type="button"
        className={[
          "app-input mt-1.5 flex items-center justify-between gap-3 py-2.5 text-left",
          open ? "border-accent ring-2 ring-accent/30" : "",
        ].join(" ")}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={listId}
        aria-labelledby={labelId}
        onClick={() => setOpen((v) => !v)}
        onKeyDown={handleTriggerKeyDown}
      >
        <span className="truncate text-sm font-medium text-foreground">
          {selected.label}
        </span>
        <ChevronDown open={open} />
      </button>

      {open ? (
        <ul
          ref={listRef}
          id={listId}
          role="listbox"
          aria-labelledby={labelId}
          className="absolute left-0 right-0 top-[calc(100%+6px)] z-40 overflow-hidden rounded-[10px] border border-border bg-elevated py-1 shadow-pop"
        >
          {options.map((opt, index) => {
            const isSelected = opt.value === value;
            const highlighted = index === highlightIndex;
            return (
              <li key={opt.value} role="presentation">
                <button
                  type="button"
                  role="option"
                  tabIndex={-1}
                  data-menu-index={index}
                  aria-selected={isSelected}
                  className={[
                    "flex w-full items-center px-3 py-2.5 text-left text-sm transition-colors",
                    highlighted
                      ? "bg-accent-tint text-accent-strong"
                      : isSelected
                        ? "bg-panel2 font-medium text-foreground"
                        : "text-foreground hover:bg-panel2",
                  ].join(" ")}
                  onClick={() => {
                    onChange(opt.value);
                    close();
                    triggerRef.current?.focus();
                  }}
                >
                  {opt.label}
                </button>
              </li>
            );
          })}
        </ul>
      ) : null}
    </div>
  );
}
