"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { AppShell } from "@/components/layout/AppShell";
import { DatePicker } from "@/components/ui/DatePicker";
import { Modal } from "@/components/ui/Modal";
import {
  fetchExchangeCalendar,
  fetchExchangeCalendarSyncPreview,
  saveExchangeCalendar,
  syncExchangeCalendarFromConsole,
  type ExchangeCalendarHolidayItem,
} from "@/lib/settings/exchange-calendar";
import { formatIsoDateDdMmmYyyy } from "@/lib/format-iso-date";

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

export default function ExchangeCalendarSettingsPage() {
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
    mutationFn: (confirmOverride: boolean) =>
      syncExchangeCalendarFromConsole(confirmOverride),
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
        setSyncError(
          preview.message ?? "Breeze Console is not configured for this deployment.",
        );
        return;
      }
      if (preview.would_overwrite_local) {
        setSyncPreviewMsg(
          preview.message ??
            "Your local holiday calendar and working hours will be replaced by Breeze Console Admin Settings.",
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
    <AppShell>
      <section className="app-card mx-auto max-w-3xl space-y-4 p-4">
        <Link href="/settings" className="app-link text-xs inline-block">
          Back to Settings
        </Link>
        <header className="space-y-1">
          <h2 className="text-xl app-text-heading">Exchange Calendar</h2>
          <p className="app-text-muted text-sm">
            Holidays and regular session hours used for after-hours order parking.
            Weekends are always non-trading.
          </p>
          {data ? (
            <p className="text-xs text-zinc-500">
              Source: {data.source === "console_sync" ? "Breeze Console Admin Settings" : "Local"}
              {data.console_updated_at ? ` · last synced ${data.console_updated_at}` : ""}
            </p>
          ) : null}
        </header>

        {saveMut.isError ? (
          <p className="text-sm text-red-600">
            {saveMut.error instanceof Error ? saveMut.error.message : "Save failed"}
          </p>
        ) : null}
        {syncError ? <p className="text-sm text-red-600">{syncError}</p> : null}

        <div className="app-card-muted space-y-3 p-4">
          <h3 className="text-sm font-semibold">Regular session (IST)</h3>
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="block space-y-1 text-sm">
              <span className="app-text-muted">Market open</span>
              <div className="flex gap-2">
                <input
                  type="number"
                  min={0}
                  max={23}
                  className="app-input w-20"
                  value={openHour}
                  onChange={(e) => setOpenHour(Number(e.target.value))}
                />
                <span className="self-center">:</span>
                <input
                  type="number"
                  min={0}
                  max={59}
                  className="app-input w-20"
                  value={openMinute}
                  onChange={(e) => setOpenMinute(Number(e.target.value))}
                />
              </div>
            </label>
            <label className="block space-y-1 text-sm">
              <span className="app-text-muted">Market close</span>
              <div className="flex gap-2">
                <input
                  type="number"
                  min={0}
                  max={23}
                  className="app-input w-20"
                  value={closeHour}
                  onChange={(e) => setCloseHour(Number(e.target.value))}
                />
                <span className="self-center">:</span>
                <input
                  type="number"
                  min={0}
                  max={59}
                  className="app-input w-20"
                  value={closeMinute}
                  onChange={(e) => setCloseMinute(Number(e.target.value))}
                />
              </div>
            </label>
          </div>
        </div>

        <div className="app-card-muted space-y-4 p-4 sm:p-5">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="app-text-heading">Exchange holidays</h3>
            {sortedHolidays.length > 0 ? (
              <span className="app-text-muted tabular-nums">
                {sortedHolidays.length} configured
              </span>
            ) : null}
          </div>

          <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
            <label className="block min-w-0 flex-1 space-y-1.5">
              <span className="app-text-muted">Date</span>
              <DatePicker value={newDate} onChange={setNewDate} />
            </label>
            <label className="block min-w-0 flex-[1.5] space-y-1.5">
              <span className="app-text-muted">Holiday name</span>
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
            <button
              type="button"
              className="app-btn-outline h-10 shrink-0 px-4 sm:self-end"
              onClick={addHoliday}
            >
              Add
            </button>
          </div>

          {sortedHolidays.length === 0 ? (
            <p className="rounded-md border border-dashed border-zinc-200 px-4 py-8 text-center text-sm text-zinc-500 dark:border-zinc-700 dark:text-zinc-400">
              No holidays configured.
            </p>
          ) : (
            <ul className="divide-y divide-zinc-200 dark:divide-zinc-800">
              {sortedHolidays.map((h) => (
                <li
                  key={h.date}
                  className="group flex items-center justify-between gap-3 py-2.5 text-sm"
                >
                  <span className="min-w-0">
                    <span className="font-mono tabular-nums text-zinc-800 dark:text-zinc-200">
                      {formatIsoDateDdMmmYyyy(h.date)}
                    </span>
                    <span className="mx-2 text-zinc-400">—</span>
                    <span className="text-zinc-700 dark:text-zinc-300">{h.name}</span>
                  </span>
                  <button
                    type="button"
                    className="app-btn-outline shrink-0 text-xs opacity-100 transition group-hover:opacity-100 sm:opacity-70"
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
          <button
            type="button"
            className="app-btn-primary"
            disabled={saveMut.isPending}
            onClick={() => saveMut.mutate()}
          >
            {saveMut.isPending ? "Saving…" : "Save local calendar"}
          </button>
        </div>

        <div className="app-card-muted space-y-3 border border-blue-200/60 p-4 dark:border-blue-900/40">
          <h3 className="text-sm font-semibold">Breeze Console Admin Settings</h3>
          <p className="app-text-muted text-sm">
            Replace your local calendar with the operator-maintained calendar from
            breeze-ui.com Console.
          </p>
          {!portalConfigured ? (
            <p className="text-sm text-amber-700 dark:text-amber-300">
              Sync is unavailable — this deployment is not linked to Breeze Console
              (PORTAL_API_BASE_URL).
            </p>
          ) : (
            <button
              type="button"
              className="app-btn-outline"
              disabled={syncMut.isPending}
              onClick={() => void startSync()}
            >
              {syncMut.isPending ? "Syncing…" : "Sync from Breeze Console"}
            </button>
          )}
        </div>

        {showSyncConfirm ? (
          <Modal
            open={showSyncConfirm}
            onClose={() => setShowSyncConfirm(false)}
            titleId="exchange-calendar-sync-title"
            panelClassName="app-card max-w-md space-y-4 p-4"
          >
            <h3 id="exchange-calendar-sync-title" className="font-semibold">
              Overwrite local calendar?
            </h3>
            <p className="text-sm text-zinc-600 dark:text-zinc-400">
              {syncPreviewMsg ??
                "Your local holiday calendar and working hours will be replaced by Breeze Console Admin Settings."}
            </p>
            <div className="flex justify-end gap-2">
              <button
                type="button"
                className="app-btn-outline"
                onClick={() => setShowSyncConfirm(false)}
              >
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
      </section>
    </AppShell>
  );
}
