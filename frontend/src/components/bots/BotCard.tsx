"use client";

import { useState } from "react";
import { BotConfigFields } from "@/components/bots/BotConfigFields";
import { BOT_META, useUpdateBot, type Bot } from "@/lib/use-bots";

export function BotCard({ bot, readOnly }: { bot: Bot; readOnly: boolean }) {
  const meta = BOT_META[bot.bot_type];
  const update = useUpdateBot();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<Record<string, unknown>>(bot.config);
  const [error, setError] = useState<string | null>(null);

  // The server normalizes config on save (filling defaults, coercing types), so the draft
  // must resync from the response rather than keep what the user typed. Done by adjusting
  // state during render rather than in an effect, and keyed on the serialized *values*:
  // react-query hands back a fresh object on every refetch, so an identity comparison
  // would wipe a half-finished edit each time the query revalidated.
  const serverConfig = JSON.stringify(bot.config);
  const [syncedConfig, setSyncedConfig] = useState(serverConfig);
  if (serverConfig !== syncedConfig) {
    setSyncedConfig(serverConfig);
    setDraft(bot.config);
  }

  const dirty = JSON.stringify(draft) !== serverConfig;

  async function submit(patch: { enabled?: boolean; config?: Record<string, unknown> }) {
    setError(null);
    try {
      await update.mutateAsync({ botType: bot.bot_type, ...patch });
    } catch (e) {
      setError((e as Error)?.message ?? "Could not save.");
    }
  }

  return (
    <section className="app-card p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="app-text-heading">{meta.title}</h2>
          <p className="app-text-muted mt-1 max-w-prose text-xs">{meta.blurb}</p>
          <p className="mt-2 text-[11px] uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
            {meta.mode}
          </p>
        </div>

        <div className="flex shrink-0 items-center gap-3">
          <span
            className={`rounded px-2 py-0.5 text-[11px] font-medium uppercase tracking-wide ${
              bot.enabled
                ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
                : "bg-zinc-500/10 text-zinc-600 dark:text-zinc-400"
            }`}
          >
            {bot.enabled ? "Enabled" : "Disabled"}
          </span>
          <button
            type="button"
            className={bot.enabled ? "app-btn-outline" : "app-btn-primary"}
            disabled={readOnly || update.isPending}
            onClick={() => void submit({ enabled: !bot.enabled })}
          >
            {bot.enabled ? "Disable" : "Enable"}
          </button>
        </div>
      </div>

      <button
        type="button"
        className="app-link mt-3 text-xs"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        {open ? "Hide settings" : "Settings"}
      </button>

      {open && (
        <div className="mt-4 border-t border-zinc-200 pt-4 dark:border-zinc-800">
          <BotConfigFields
            botType={bot.bot_type}
            config={draft}
            onChange={(patch) => setDraft((d) => ({ ...d, ...patch }))}
          />
          <div className="mt-4 flex items-center gap-3">
            <button
              type="button"
              className="app-btn-primary"
              disabled={readOnly || !dirty || update.isPending}
              onClick={() => void submit({ config: draft })}
            >
              {update.isPending ? "Saving…" : "Save settings"}
            </button>
            {dirty && !readOnly && (
              <button
                type="button"
                className="app-btn-secondary"
                onClick={() => setDraft(bot.config)}
                disabled={update.isPending}
              >
                Discard
              </button>
            )}
          </div>
        </div>
      )}

      {error && <p className="mt-3 text-sm text-rose-600 dark:text-rose-400">{error}</p>}
    </section>
  );
}
