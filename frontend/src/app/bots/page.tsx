"use client";

import { AppShell } from "@/components/layout/AppShell";
import { BotCard } from "@/components/bots/BotCard";
import { BotRunLog } from "@/components/bots/BotRunLog";
import { useLicenseRestrictions } from "@/components/license/LicenseRestrictionProvider";
import { useBots } from "@/lib/use-bots";

export default function BotsPage() {
  const { data, isLoading, isError, error } = useBots();
  const { tradingReadOnly } = useLicenseRestrictions();

  // Sorted by the cross-bot priority, so the cards read in the order the bots actually
  // run — the one that sizes first sits first.
  const bots = [...(data ?? [])].sort((a, b) => a.priority - b.priority);

  return (
    <AppShell>
      <div className="space-y-4">
        <header>
          <h1 className="app-text-heading text-lg">Bots</h1>
          <p className="app-text-muted mt-1 max-w-prose text-sm">
            Automations that scan and trade on your behalf, within limits you set. Every bot
            can be run by hand; switching one to autonomous lets it trade without you.
          </p>
        </header>

        {isLoading && <p className="app-text-muted text-sm">Loading bots…</p>}
        {isError && (
          <p className="text-sm text-down">
            Could not load bots: {(error as Error)?.message ?? "unknown error"}
          </p>
        )}

        {/* Read-only mode disables arming and saving, but never hides the bots: a lapsed
            licence is a real, explainable state, not an outage. */}
        {tradingReadOnly && (
          <p className="app-card-muted p-3 text-xs">
            Read-only mode — bots cannot be enabled, reconfigured or run until your licence
            is active.
          </p>
        )}

        {/* Capped near 22rem: a square card stretched to half a wide viewport turns
            its own aspect ratio into dead space. */}
        <div className="grid gap-4 sm:grid-cols-[repeat(2,minmax(0,22rem))]">
          {bots.map((bot) => (
            <BotCard key={bot.id} bot={bot} readOnly={tradingReadOnly} />
          ))}
        </div>

        {/* Cross-bot and below the cards on purpose: the run log is the record of why
            nothing happened on a quiet day, which belongs to the page, not to one card. */}
        <BotRunLog />
      </div>
    </AppShell>
  );
}
