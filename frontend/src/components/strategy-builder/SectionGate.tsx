"use client";

import type { ReactNode } from "react";

export function SectionGate({
  locked,
  dimWhenLocked = false,
  children,
}: {
  locked: boolean;
  hint?: string;
  /** When locked, show children dimmed instead of hiding (e.g. while chain builds). */
  dimWhenLocked?: boolean;
  children: ReactNode;
}) {
  if (locked && !dimWhenLocked) return null;
  if (locked && dimWhenLocked) {
    return (
      <div
        className="pointer-events-none select-none opacity-45"
        aria-hidden
      >
        {children}
      </div>
    );
  }
  return <>{children}</>;
}
