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
        className="inline-flex size-4 shrink-0 cursor-pointer items-center justify-center rounded-full text-[12px] font-bold leading-none text-faint ring-1 ring-border transition hover:text-muted hover:ring-accent/40"
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
          className="absolute left-1/2 top-full z-50 mt-1.5 w-max max-w-[16rem] -translate-x-1/2 rounded-lg border border-border bg-elevated px-3 py-2.5 text-left text-[13px] leading-snug text-muted shadow-pop"
        >
          {title ? (
            <p className="mb-1.5 text-xs font-semibold text-foreground">
              {title}
            </p>
          ) : null}
          {children}
          {learnMoreTopicId ? (
            <p className="mt-2 border-t border-border-soft pt-2">
              <HelpLink topicId={learnMoreTopicId} className="text-[13px]">
                Learn more
              </HelpLink>
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
