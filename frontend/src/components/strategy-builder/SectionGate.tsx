"use client";

import type { ReactNode } from "react";

export function SectionGate({
  locked,
  hint,
  children,
}: {
  locked: boolean;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <div className="relative">
      <div className={locked ? "pointer-events-none select-none opacity-40" : undefined}>
        {children}
      </div>
      {locked && hint ? (
        <div
          className="pointer-events-none absolute inset-0 z-10 flex items-start justify-center rounded-xl pt-10"
          aria-hidden
        >
          <p className="max-w-sm rounded-lg border border-zinc-200/80 bg-white/95 px-4 py-2.5 text-center text-sm font-medium text-zinc-700 shadow-lg ring-1 ring-zinc-950/5 backdrop-blur-sm dark:border-zinc-700 dark:bg-zinc-900/95 dark:text-zinc-200 dark:ring-white/10">
            {hint}
          </p>
        </div>
      ) : null}
    </div>
  );
}
