"use client";

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { SettingsScreenHeader } from "@/components/settings/SettingsScreenHeader";
import { AsyncLabelSpan } from "@/components/ui/AsyncLabelSpan";
import { Modal } from "@/components/ui/Modal";
import {
  STORAGE_INVENTORY_KEY,
  STORAGE_JOB_KEY,
  STORAGE_STATUS_KEY,
  describeCoverage,
  fetchStorageInventory,
  fetchStorageJob,
  formatBytes,
  formatDay,
  saveStorageThreshold,
  startStorageDelete,
  type StorageElement,
  type StorageJob,
  type StorageStatus,
} from "@/lib/settings/storage";
import { sb } from "@/lib/strategy-builder/ui";

function DiskIcon() {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.9"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M22 12H2" />
      <path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z" />
      <path d="M6 16h.01M10 16h.01" />
    </svg>
  );
}

function TrashIcon() {
  return (
    <svg
      width="15"
      height="15"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.9"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M3 6h18" />
      <path d="M8 6V4h8v2" />
      <path d="M19 6l-1 14H6L5 6" />
      <path d="M10 11v6M14 11v6" />
    </svg>
  );
}

const GROUP_ORDER = ["Backtest history", "Backtest results", "Logs", "Kept by the app"];

const GROUP_NOTES: Record<string, string> = {
  "Kept by the app": "Listed so the figures add up. These can't be deleted here.",
};

export function StorageScreen() {
  const qc = useQueryClient();
  const inventoryQ = useQuery({
    queryKey: STORAGE_INVENTORY_KEY,
    queryFn: ({ signal }) => fetchStorageInventory(signal),
  });
  const inventory = inventoryQ.data;

  const jobQ = useQuery({
    queryKey: STORAGE_JOB_KEY,
    queryFn: ({ signal }) => fetchStorageJob(signal),
    refetchInterval: (q) => (q.state.data?.job?.running ? 1500 : false),
  });
  const job = jobQ.data?.job ?? inventory?.job ?? null;
  const jobRunning = Boolean(job?.running);

  // When a delete finishes, what it freed shows up in both the volume and the rows.
  const [watching, setWatching] = useState<string | null>(null);
  useEffect(() => {
    if (watching && job && job.id === watching && !job.running) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- one-shot: stop watching the finished job
      setWatching(null);
      void qc.invalidateQueries({ queryKey: STORAGE_INVENTORY_KEY });
      void qc.invalidateQueries({ queryKey: STORAGE_STATUS_KEY });
    }
  }, [watching, job, qc]);

  const [target, setTarget] = useState<StorageElement | null>(null);

  const groups = useMemo(() => {
    const byGroup = new Map<string, StorageElement[]>();
    for (const el of inventory?.elements ?? []) {
      byGroup.set(el.group, [...(byGroup.get(el.group) ?? []), el]);
    }
    return [...byGroup.entries()].sort(
      ([a], [b]) => GROUP_ORDER.indexOf(a) - GROUP_ORDER.indexOf(b),
    );
  }, [inventory?.elements]);

  return (
    <div>
      <SettingsScreenHeader
        icon={<DiskIcon />}
        title="Storage"
        description="How full this deployment's data volume is, what is using it, and deleting data you no longer need."
      />

      <div className="space-y-4">
        {inventoryQ.isError ? (
          <p className="text-xs text-down">
            Could not read storage. {(inventoryQ.error as Error).message}
          </p>
        ) : null}

        <VolumeCard
          status={inventory?.status}
          bounds={inventory?.threshold_bounds}
          loading={inventoryQ.isPending}
          onSaved={() => {
            void qc.invalidateQueries({ queryKey: STORAGE_INVENTORY_KEY });
            void qc.invalidateQueries({ queryKey: STORAGE_STATUS_KEY });
          }}
        />

        {job ? <JobPanel job={job} elements={inventory?.elements ?? []} /> : null}

        {inventoryQ.isPending ? (
          <p className="app-text-muted text-sm">Measuring… a large backtest cache can take a few seconds.</p>
        ) : null}

        {groups.map(([group, items]) => (
          <section key={group} className="app-card overflow-hidden">
            <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-border px-4 py-3">
              <h3 className="text-heading font-bold text-foreground">{group}</h3>
              <span className="font-mono text-xs tabular-nums text-muted">
                {formatBytes(items.reduce((sum, el) => sum + el.bytes, 0))}
              </span>
            </div>
            {GROUP_NOTES[group] ? (
              <p className="border-b border-border px-4 py-2 text-xs text-muted">{GROUP_NOTES[group]}</p>
            ) : null}
            <ul className="divide-y divide-border">
              {items.map((el) => (
                <ElementRow
                  key={el.key}
                  el={el}
                  disabled={jobRunning}
                  onDelete={() => setTarget(el)}
                />
              ))}
            </ul>
          </section>
        ))}

        {inventory ? (
          <p className="text-xs leading-relaxed text-muted">
            Sizes marked ≈ share out the backtest cache file by row count, so they are estimates;
            the volume figures and whole-file sizes are exact. Deleting from the backtest cache
            compacts it afterwards, which can take a minute on a large cache.
          </p>
        ) : null}
      </div>

      <DeleteDialog
        el={target}
        onClose={() => setTarget(null)}
        onStarted={(jobId) => {
          setTarget(null);
          setWatching(jobId);
          void qc.invalidateQueries({ queryKey: STORAGE_JOB_KEY });
        }}
      />
    </div>
  );
}

