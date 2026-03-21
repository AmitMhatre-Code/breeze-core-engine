"use client";

import { AppShell } from "@/components/layout/AppShell";
import { OptionChainPlaceSection } from "@/components/order/OptionChainPlaceSection";

export default function TradeOptionsChainPage() {
  return (
    <AppShell contentWidth="wide">
      <div className="space-y-5">
        <header className="space-y-1">
          <h1 className="text-xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
            Trade Options Chain
          </h1>
          <p className="max-w-2xl text-sm leading-relaxed text-zinc-600 dark:text-zinc-400">
            Fetch the full chain, select an expiry, then buy or sell any strike
          </p>
        </header>
        <OptionChainPlaceSection />
      </div>
    </AppShell>
  );
}
