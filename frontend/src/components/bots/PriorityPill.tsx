"use client";

import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { BOT_META, useBots, useUpdateBot, type Bot } from "@/lib/use-bots";

/** The card's priority badge, as a picker rather than a typed number.
 *
 *  A typed field committed on every keystroke (typing "12" saved 1, then 12), re-sorted the
 *  page under the cursor, and never showed who else held a slot. Here each slot is listed
 *  with the bot currently in it, and taking a held slot swaps the two — the server does the
 *  swap, so priorities stay a ranking and the scheduler never has to break a tie.
 */
export function PriorityPill({
  bot,
  readOnly,
  onError,
}: {
  bot: Bot;
  readOnly: boolean;
  onError: (message: string | null) => void;
}) {
  const { data: bots = [] } = useBots();
  const update = useUpdateBot();
  const [open, setOpen] = useState(false);
  const [highlight, setHighlight] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const title = BOT_META[bot.bot_type]?.title ?? bot.bot_type;

  // One slot per bot; a custom number beyond that (set before the picker existed) still
  // gets a row, so the current value is always visible in the list.
  const slotCount = Math.max(bots.length, ...bots.map((b) => b.priority), bot.priority);
  const slots = Array.from({ length: slotCount }, (_, i) => {
    const priority = i + 1;
    return { priority, holder: bots.find((b) => b.priority === priority && b.id !== bot.id) };
  });

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  function openList() {
    setHighlight(Math.max(0, bot.priority - 1));
    setOpen(true);
  }

  async function choose(priority: number) {
    setOpen(false);
    if (priority === bot.priority) return;
    onError(null);
    try {
      await update.mutateAsync({ botType: bot.bot_type, priority });
    } catch (e) {
      onError((e as Error)?.message ?? "Could not save.");
    }
  }

  function onKeyDown(e: KeyboardEvent<HTMLButtonElement>) {
    if (!open) {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        openList();
      }
      return;
    }
    if (e.key === "Escape") {
      e.preventDefault();
      setOpen(false);
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      setHighlight((i) => (i + 1) % slots.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlight((i) => (i - 1 + slots.length) % slots.length);
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      void choose(slots[highlight]!.priority);
    } else if (e.key === "Tab") {
      setOpen(false);
    }
  }

  const disabled = readOnly || update.isPending;

  return (
    <div ref={rootRef} className={`relative inline-block ${open ? "z-[300]" : ""}`}>
      <button
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={`Priority for ${title}: ${bot.priority}. Change priority`}
        disabled={disabled}
        onClick={() => (open ? setOpen(false) : openList())}
        onKeyDown={onKeyDown}
        className="inline-flex items-center gap-1.5 rounded border border-gtt/30 bg-gtt-tint px-2 py-0.5 font-mono text-micro font-bold uppercase tracking-[0.06em] text-gtt-on-tint transition hover:border-gtt/60 focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/45 disabled:cursor-not-allowed disabled:opacity-50"
      >
        Priority
        <span>{bot.priority}</span>
        <span aria-hidden className="text-[0.7em] opacity-70">
          ▾
        </span>
      </button>

      {open ? (
        <ul
          role="listbox"
          aria-label={`Priority for ${title}`}
          className="absolute left-0 top-full mt-1 w-64 rounded-lg border border-border bg-elevated p-1 shadow-pop"
        >
          <li className="px-3 pb-1.5 pt-1 text-hint text-muted">
            Lower runs first and sizes against margin before the others.
          </li>
          {slots.map(({ priority, holder }, index) => {
            const current = priority === bot.priority;
            return (
              <li key={priority}>
                <button
                  type="button"
                  role="option"
                  tabIndex={-1}
                  aria-selected={current}
                  onMouseEnter={() => setHighlight(index)}
                  onClick={() => void choose(priority)}
                  className={`flex w-full items-baseline gap-3 rounded-md px-3 py-1.5 text-left text-sm transition ${
                    index === highlight ? "bg-panel2" : ""
                  } ${current ? "font-semibold text-accent-strong" : "text-foreground"}`}
                >
                  <span className="w-4 shrink-0 font-mono">{priority}</span>
                  <span className="min-w-0 flex-1 truncate">
                    {current ? title : (holder ? BOT_META[holder.bot_type]?.title : null) ?? "—"}
                  </span>
                  {current ? (
                    <span className="shrink-0 text-hint text-muted">current</span>
                  ) : holder ? (
                    <span className="shrink-0 text-hint text-muted">swap</span>
                  ) : null}
                </button>
              </li>
            );
          })}
        </ul>
      ) : null}
    </div>
  );
}
