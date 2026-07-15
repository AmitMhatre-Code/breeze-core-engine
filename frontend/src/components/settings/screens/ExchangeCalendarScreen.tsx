"use client";

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { HelpLink } from "@/components/help/HelpLink";
import { DatePicker } from "@/components/ui/DatePicker";
import { Modal } from "@/components/ui/Modal";
import { SettingsScreenHeader } from "@/components/settings/SettingsScreenHeader";
import {
  fetchExchangeCalendar,
  fetchExchangeCalendarSyncPreview,
  saveExchangeCalendar,
  syncExchangeCalendarFromConsole,
  type ExchangeCalendarHolidayItem,
} from "@/lib/settings/exchange-calendar";
import { formatIsoDateDdMmmYyyy } from "@/lib/format-iso-date";

function CalendarIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <rect x="3" y="4" width="18" height="18" rx="2" />
      <path d="M16 2v4M8 2v4M3 10h18" />
    </svg>
  );
}

export function ExchangeCalendarScreen() {
  const qc = useQueryClient();
  const [openHour, setOpenHour] = useState(9);
  const [openMinute, setOpenMinute] = useState(15);
  const [closeHour, setCloseHour] = useState(15);
  const [closeMinute, setCloseMinute] = useState(30);
  const [holidays, setHolidays] = useState<ExchangeCalendarHolidayItem[]>([]);
  const [newDate, setNewDate] = useState("");
  const [newName, setNewName] = useState("");
  const [syncError, setSyncError] = useState<string | null>(null);
  const [showSyncConfirm, setShowSyncConfirm] = useState(false);
  const [syncPreviewMsg, setSyncPreviewMsg] = useState<string | null>(null);

  const calQ = useQuery({
    queryKey: ["settings", "exchange-calendar"],
    queryFn: fetchExchangeCalendar,
  });

  useEffect(() => {
    const d = calQ.data;
    if (!d) return;
    setOpenHour(d.working_hours.open_hour);
    setOpenMinute(d.working_hours.open_minute);
    setCloseHour(d.working_hours.close_hour);
    setCloseMinute(d.working_hours.close_minute);
    setHolidays(
      d.holidays_list?.length
        ? [...d.holidays_list]
        : Object.entries(d.holidays || {}).map(([date, name]) => ({ date, name })),
    );
  }, [calQ.data]);

  const saveMut = useMutation({
    mutationFn: () =>
      saveExchangeCalendar({
        working_hours: {
          open_hour: openHour,
          open_minute: openMinute,
          close_hour: closeHour,
          close_minute: closeMinute,
        },
        holidays,
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["settings", "exchange-calendar"] });
    },
  });

  const syncMut = useMutation({
    mutationFn: (confirmOverride: boolean) => syncExchangeCalendarFromConsole(confirmOverride),
    onSuccess: () => {
      setShowSyncConfirm(false);
      setSyncPreviewMsg(null);
      setSyncError(null);
      void qc.invalidateQueries({ queryKey: ["settings", "exchange-calendar"] });
    },
    onError: (e) => {
      setSyncError(e instanceof Error ? e.message : "Sync failed");
    },
  });

  const sortedHolidays = useMemo(
    () => [...holidays].sort((a, b) => a.date.localeCompare(b.date)),
    [holidays],
  );

  const addHoliday = () => {
    const d = newDate.trim();
    const n = newName.trim();
    if (!d || !n) return;
    if (holidays.some((h) => h.date === d)) return;
    setHolidays((prev) => [...prev, { date: d, name: n }]);
    setNewDate("");
    setNewName("");
  };

  const removeHoliday = (date: string) => {
    setHolidays((prev) => prev.filter((h) => h.date !== date));
  };

  const startSync = async () => {
    setSyncError(null);
    try {
      const preview = await fetchExchangeCalendarSyncPreview();
      if (!preview.portal_configured) {
        setSyncError(preview.message ?? "Breeze Console is not configured for this deployment.");
        return;
      }
      if (preview.would_overwrite_local) {
        setSyncPreviewMsg(
          preview.message ??
            "The local holiday calendar and working hours will be replaced by Breeze Console Admin Settings.",
        );
        setShowSyncConfirm(true);
        return;
      }
      syncMut.mutate(false);
    } catch (e) {
      setSyncError(e instanceof Error ? e.message : "Could not preview sync");
    }
  };

  const data = calQ.data;
  const portalConfigured = data?.portal_configured ?? false;

  return (
    <div>
      <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
        <SettingsScreenHeader
          icon={<CalendarIcon />}
          title="Exchange Calendar"
          description="Holidays and regular session hours used for after-hours order parking. Weekends are always non-trading."
        />
        <HelpLink topicId="exchange-calendar" className="shrink-0 text-xs">
          Help
        </HelpLink>
      </div>

      <section className="app-card max-w-[640px] space-y-5 p-5">
        {data ? (
          <p className="text-heading text-faint">
            Source: {data.source === "console_sync" ? "Breeze Console Admin Settings" : "Local"}
            {data.console_updated_at ? ` · last synced ${data.console_updated_at}` : ""}
          </p>
        ) : null}

        {saveMut.isError ? (
          <p className="app-alert-error text-xs">
            {saveMut.error instanceof Error ? saveMut.error.message : "Save failed"}
          </p>
        ) : null}
        {syncError ? <p className="app-alert-error text-xs">{syncError}</p> : null}

        <div className="space-y-3 rounded-[10px] border border-border px-4 py-3.5">
          <h3 className="text-heading font-bold text-foreground">Regular session (IST)</h3>
          <div className="flex flex-wrap gap-5">
            <div>
              <label className="mb-1 block text-micro text-muted">Market open</label>
              <div className="flex items-center gap-1.5">
                <input
                  type="number"
                  min={0}
                  max={23}
                  className="app-input w-[52px] text-center"
                  value={openHour}
                  onChange={(e) => setOpenHour(Number(e.target.value))}
                />
                <span className="text-muted">:</span>
                <input
                  type="number"
                  min={0}
                  max={59}
                  className="app-input w-[52px] text-center"
                  value={openMinute}
                  onChange={(e) => setOpenMinute(Number(e.target.value))}
                />
              </div>
            </div>
            <div>
              <label className="mb-1 block text-micro text-muted">Market close</label>
              <div className="flex items-center gap-1.5">
                <input
                  type="number"
                  min={0}
                  max={23}
                  className="app-input w-[52px] text-center"
                  value={closeHour}
                  onChange={(e) => setCloseHour(Number(e.target.value))}
                />
                <span className="text-muted">:</span>
                <input
                  type="number"
                  min={0}
                  max={59}
                  className="app-input w-[52px] text-center"
                  value={closeMinute}
                  onChange={(e) => setCloseMinute(Number(e.target.value))}
                />
              </div>
            </div>
          </div>
        </div>

        <div className="space-y-4 rounded-[10px] border border-border px-4 py-3.5">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="text-heading font-bold text-foreground">Exchange holidays</h3>
            {sortedHolidays.length > 0 ? (
              <span className="text-heading text-muted">
                <span className="font-mono tabular-nums">{sortedHolidays.length}</span> configured
              </span>
            ) : null}
          </div>

          <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
            <label className="block min-w-0 flex-1 space-y-1.5">
              <span className="text-heading text-muted">Date</span>
              <DatePicker value={newDate} onChange={setNewDate} />
            </label>
            <label className="block min-w-0 flex-[1.5] space-y-1.5">
              <span className="text-heading text-muted">Holiday name</span>
              <input
                type="text"
                className="app-input h-10 w-full"
                placeholder="e.g. Republic Day"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") addHoliday();
                }}
              />
            </label>
            <button type="button" className="app-btn-outline h-10 shrink-0 px-4 sm:self-end" onClick={addHoliday}>
              Add
            </button>
          </div>

          {sortedHolidays.length === 0 ? (
            <p className="rounded-[9px] border border-dashed border-border px-4 py-8 text-center text-sm text-faint">
              No holidays configured.
            </p>
          ) : (
            <ul className="divide-y divide-border-soft">
              {sortedHolidays.map((h) => (
                <li key={h.date} className="group flex items-center justify-between gap-3 py-2.5 text-sm">
                  <span className="min-w-0">
                    <span className="font-mono tabular-nums text-foreground">
                      {formatIsoDateDdMmmYyyy(h.date)}
                    </span>
                    <span className="mx-2 text-faint">—</span>
                    <span className="text-muted">{h.name}</span>
                  </span>
                  <button
                    type="button"
                    className="app-btn-outline shrink-0 text-xs"
                    onClick={() => removeHoliday(h.date)}
                  >
                    Remove
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="flex flex-wrap gap-2">
          <button type="button" className="app-btn-primary" disabled={saveMut.isPending} onClick={() => saveMut.mutate()}>
            {saveMut.isPending ? "Saving…" : "Save local calendar"}
          </button>
        </div>

        <div className="space-y-3 rounded-[10px] border border-accent/30 bg-accent-tint p-4">
          <h3 className="text-heading font-bold text-foreground">Breeze Console Admin Settings</h3>
          <p className="text-xs text-muted">
            Replace the shared exchange calendar with the operator-maintained calendar from breeze-ui.com Console.
          </p>
          {!portalConfigured ? (
            <p className="text-xs text-amber-accent">
              Sync is unavailable — this deployment is not linked to Breeze Console (PORTAL_API_BASE_URL).
            </p>
          ) : (
            <button type="button" className="app-btn-outline" disabled={syncMut.isPending} onClick={() => void startSync()}>
              {syncMut.isPending ? "Syncing…" : "Sync from Breeze Console"}
            </button>
          )}
        </div>
      </section>

      {showSyncConfirm ? (
        <Modal
          open={showSyncConfirm}
          onClose={() => setShowSyncConfirm(false)}
          titleId="exchange-calendar-sync-title"
          panelClassName="app-card max-w-md space-y-4 p-4"
        >
          <h3 id="exchange-calendar-sync-title" className="font-semibold text-foreground">
            Overwrite local calendar?
          </h3>
          <p className="text-sm text-muted">
            {syncPreviewMsg ??
              "The local holiday calendar and working hours will be replaced by Breeze Console Admin Settings."}
          </p>
          <div className="flex justify-end gap-2">
            <button type="button" className="app-btn-outline" onClick={() => setShowSyncConfirm(false)}>
              Cancel
            </button>
            <button
              type="button"
              className="app-btn-primary"
              disabled={syncMut.isPending}
              onClick={() => syncMut.mutate(true)}
            >
              Continue
            </button>
          </div>
        </Modal>
      ) : null}
    </div>
  );
}
