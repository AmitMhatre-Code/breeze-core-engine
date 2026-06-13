"use client";

import { useEffect, useId, useRef } from "react";

export function ApiUsageWarningDialog(props: {
  open: boolean;
  message: string;
  onDismiss: () => void;
}) {
  const { open, message, onDismiss } = props;
  const titleId = useId();
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onDismiss();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onDismiss]);

  useEffect(() => {
    if (!open) return;
    const id = window.requestAnimationFrame(() => closeRef.current?.focus());
    return () => window.cancelAnimationFrame(id);
  }, [open]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-black/45 p-4"
      role="presentation"
      onClick={onDismiss}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="w-full max-w-md rounded-xl border border-amber-500/50 bg-white p-5 shadow-xl dark:border-amber-400/40 dark:bg-zinc-950"
        onClick={(e) => e.stopPropagation()}
      >
        <h2
          id={titleId}
          className="text-base font-semibold text-amber-950 dark:text-amber-100"
        >
          Daily API usage warning
        </h2>
        <p className="mt-3 text-sm leading-relaxed text-zinc-700 dark:text-zinc-300">
          {message}
        </p>
        <div className="mt-5 flex justify-end">
          <button
            ref={closeRef}
            type="button"
            className="app-btn-primary rounded-lg px-4 py-2 text-sm font-medium"
            onClick={onDismiss}
          >
            Understood
          </button>
        </div>
      </div>
    </div>
  );
}
