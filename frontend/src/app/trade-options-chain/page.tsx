"use client";

import { AppShell } from "@/components/layout/AppShell";
import { OptionChainPlaceSection } from "@/components/order/OptionChainPlaceSection";

export default function TradeOptionsChainPage() {
  return (
    <AppShell contentWidth="wide">
      <div className="space-y-5">
        <header className="space-y-1">
          <h1 className="app-text-title">Trade Options Chain</h1>
          <p className="max-w-2xl text-sm leading-relaxed text-zinc-600 dark:text-zinc-400">
            Fetch the full chain, select an expiry to Buy or Sell
          </p>
        </header>
        <OptionChainPlaceSection />
      </div>
    </AppShell>
  );
}