function VolumeCard({
  status,
  bounds,
  loading,
  onSaved,
}: {
  status: StorageStatus | undefined;
  bounds: { min: number; max: number; default: number } | undefined;
  loading: boolean;
  onSaved: () => void;
}) {
  const [draft, setDraft] = useState("");
  useEffect(() => {
    if (status) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- syncs local draft from server setting once it loads
      setDraft(String(status.threshold_pct));
    }
  }, [status?.threshold_pct]); // eslint-disable-line react-hooks/exhaustive-deps

  const save = useMutation({
    mutationFn: (pct: number) => saveStorageThreshold(pct),
    onSuccess: onSaved,
  });

  const pct = status?.used_pct ?? 0;
  const over = Boolean(status?.over_threshold);
  const min = bounds?.min ?? 50;
  const max = bounds?.max ?? 98;

  return (
    <section className="app-card space-y-4 p-5">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-heading font-bold text-foreground">Data volume</h3>
        {status?.available ? (
          <span className="font-mono text-xs tabular-nums text-muted">
            {formatBytes(status.used_bytes)} used · {formatBytes(status.free_bytes)} free ·{" "}
            {formatBytes(status.total_bytes)} total
          </span>
        ) : null}
      </div>

      {loading ? (
        <p className="app-text-muted text-sm">Checking…</p>
      ) : status && !status.available ? (
        <p className="text-xs text-down">The data volume could not be read on this deployment.</p>
      ) : status ? (
        <div className="space-y-1.5">
          <div
            className="relative h-3 overflow-hidden rounded-full bg-panel2"
            role="meter"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={pct}
            aria-label="Data volume used"
          >
            <div
              className={["h-full rounded-full", over ? "bg-amber-500" : "bg-accent-strong"].join(" ")}
              style={{ width: `${Math.min(100, pct)}%` }}
            />
            <div
              aria-hidden
              className="absolute inset-y-0 w-0.5 bg-foreground/70"
              style={{ left: `${status.threshold_pct}%` }}
              title={`Threshold ${status.threshold_pct}%`}
            />
          </div>
          <p className={["text-xs", over ? "font-semibold text-amber-700 dark:text-amber-300" : "text-muted"].join(" ")}>
            {pct.toFixed(1)}% used
            {over
              ? ` — past the ${status.threshold_pct}% threshold. Backtests that download or write data are paused.`
              : ` · threshold ${status.threshold_pct}%`}
          </p>
        </div>
      ) : null}

      <div>
        <label
          htmlFor="storage-threshold-pct"
          className="mb-1.5 block text-micro font-semibold uppercase tracking-[.06em] text-faint"
        >
          Threshold (% used)
        </label>
        <div className="flex flex-wrap items-center gap-2">
          <input
            id="storage-threshold-pct"
            type="number"
            min={min}
            max={max}
            step={1}
            inputMode="numeric"
            className="h-10 w-28 rounded-t-[3px] border-0 border-b border-muted bg-background dark:bg-elevated px-3 font-mono text-sm tabular-nums text-foreground outline-none transition hover:border-accent focus:border-accent-strong focus:bg-panel disabled:cursor-not-allowed disabled:opacity-60 [-moz-appearance:textfield] [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            disabled={loading || save.isPending}
          />
          <button
            type="button"
            className="app-btn-outline rounded-[9px] px-4 py-2 text-xs"
            disabled={loading || save.isPending || draft.trim() === ""}
            aria-busy={save.isPending}
            onClick={() => {
              const n = Number(draft.trim());
              if (!Number.isInteger(n) || n < min || n > max) {
                alert(`Enter a whole number between ${min} and ${max}.`);
                return;
              }
              save.mutate(n, {
                onError: (e) => alert(e instanceof Error ? e.message : "Save failed"),
              });
            }}
          >
            <AsyncLabelSpan busy={save.isPending} idleLabel="Save" busyLabel="Saving…" />
          </button>
        </div>
        <p className="mt-1.5 text-xs leading-relaxed text-muted">
          At or past this point every page shows a banner asking to free up space, new backtests are
          refused, and a running backtest stops downloading or writing data. Keep some room: compacting
          the backtest cache after a delete needs free space about the size of the cache.
        </p>
      </div>
    </section>
  );
}

