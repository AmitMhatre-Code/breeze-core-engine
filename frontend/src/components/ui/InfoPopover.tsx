"use client";

import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { HelpLink } from "@/components/help/HelpLink";

type Props = {
  title?: string;
  children: ReactNode;
  /** Accessible label for the trigger button when title is absent. */
  ariaLabel?: string;
  /** Opens full help topic from the popover footer. */
  learnMoreTopicId?: string;
};

const PANEL_MAX_WIDTH = 256; // 16rem
const VIEWPORT_MARGIN = 8;

export function InfoPopover({ title, children, ariaLabel, learnMoreTopicId }: Props) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ top: number; left: number; flipped: boolean } | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const panelId = useId();
  const triggerLabel = ariaLabel ?? title ?? "More information";

  const close = useCallback(() => {
    setOpen(false);
    triggerRef.current?.focus();
  }, []);

  const reposition = useCallback(() => {
    const trigger = triggerRef.current;
    if (!trigger) return;
    const rect = trigger.getBoundingClientRect();
    const panelHeight = panelRef.current?.offsetHeight ?? 0;
    const spaceBelow = window.innerHeight - rect.bottom;
    const flipped = spaceBelow < panelHeight + VIEWPORT_MARGIN && rect.top > panelHeight;
    const top = flipped ? rect.top - panelHeight - 6 : rect.bottom + 6;
    const left = Math.min(
      Math.max(rect.left + rect.width / 2 - PANEL_MAX_WIDTH / 2, VIEWPORT_MARGIN),
      window.innerWidth - PANEL_MAX_WIDTH - VIEWPORT_MARGIN,
    );
    setPos({ top, left, flipped });
  }, []);

  useLayoutEffect(() => {
    if (!open) return;
    reposition();
  }, [open, reposition]);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      const target = e.target as Node;
      if (rootRef.current?.contains(target)) return;
      if (panelRef.current?.contains(target)) return;
      close();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    window.addEventListener("scroll", reposition, true);
    window.addEventListener("resize", reposition);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("scroll", reposition, true);
      window.removeEventListener("resize", reposition);
    };
  }, [open, close, reposition]);

  return (
    <div ref={rootRef} className="relative inline-flex shrink-0">
      <button
        ref={triggerRef}
        type="button"
        className="inline-flex size-4 shrink-0 cursor-pointer items-center justify-center rounded-full text-body font-bold leading-none text-faint ring-1 ring-border transition hover:text-muted hover:ring-accent/40"
        aria-expanded={open}
        aria-controls={panelId}
        aria-label={triggerLabel}
        onClick={() => setOpen((v) => !v)}
      >
        i
      </button>
      {open && typeof document !== "undefined"
        ? createPortal(
            <div
              ref={panelRef}
              id={panelId}
              role="dialog"
              aria-label={title ?? triggerLabel}
              className="fixed z-50 w-max max-w-[16rem] rounded-lg border border-border bg-elevated px-3 py-2.5 text-left text-heading leading-snug text-muted shadow-pop"
              style={{
                top: pos?.top ?? -9999,
                left: pos?.left ?? -9999,
                visibility: pos ? "visible" : "hidden",
              }}
            >
              {title ? (
                <p className="mb-1.5 text-xs font-semibold text-foreground">
                  {title}
                </p>
              ) : null}
              {children}
              {learnMoreTopicId ? (
                <p className="mt-2 border-t border-border-soft pt-2">
                  <HelpLink topicId={learnMoreTopicId} className="text-heading">
                    Learn more
                  </HelpLink>
                </p>
              ) : null}
            </div>,
            document.body,
          )
        : null}
    </div>
  );
}
