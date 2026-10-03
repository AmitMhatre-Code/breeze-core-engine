"use client";

import { AppShell } from "@/components/layout/AppShell";
import { CondorsPage } from "@/components/condor/CondorsPage";

export default function IronCondors() {
  return (
    <AppShell contentWidth="wide">
      <CondorsPage />
    </AppShell>
  );
}
