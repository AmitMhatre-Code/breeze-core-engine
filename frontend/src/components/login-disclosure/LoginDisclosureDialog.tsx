"use client";

import { MarkdownContent } from "@/components/markdown/MarkdownContent";

type Props = {
  open: boolean;
  pending: boolean;
  contentMarkdown: string;
  version: number | null;
  effectiveDate: string | null;
  onProceed: () => void;
};

export function LoginDisclosureDialog({
  open,
  pending,
  contentMarkdown,
  version,
  effectiveDate,
  onProceed,
}: Props) {
  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-[200] flex items-center justify-center bg-black/70 p-4"
      role="presentation"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="login-disclosure-title"
        className="flex max-h-[90vh] w-full max-w-3xl flex-col rounded-xl border border-zinc-700 bg-zinc-950 shadow-2xl"
      >
        <header className="shrink-0 border-b border-zinc-800 px-5 py-4">
          <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-blue-400">
            Required
          </p>
          <h2
            id="login-disclosure-title"
            className="text-lg font-bold tracking-tight text-zinc-50"
          >
            SEBI Risk Disclosure
          </h2>
          {version != null ? (
            <p className="mt-1 text-xs text-zinc-500">
              Version {version}
              {effectiveDate ? ` · Effective ${effectiveDate}` : null}
            </p>
          ) : null}
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
          <MarkdownContent
            markdown={contentMarkdown}
            className="text-sm leading-relaxed text-zinc-300"
          />
        </div>

        <footer className="shrink-0 border-t border-zinc-800 px-5 py-4">
          <p className="mb-3 text-xs leading-relaxed text-zinc-500">
            You must read and acknowledge the risk disclosure before using Breeze Modern.
          </p>
          <div className="flex justify-end">
            <button
              type="button"
              disabled={pending}
              onClick={onProceed}
              className="app-btn-primary min-w-[10rem]"
            >
              {pending ? "Saving…" : "Proceed"}
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
}