function ElementRow({
  el,
  disabled,
  onDelete,
}: {
  el: StorageElement;
  disabled: boolean;
  onDelete: () => void;
}) {
  return (
    <li className="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-start sm:justify-between">
      <div className="min-w-0 space-y-1">
        <div className="flex flex-wrap items-baseline gap-x-2">
          <span className="text-sm font-semibold text-foreground">{el.label}</span>
          <span className="text-xs text-muted">{describeCoverage(el)}</span>
        </div>
        {el.series.length > 1 ? (
          <ul className="space-y-0.5">
            {el.series.map((s) => (
              <li key={s.name} className="text-xs text-muted">
                <span className="font-mono text-foreground">{s.name}</span>{" "}
                {formatDay(s.from)} – {formatDay(s.to)} · {s.days} day{s.days === 1 ? "" : "s"}
              </li>
            ))}
          </ul>
        ) : null}
        <p className="text-xs leading-relaxed text-muted">{el.description}</p>
        {el.guard ? <p className="text-xs leading-relaxed text-faint">{el.guard}</p> : null}
      </div>
      <div className="flex shrink-0 items-center gap-3 sm:flex-col sm:items-end sm:gap-1.5">
        <span className="font-mono text-sm tabular-nums text-foreground">
          {el.approx && el.bytes > 0 ? "≈ " : ""}
          {formatBytes(el.bytes)}
        </span>
        {el.deletable ? (
          <button
            type="button"
            className="app-btn-outline rounded-[9px] p-1.5 text-muted hover:border-down hover:bg-down-tint hover:text-down-on-tint"
            disabled={disabled || !el.from}
            aria-label={`Delete ${el.label}…`}
            title={!el.from ? "Nothing to delete" : disabled ? "A cleanup is running" : `Delete ${el.label}…`}
            onClick={onDelete}
          >
            <TrashIcon />
          </button>
        ) : null}
      </div>
    </li>
  );
}

