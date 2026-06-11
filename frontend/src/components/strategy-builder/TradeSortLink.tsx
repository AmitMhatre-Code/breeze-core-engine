"use client";

import { useEffect, useRef, useState } from "react";

export type TradeSortKey = "server" | "pop" | "net_premium" | "max_loss";

const SORT_OPTIONS: { key: TradeSortKey; label: string }[] = [
  { key: "server", label: "Server order" },
  { key: "pop", label: "PoP (high → low)" },
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

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        className="text-[11px] font-normal text-sky-600 underline underline-offset-2 hover:text-sky-500 dark:text-sky-400 dark:hover:text-sky-300"
        aria-expanded={open}
        aria-haspopup="listbox"
        onClick={() => setOpen((v) => !v)}
      >
        sort by
      </button>
      {open ? (
        <div
          className="absolute right-0 top-full z-50 mt-1 min-w-[12rem] overflow-hidden rounded-lg border border-zinc-200/90 bg-white py-1 shadow-lg ring-1 ring-zinc-950/5 dark:border-zinc-700 dark:bg-zinc-900 dark:ring-white/10"
          role="listbox"
        >
          {SORT_OPTIONS.map((opt) => {
            const active = value === opt.key;
            return (
              <button
                key={opt.key}
                type="button"
                role="option"
                aria-selected={active}
                className={`flex w-full items-center gap-2 px-3 py-2 text-left text-sm transition hover:bg-zinc-50 dark:hover:bg-zinc-800/80 ${
                  active ? "bg-sky-50/80 text-sky-800 dark:bg-sky-950/30 dark:text-sky-200" : "text-zinc-700 dark:text-zinc-200"
                }`}
                onClick={() => {
                  onChange(opt.key);
                  setOpen(false);
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
