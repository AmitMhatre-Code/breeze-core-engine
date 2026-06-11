"use client";

import type { ReactNode } from "react";

export function SectionGate({
  locked,
  children,
}: {
  locked: boolean;
  hint?: string;
  children: ReactNode;
}) {
  if (locked) return null;
  return <>{children}</>;
}
