"use client";

import { Modal } from "@/components/ui/Modal";

const NAV_ROWS = [
  { keys: "Alt + 1", action: "Go to Dashboard" },
  { keys: "Alt + 2", action: "Go to Portfolio" },
  { keys: "Alt + 3", action: "Go to Performance" },
  { keys: "Alt + 4", action: "Go to Order Book" },
  { keys: "Alt + 5", action: "Go to Place Order" },
  { keys: "Alt + 6", action: "Go to Basket Order" },
  { keys: "Alt + 7", action: "Go to Strategy Builder" },
  { keys: "Alt + 8", action: "Go to Settings" },
] as const;

const CONTEXT_ROWS = [
  { keys: "/", action: "Focus scrip search (trading pages)" },
  { keys: "?", action: "Open this help dialog" },
  { keys: "Escape", action: "Close the topmost dialog or menu" },
] as const;

export function KeyboardShortcutsDialog({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  return (
    <Modal open={open} onClose={onClose} titleId="keyboard-shortcuts-title">
      <div className="rounded-lg border border-zinc-200 bg-white p-5 shadow-2xl dark:border-zinc-800 dark:bg-zinc-950">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2
              id="keyboard-shortcuts-title"
              className="text-base font-semibold text-zinc-900 dark:text-zinc-100"
            >
              Keyboard shortcuts
            </h2>
            <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">
              Shortcuts are disabled while typing in a field.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="inline-flex size-9 shrink-0 items-center justify-center rounded-md text-zinc-600 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-900"
            aria-label="Close"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden>
              <path d="M18 6 6 18M6 6l12 12" strokeLinecap="round" />
            </svg>
          </button>
        </div>

        <div className="mt-4 space-y-4 text-sm">
          <section>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
              Navigation
            </h3>
            <ul className="mt-2 space-y-1.5">
              {NAV_ROWS.map((row) => (
                <li key={row.keys} className="flex justify-between gap-4">
                  <kbd className="shrink-0 rounded border border-zinc-200 bg-zinc-50 px-1.5 py-0.5 font-mono text-xs text-zinc-700 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200">
                    {row.keys}
                  </kbd>
                  <span className="text-right text-zinc-700 dark:text-zinc-300">
                    {row.action}
                  </span>
                </li>
              ))}
            </ul>
          </section>

          <section>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
              Trading pages
            </h3>
            <ul className="mt-2 space-y-1.5">
              {CONTEXT_ROWS.map((row) => (
                <li key={row.keys} className="flex justify-between gap-4">
                  <kbd className="shrink-0 rounded border border-zinc-200 bg-zinc-50 px-1.5 py-0.5 font-mono text-xs text-zinc-700 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200">
                    {row.keys}
                  </kbd>
                  <span className="text-right text-zinc-700 dark:text-zinc-300">
                    {row.action}
                  </span>
                </li>
              ))}
            </ul>
          </section>
        </div>
      </div>
    </Modal>
  );
}
