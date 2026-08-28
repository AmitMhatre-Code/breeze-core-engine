"use client";

import { useLayoutEffect, useRef, useState, type RefObject } from "react";

/**
 * Flips an anchored popover above its trigger when there isn't room below.
 *
 * Both date pickers hard-coded `top: 100%`, so on a phone a trigger in the lower
 * half of the page opened a calendar that ran off the bottom of the viewport (the
 * Order Book date range spanned y 628→1001 in an 812px viewport). Tablets had room
 * and never showed it, which is why it reads as phone-specific.
 *
 * Measures after paint, so the popover's real height is known rather than assumed.
 */
export function usePopoverPlacement(
  open: boolean,
  triggerRef: RefObject<HTMLElement | null>,
  gap = 8,
) {
  const popoverRef = useRef<HTMLDivElement>(null);
  const [placeAbove, setPlaceAbove] = useState(false);

  useLayoutEffect(() => {
    if (!open) return;
    // No reset on close: `measure()` below runs inside a layout effect, i.e. before the
    // browser paints, so a stale value from the previous open can never be shown.
    const measure = () => {
      const trigger = triggerRef.current;
      const popover = popoverRef.current;
      if (!trigger || !popover) return;
      const rect = trigger.getBoundingClientRect();
      const height = popover.offsetHeight;
      const roomBelow = window.innerHeight - rect.bottom - gap;
      const roomAbove = rect.top - gap;
      // Only flip when below genuinely can't fit AND above fits better — otherwise
      // keep the conventional downward placement.
      setPlaceAbove(height > roomBelow && roomAbove > roomBelow);
    };
    measure();
    window.addEventListener("resize", measure);
    window.addEventListener("orientationchange", measure);
    return () => {
      window.removeEventListener("resize", measure);
      window.removeEventListener("orientationchange", measure);
    };
  }, [open, triggerRef, gap]);

  return { popoverRef, placeAbove };
}
