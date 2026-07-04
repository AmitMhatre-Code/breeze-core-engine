"use client";

import {
  useEffect,
  useRef,
  useSyncExternalStore,
  type ReactNode,
  type RefObject,
} from "react";
import { createPortal } from "react-dom";
import { getFocusableElements } from "@/lib/ui/focusable";
import { useFocusTrap } from "@/lib/ui/use-focus-trap";

export type ModalVariant = "centered" | "bottomSheet" | "drawer";

export type ModalProps = {
  open: boolean;
  onClose: () => void;
  children: ReactNode;
  titleId?: string;
  descriptionId?: string;
  role?: "dialog" | "alertdialog";
  /** Backdrop click closes when true (default). */
  dismissible?: boolean;
  closeOnEscape?: boolean;
  /** When true, dismiss is blocked (e.g. pending submit). */
  pending?: boolean;
  variant?: ModalVariant;
  initialFocusRef?: RefObject<HTMLElement | null>;
  returnFocus?: boolean;
  className?: string;
  panelClassName?: string;
  zIndexClass?: string;
};

export function Modal({
  open,
  onClose,
  children,
  titleId,
  descriptionId,
  role = "dialog",
  dismissible = true,
  closeOnEscape,
  pending = false,
  variant = "centered",
  initialFocusRef,
  returnFocus = true,
  className,
  panelClassName,
  zIndexClass = "z-50",
}: ModalProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);
  const portalReady = useSyncExternalStore(
    () => () => {},
    () => true,
    () => false,
  );

  const canDismiss = dismissible && !pending;
  const escapeCloses = closeOnEscape ?? dismissible;

  useFocusTrap(open, panelRef);

  useEffect(() => {
    if (!open) return;
    previousFocusRef.current = document.activeElement as HTMLElement | null;
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const id = requestAnimationFrame(() => {
      if (initialFocusRef?.current) {
        initialFocusRef.current.focus();
        return;
      }
      const panel = panelRef.current;
      if (!panel) return;
      const focusable = getFocusableElements(panel);
      focusable[0]?.focus();
    });
    return () => {
      cancelAnimationFrame(id);
      document.body.style.overflow = prevOverflow;
      if (returnFocus) {
        previousFocusRef.current?.focus?.();
      }
    };
  }, [open, initialFocusRef, returnFocus]);

  useEffect(() => {
    if (!open || !escapeCloses) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && canDismiss) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, escapeCloses, canDismiss, onClose]);

  if (!open || !portalReady) return null;

  const shellClass =
    variant === "bottomSheet"
      ? `fixed inset-0 ${zIndexClass} flex flex-col justify-end sm:items-center sm:justify-center sm:p-4`
      : variant === "drawer"
        ? `fixed inset-0 ${zIndexClass}`
        : `fixed inset-0 ${zIndexClass} flex items-center justify-center p-4`;

  const panelVariantClass =
    variant === "bottomSheet"
      ? [
          "relative z-[1] flex max-h-[min(32rem,88dvh)] w-full min-w-0 flex-col overflow-hidden border-x border-t border-border bg-elevated shadow-pop",
          "rounded-t-2xl sm:mx-auto sm:mb-0 sm:max-h-[min(32rem,85vh)] sm:w-full sm:max-w-lg sm:rounded-lg sm:border",
          "mb-[max(0px,env(safe-area-inset-bottom))]",
        ].join(" ")
      : variant === "drawer"
        ? "absolute inset-y-0 left-0 z-[1] flex w-[min(100%,18rem)] flex-col border-r border-border bg-elevated shadow-pop"
        : "relative z-[1] w-full max-w-lg";

  const backdrop = canDismiss ? (
    <button
      type="button"
      className="absolute inset-0 bg-black/50 dark:bg-black/60"
      aria-label="Close dialog"
      onClick={onClose}
    />
  ) : (
    <div
      className="absolute inset-0 bg-black/50 dark:bg-black/60"
      role="presentation"
      aria-hidden
    />
  );

  return createPortal(
    <div className={[shellClass, className].filter(Boolean).join(" ")}>
      {variant === "drawer" ? (
        <>
          {backdrop}
          <div
            ref={panelRef}
            role={role}
            aria-modal="true"
            aria-labelledby={titleId}
            aria-describedby={descriptionId}
            className={[panelVariantClass, panelClassName].filter(Boolean).join(" ")}
          >
            {children}
          </div>
        </>
      ) : (
        <>
          {backdrop}
          <div
            ref={panelRef}
            role={role}
            aria-modal="true"
            aria-labelledby={titleId}
            aria-describedby={descriptionId}
            className={[panelVariantClass, panelClassName].filter(Boolean).join(" ")}
            onClick={(e) => e.stopPropagation()}
          >
            {children}
          </div>
        </>
      )}
    </div>,
    document.body,
  );
}
