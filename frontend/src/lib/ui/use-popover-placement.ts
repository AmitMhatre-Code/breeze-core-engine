"use client";

import { useLayoutEffect, useRef, useState, type CSSProperties, type RefObject } from "react";

/** Keeps a popover this far inside the viewport's left and right edges. */
const EDGE = 8;

/**
 * Places an anchored popover with `position: fixed`, below its anchor or above it when
 * there isn't room below.
 *
 * Fixed rather than absolute because the date picker sits inside containers that clip:
 * the Storage delete dialog scrolls (`overflow-y-auto`), so an absolutely positioned
 * calendar inside it was cut off and had to be scrolled to within the dialog. The
 * popover stays in the DOM under its picker, so outside-click checks still see it as
 * inside. Callers must not sit under a `transform`, which would re-anchor `fixed`.
 *
 * The flip exists because a hard-coded downward calendar on a phone ran off the bottom
 * of the viewport (the Order Book date range spanned y 628→1001 in an 812px viewport).
 * Measures before paint, so the popover's real size is known rather than assumed, and
 * re-measures on scroll and resize so it follows its anchor.
 */
export function usePopoverPlacement(
  open: boolean,
  anchorRef: RefObject<HTMLElement | null>,
  gap = 6,
) {
  const popoverRef = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);

  useLayoutEffect(() => {
    if (!open) return;
    const measure = () => {
      const anchor = anchorRef.current;
      const popover = popoverRef.current;
      if (!anchor || !popover) return;
      const rect = anchor.getBoundingClientRect();
      const height = popover.offsetHeight;
      const width = popover.offsetWidth;
      const roomBelow = window.innerHeight - rect.bottom - gap;
      const roomAbove = rect.top - gap;
      // Only flip when below genuinely can't fit AND above fits better — otherwise
      // keep the conventional downward placement.
      const above = height > roomBelow && roomAbove > roomBelow;
      setPos({
        top: above ? rect.top - gap - height : rect.bottom + gap,
        left: Math.max(EDGE, Math.min(rect.left, window.innerWidth - width - EDGE)),
      });
    };
    measure();
    window.addEventListener("resize", measure);
    window.addEventListener("orientationchange", measure);
    // Capture, so scrolling any container (a dialog, a panel) moves the popover too.
    window.addEventListener("scroll", measure, true);
    return () => {
      window.removeEventListener("resize", measure);
      window.removeEventListener("orientationchange", measure);
      window.removeEventListener("scroll", measure, true);
    };
  }, [open, anchorRef, gap]);

  // Hidden until measured; the measurement runs before paint, so nothing flashes.
  const style: CSSProperties = pos
    ? { position: "fixed", top: pos.top, left: pos.left }
    : { position: "fixed", top: 0, left: 0, visibility: "hidden" };

  return { popoverRef, style };
}