function DeleteDialog({
  el,
  onClose,
  onStarted,
}: {
  el: StorageElement | null;
  onClose: () => void;
  onStarted: (jobId: string) => void;
}) {
  const [range, setRange] = useState({ from: "", to: "" });
  useEffect(() => {
    if (el) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- seeds the range from the row that opened the dialog
      setRange({ from: el.from?.slice(0, 10) ?? "", to: el.to?.slice(0, 10) ?? "" });
    }
  }, [el]);

  const start = useMutation({
    mutationFn: () => startStorageDelete(el!.key, range.from, range.to),
    onSuccess: (res) => onStarted(res.job.id),
  });

  const invalid = !range.from || !range.to || range.from > range.to;

  return (
    <Modal
      open={el !== null}
      onClose={onClose}
      role="alertdialog"
      titleId="storage-delete-title"
      descriptionId="storage-delete-body"
      pending={start.isPending}
      panelClassName={`${sb.modalPanel} !max-w-[min(96vw,30rem)] mx-auto`}
    >
      <h3 id="storage-delete-title" className="app-text-title">
        Delete {el?.label.toLowerCase()}?
      </h3>
      <div id="storage-delete-body" className="mt-2 space-y-3 text-sm leading-relaxed text-muted">
        <p>
          Stored now: {el ? describeCoverage(el) : null}. Everything dated inside the range below is
          deleted permanently.
        </p>
        {el?.guard ? <p className="text-xs">{el.guard}</p> : null}
        <div className="flex flex-wrap items-center gap-3 text-xs">
          <label className="flex items-center gap-1.5">
            <span className="text-muted">From</span>
            <input
              type="date"
              className="app-input py-1 text-xs"
              value={range.from}
              onChange={(e) => setRange((r) => ({ ...r, from: e.target.value }))}
            />
          </label>
          <label className="flex items-center gap-1.5">
            <span className="text-muted">To</span>
            <input
              type="date"
              className="app-input py-1 text-xs"
              value={range.to}
              onChange={(e) => setRange((r) => ({ ...r, to: e.target.value }))}
            />
          </label>
        </div>
        {range.from && range.to && range.from > range.to ? (
          <p className="text-xs text-down">The end date is before the start date.</p>
        ) : null}
        {start.isError ? (
          <p className="text-xs text-down">{(start.error as Error).message}</p>
        ) : null}
      </div>
      <div className="mt-5 flex flex-wrap justify-end gap-2">
        <button type="button" className="app-btn-outline rounded-[9px] px-4 py-2 text-xs" onClick={onClose}>
          Cancel
        </button>
        <button
          type="button"
          className="app-btn-danger rounded-[9px] px-4 py-2 text-xs"
          disabled={invalid || start.isPending}
          aria-busy={start.isPending}
          onClick={() => start.mutate()}
        >
          <AsyncLabelSpan busy={start.isPending} idleLabel="Delete" busyLabel="Starting…" />
        </button>
      </div>
    </Modal>
  );
}

function JobPanel({
  job,
  elements,
}: {
  job: StorageJob;
  elements: StorageElement[];
}) {
  const label = elements.find((e) => e.key === job.element)?.label ?? job.element;
  const range = job.from === job.to ? formatDay(job.from) : `${formatDay(job.from)} – ${formatDay(job.to)}`;
  const tone =
    job.status === "failed"
      ? "border-down/40 bg-down-tint text-down-on-tint"
      : job.running
        ? "border-border bg-panel2 text-foreground"
        : "border-up/40 bg-up-tint text-foreground";
  return (
    <div role="status" className={`rounded-[10px] border px-4 py-3 text-xs leading-relaxed ${tone}`}>
      <p className="font-semibold">
        {label}, {range}:{" "}
        {job.running
          ? job.stage === "compacting"
            ? "compacting the backtest cache…"
            : "deleting…"
          : job.status === "failed"
            ? "delete failed."
            : "done."}
      </p>
      {job.running ? null : <p className="mt-0.5">{job.error ?? job.message}</p>}
    </div>
  );
}

