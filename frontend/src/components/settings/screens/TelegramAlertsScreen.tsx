"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { SettingsScreenHeader } from "@/components/settings/SettingsScreenHeader";
import { AsyncLabelSpan } from "@/components/ui/AsyncLabelSpan";
import { Checkbox } from "@/components/ui/Checkbox";
import { TelegramLinkPanel } from "@/components/telegram/TelegramLinkPanel";
import {
  disconnectTelegram,
  fetchTelegramStatus,
  setTelegramAlertsEnabled,
  TELEGRAM_STATUS_QUERY_KEY,
} from "@/lib/telegram/telegram-alerts";

function BellIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9" />
      <path d="M10.3 21a1.94 1.94 0 0 0 3.4 0" />
    </svg>
  );
}

export function TelegramAlertsScreen() {
  const qc = useQueryClient();

  const statusQ = useQuery({
    queryKey: TELEGRAM_STATUS_QUERY_KEY,
    queryFn: fetchTelegramStatus,
  });

  const alertsToggle = useMutation({
    mutationFn: setTelegramAlertsEnabled,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: TELEGRAM_STATUS_QUERY_KEY });
    },
  });

  // "Change linked account" (State C) is driven entirely by unlinking: once
  // disconnected, `status.connected` flips false and TelegramLinkPanel below
  // renders its normal unregistered QR flow — no separate UI branch needed.
  const unlinkMut = useMutation({
    mutationFn: disconnectTelegram,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: TELEGRAM_STATUS_QUERY_KEY });
    },
  });

  const status = statusQ.data;

  return (
    <div>
      <SettingsScreenHeader
        icon={<BellIcon />}
        title="Telegram Alerts"
        description="Get an instant Telegram message whenever a Profit Booking / Stop Loss rule fires."
      />

      <div className="space-y-4">
        {statusQ.error ? (
          <p className="text-xs text-down">
            {statusQ.error instanceof Error ? statusQ.error.message : "Could not load Telegram status"}
          </p>
        ) : null}

        <section className="app-card space-y-3 p-5">
          {status?.connected ? (
            <>
              <div className="flex items-center justify-between rounded-md border border-border-soft bg-panel2 px-3.5 py-2.5 text-sm">
                <span className="flex items-center gap-2 text-muted">
                  <span className="relative flex size-2 shrink-0">
                    <span className="absolute inline-flex size-full animate-ping rounded-full bg-up opacity-75" />
                    <span className="relative inline-flex size-2 rounded-full bg-up" />
                  </span>
                  Status: Connected
                </span>
                <span className="font-mono text-xs text-foreground">@{status.telegram_username}</span>
              </div>

              <label className="flex items-center justify-between gap-3 text-sm">
                <span className="text-muted">Alerts enabled</span>
                <Checkbox
                  checked={status.alerts_enabled}
                  disabled={alertsToggle.isPending}
                  onChange={(checked) => alertsToggle.mutate(checked)}
                  aria-label="Alerts enabled"
                />
              </label>

              <div className="flex flex-wrap items-center gap-2 pt-1">
                <button
                  type="button"
                  className="app-btn-outline rounded-sm px-3 py-1.5 text-xs"
                  disabled={unlinkMut.isPending}
                  onClick={() => {
                    if (!confirm("Disconnect this Telegram account and generate a new link for a different one?")) return;
                    unlinkMut.mutate();
                  }}
                >
                  Change linked account
                </button>
                <button
                  type="button"
                  className="rounded-sm bg-down-btn px-3 py-1.5 text-xs font-semibold text-down-ink transition hover:brightness-[1.06] disabled:opacity-50"
                  disabled={unlinkMut.isPending}
                  onClick={() => {
                    if (!confirm("Deregister this Telegram account? You will stop receiving alerts.")) return;
                    unlinkMut.mutate();
                  }}
                >
                  <AsyncLabelSpan busy={unlinkMut.isPending} idleLabel="Deregister" busyLabel="Deregistering…" />
                </button>
              </div>
            </>
          ) : (
            <TelegramLinkPanel onConnected={() => qc.invalidateQueries({ queryKey: TELEGRAM_STATUS_QUERY_KEY })} />
          )}
        </section>
      </div>
    </div>
  );
}
