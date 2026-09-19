"use client";

import { AppShell } from "@/components/layout/AppShell";
import { SignalsPage } from "@/components/signals/SignalsPage";

export default function Signals() {
  return (
    <AppShell contentWidth="wide">
      <SignalsPage />
    </AppShell>
  );
}
