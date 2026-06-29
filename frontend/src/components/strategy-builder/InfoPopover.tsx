"use client";

import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from "react";
import { HelpLink } from "@/components/help/HelpLink";

type Props = {
  title?: string;
  children: ReactNode;
  /** Accessible label for the trigger button when title is absent. */
  ariaLabel?: string;
  /** Opens full help topic from the popover footer. */
  learnMoreTopicId?: string;
};

export function InfoPopover({ title, children, ariaLabel, learnMoreTopicId }: Props) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelId = useId();
  const triggerLabel = ariaLabel ?? title ?? "More information";

  const close = useCallback(() => {
    setOpen(false);
    triggerRef.current?.focus();
  }, []);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) close();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, close]);

  return (
    <div ref={rootRef} className="relative inline-flex shrink-0">
      <button
        ref={triggerRef}
        type="button"
        className="inline-flex size-4 shrink-0 cursor-pointer items-center justify-center rounded-full text-[10px] font-bold leading-none text-zinc-400 ring-1 ring-zinc-300/80 transition hover:text-zinc-600 hover:ring-zinc-400 dark:text-zinc-500 dark:ring-zinc-600 dark:hover:text-zinc-300 dark:hover:ring-zinc-500"
        aria-expanded={open}
        aria-controls={panelId}
        aria-label={triggerLabel}
        onClick={() => setOpen((v) => !v)}
      >
        i
      </button>
      {open ? (
        <div
          id={panelId}
          role="dialog"
          aria-label={title ?? triggerLabel}
          className="absolute left-1/2 top-full z-50 mt-1.5 w-max max-w-[16rem] -translate-x-1/2 rounded-lg border border-zinc-200/90 bg-white px-3 py-2.5 text-left text-[11px] leading-snug text-zinc-700 shadow-lg ring-1 ring-zinc-950/5 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200 dark:ring-white/10"
        >
          {title ? (
            <p className="mb-1.5 text-xs font-semibold text-zinc-900 dark:text-zinc-100">
              {title}
            </p>
          ) : null}
          {children}
          {learnMoreTopicId ? (
            <p className="mt-2 border-t border-zinc-100 pt-2 dark:border-zinc-700">
              <HelpLink topicId={learnMoreTopicId} className="text-[11px]">
                Learn more
              </HelpLink>
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
