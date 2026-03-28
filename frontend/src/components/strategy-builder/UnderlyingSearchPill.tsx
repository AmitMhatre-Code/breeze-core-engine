"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { UnderlyingEntry } from "@/lib/strategy-builder/types";

const RECENT_CODES = ["NIFTY", "BANKNIFTY"] as const;

type Props = {
  underlyings: UnderlyingEntry[];
  value: string;
  onChange: (stockCode: string) => void;
  disabled?: boolean;
};

export function UnderlyingSearchPill({
  underlyings,
  value,
  onChange,
  disabled,
}: Props) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const rootRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const sorted = useMemo(
    () =>
      [...underlyings].sort((a, b) =>
        a.stock_code.localeCompare(b.stock_code),
      ),
    [underlyings],
  );

  const qn = q.trim().toLowerCase();
  const filtered = useMemo(() => {
    if (!qn) return sorted;
    return sorted.filter(
      (u) =>
        u.stock_code.toLowerCase().includes(qn) ||
        (u.long_name ?? "").toLowerCase().includes(qn),
    );
  }, [sorted, qn]);

  const recent = useMemo(() => {
    return RECENT_CODES.map((code) =>
      sorted.find((u) => u.stock_code === code),
    ).filter((u): u is UnderlyingEntry => Boolean(u));
  }, [sorted]);

  const select = (code: string) => {
    onChange(code);
    setOpen(false);
    setQ("");
  };

  useEffect(() => {
    if (!open) return;
    const id = requestAnimationFrame(() => inputRef.current?.focus());
    return () => cancelAnimationFrame(id);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const fn = (e: MouseEvent) => {
      if (rootRef.current?.contains(e.target as Node)) return;
      setOpen(false);
    };
    document.addEventListener("mousedown", fn);
    return () => document.removeEventListener("mousedown", fn);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  const selected = sorted.find((u) => u.stock_code === value);

  return (
    <div
      ref={rootRef}
      className={`relative min-w-[min(100%,14rem)] flex-1 lg:max-w-md ${open ? "z-[300]" : "z-0"}`}
    >
      <span className="mb-1.5 block text-xs font-medium text-zinc-600 dark:text-zinc-400">
        Underlying
      </span>
      <button
        type="button"
        disabled={disabled}
        aria-expanded={open}
        aria-haspopup="listbox"
        onClick={() => !disabled && setOpen((o) => !o)}
        className="flex w-full min-w-0 items-center justify-between gap-2 rounded-md border border-zinc-200 bg-white px-3.5 py-2.5 text-left text-sm text-zinc-900 shadow-sm outline-none transition hover:border-zinc-300 focus-visible:border-sky-500 focus-visible:ring-4 focus-visible:ring-sky-500/15 disabled:cursor-not-allowed disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:hover:border-zinc-600 dark:focus-visible:border-sky-400 dark:focus-visible:ring-sky-400/20"
      >
        <span className="min-w-0 flex-1 truncate">
          {value ? (
            <>
              <span className="font-semibold">{value}</span>
              {selected?.long_name ? (
                <span className="ml-1.5 text-xs font-normal text-zinc-500 dark:text-zinc-400">
                  {selected.long_name}
                </span>
              ) : null}
            </>
          ) : (
            <span className="text-zinc-400 dark:text-zinc-500">
              Select underlying…
            </span>
          )}
        </span>
        <span className="shrink-0 text-zinc-400" aria-hidden>
          ▾
        </span>
      </button>

      {open ? (
        <>
          <div
            className="fixed inset-0 z-[295] bg-black/45 lg:hidden"
            role="presentation"
            aria-hidden
            onClick={() => setOpen(false)}
          />
          <div
            className="fixed inset-0 z-[300] flex flex-col bg-zinc-50 dark:bg-zinc-950 lg:absolute lg:inset-x-auto lg:inset-y-auto lg:left-0 lg:top-full lg:z-[300] lg:mt-1.5 lg:max-h-[min(22rem,70vh)] lg:w-full lg:min-w-[18rem] lg:max-w-lg lg:rounded-md lg:border lg:border-zinc-200 lg:bg-white lg:shadow-xl lg:dark:border-zinc-700 lg:dark:bg-zinc-900"
            role="listbox"
            aria-label="Search underlyings"
          >
            <div className="flex shrink-0 items-center justify-between border-b border-zinc-200 px-3 py-2.5 dark:border-zinc-800 lg:hidden">
              <span className="text-sm font-semibold text-zinc-900 dark:text-zinc-50">
                Choose underlying
              </span>
              <button
                type="button"
                className="rounded-md p-2 text-zinc-500 hover:bg-zinc-200/80 dark:hover:bg-zinc-800"
                aria-label="Close"
                onClick={() => setOpen(false)}
              >
                <span className="text-xl leading-none" aria-hidden>
                  ×
                </span>
              </button>
            </div>
            <div className="shrink-0 border-b border-zinc-200 p-2 dark:border-zinc-800 lg:border-t-0 lg:pt-2">
              <input
                ref={inputRef}
                type="search"
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="Search name or symbol…"
                autoComplete="off"
                className="w-full rounded-md border border-zinc-200 bg-white px-3 py-2 text-sm text-zinc-900 outline-none placeholder:text-zinc-400 focus:border-sky-500 focus:ring-2 focus:ring-sky-500/20 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:placeholder:text-zinc-500"
              />
            </div>

            {recent.length > 0 ? (
              <div className="shrink-0 border-b border-zinc-200 px-3 py-2 dark:border-zinc-800 lg:hidden">
                <div className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                  Recently traded
                </div>
                <div className="flex flex-wrap gap-2">
                  {recent.map((u) => (
                    <button
                      key={u.stock_code}
                      type="button"
                      className="rounded-full border border-zinc-200 bg-white px-3 py-1.5 text-xs font-semibold text-zinc-800 shadow-sm transition hover:border-sky-300 hover:bg-sky-50 dark:border-zinc-600 dark:bg-zinc-900 dark:text-zinc-100 dark:hover:border-sky-600 dark:hover:bg-sky-950/40"
                      onClick={() => select(u.stock_code)}
                    >
                      {u.stock_code}
                    </button>
                  ))}
                </div>
              </div>
            ) : null}

            <ul className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-1 lg:max-h-[min(18rem,50vh)]">
              {filtered.length === 0 ? (
                <li className="px-3 py-6 text-center text-sm text-zinc-500 dark:text-zinc-400">
                  No matches
                </li>
              ) : (
                filtered.map((u) => (
                  <li key={u.stock_code}>
                    <button
                      type="button"
                      role="option"
                      aria-selected={u.stock_code === value}
                      className={`flex w-full flex-col items-start rounded-md px-3 py-2 text-left text-sm transition hover:bg-zinc-100 dark:hover:bg-zinc-800/80 ${u.stock_code === value ? "bg-sky-50 text-sky-900 dark:bg-sky-950/40 dark:text-sky-100" : "text-zinc-900 dark:text-zinc-100"}`}
                      onClick={() => select(u.stock_code)}
                    >
                      <span className="font-semibold">{u.stock_code}</span>
                      {u.long_name ? (
                        <span className="text-xs text-zinc-500 dark:text-zinc-400">
                          {u.long_name}
                        </span>
                      ) : null}
                    </button>
                  </li>
                ))
              )}
            </ul>
          </div>
        </>
      ) : null}
    </div>
  );
}
