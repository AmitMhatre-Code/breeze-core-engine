"use client";

import { useCallback, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { LiquidityWarning } from "@/lib/liquidity";

const PANEL_MAX_WIDTH = 288; // 18rem
const VIEWPORT_MARGIN = 8;

/**
 * ⚠ beside a ticket's quantity when the live book cannot absorb it near the LTP
 * (docs/liquidity-checks-plan.md). Hover, focus or tap shows why. It never blocks anything.
 *
 * The panel is portalled: the leg tables scroll sideways, and a positioned child would be
 * clipped by them.
 */
export function LiquidityWarningIcon({ warning }: { warning: LiquidityWarning }) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const panelId = useId();

  const reposition = useCallback(() => {
    const trigger = triggerRef.current;
    if (!trigger) return;
    const rect = trigger.getBoundingClientRect();
    const panelHeight = panelRef.current?.offsetHeight ?? 0;
    const below = window.innerHeight - rect.bottom >= panelHeight + VIEWPORT_MARGIN;
    const top = below || rect.top < panelHeight ? rect.bottom + 6 : rect.top - panelHeight - 6;
    const left = Math.min(
      Math.max(rect.left + rect.width / 2 - PANEL_MAX_WIDTH / 2, VIEWPORT_MARGIN),
      window.innerWidth - PANEL_MAX_WIDTH - VIEWPORT_MARGIN,
    );
    setPos({ top, left });
  }, []);

  useLayoutEffect(() => {
    if (open) reposition();
  }, [open, reposition]);

  if (!warning) return null;
  const label = warning.messages.join(" ");
  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        className="inline-flex size-5 shrink-0 cursor-help items-center justify-center text-base leading-none text-amber-accent focus:outline-none focus-visible:ring-2 focus-visible:ring-amber-accent/40"
        aria-label={`Liquidity warning: ${label}`}
        aria-describedby={open ? panelId : undefined}
        data-testid="liquidity-warning"
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        // Opens, never toggles: a mouse hovers before it clicks, and a toggle would shut the panel
        // it just opened. Tapping elsewhere closes it (blur).
        onClick={() => setOpen(true)}
      >
        ⚠
      </button>
      {open && typeof document !== "undefined"
        ? createPortal(
            <div
              ref={panelRef}
              id={panelId}
              role="tooltip"
              className="fixed z-50 w-max max-w-[18rem] space-y-1.5 rounded-lg border border-border bg-elevated px-3 py-2.5 text-left text-xs leading-snug text-muted shadow-pop"
              style={{
                top: pos?.top ?? -9999,
                left: pos?.left ?? -9999,
                visibility: pos ? "visible" : "hidden",
              }}
            >
              {warning.messages.map((m) => (
                <p key={m}>{m}</p>
              ))}
            </div>,
            document.body,
          )
        : null}
    </>
  );
}

/** The confirmation modal's version: the same messages in red, each prefixed with ⚠. */
export function LiquidityWarningLines({ warning }: { warning: LiquidityWarning }) {
  if (!warning) return null;
  return (
    <div className="mt-1 space-y-0.5" data-testid="liquidity-warning-lines">
      {warning.messages.map((m) => (
        <p key={m} className="whitespace-normal font-sans text-xs leading-snug text-down">
          <span aria-hidden>⚠ </span>
          {m}
        </p>
      ))}
    </div>
  );
}
