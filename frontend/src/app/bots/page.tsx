"use client";

import { AppShell } from "@/components/layout/AppShell";
import { BotCard } from "@/components/bots/BotCard";
import { BotRunLog } from "@/components/bots/BotRunLog";
import { ProposalPanel } from "@/components/bots/ProposalPanel";
import { useLicenseRestrictions } from "@/components/license/LicenseRestrictionProvider";
import { useBots } from "@/lib/use-bots";

export default function BotsPage() {
  const { data, isLoading, isError, error } = useBots();
  const { tradingReadOnly } = useLicenseRestrictions();

  return (
    <AppShell>
      <div className="space-y-4">
        <header>
          <h1 className="app-text-heading text-lg">Bots</h1>
          <p className="app-text-muted mt-1 max-w-prose text-sm">
            Automations that scan and trade on your behalf, within limits you set. A bot
            does nothing until you enable it.
          </p>
        </header>

        {isLoading && <p className="app-text-muted text-sm">Loading bots…</p>}
        {isError && (
          <p className="text-sm text-rose-600 dark:text-rose-400">
            Could not load bots: {(error as Error)?.message ?? "unknown error"}
          </p>
        )}

        {/* Read-only mode disables arming and saving, but never hides the bots: a lapsed
            licence is a real, explainable state, not an outage. */}
        {tradingReadOnly && (
          <p className="app-card-muted p-3 text-xs">
            Read-only mode — bots cannot be enabled or reconfigured until your licence is
            active.
          </p>
        )}

        {data?.map((bot) => (
          <BotCard key={bot.id} bot={bot} readOnly={tradingReadOnly} />
        ))}

        <ProposalPanel readOnly={tradingReadOnly} />

        <BotRunLog />
      </div>
    </AppShell>
  );
}
