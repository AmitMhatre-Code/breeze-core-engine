import { useQuery } from "@tanstack/react-query";

import { apiClient } from "@/lib/api-client";

/** Settings -> Storage (docs/design-decisions.md #44). Served under `/api/settings/storage`. */

export type StorageStatus = {
  available: boolean;
  total_bytes: number | null;
  used_bytes: number | null;
  free_bytes: number | null;
  used_pct: number | null;
  threshold_pct: number;
  over_threshold: boolean;
  message: string | null;
};

export type StorageSeries = { name: string; from: string; to: string; days: number };

export type StorageElement = {
  key: string;
  label: string;
  group: string;
  bytes: number;
  approx: boolean;
  deletable: boolean;
  description: string;
  from: string | null;
  to: string | null;
  days: number | null;
  series: StorageSeries[];
  count: number | null;
  count_unit: string | null;
  guard: string | null;
};

export type StorageJob = {
  id: string;
  element: string;
  from: string;
  to: string;
  status: "running" | "completed" | "failed";
  stage: "deleting" | "compacting" | "done";
  running: boolean;
  deleted: number | null;
  unit: string | null;
  freed_bytes: number | null;
  compacted: boolean | null;
  message: string | null;
  error: string | null;
};

export type StorageInventory = {
  status: StorageStatus;
  elements: StorageElement[];
  generated_at: string;
  threshold_bounds: { min: number; max: number; default: number };
  job: StorageJob | null;
};

export const STORAGE_STATUS_KEY = ["settings", "storage", "status"] as const;
export const STORAGE_INVENTORY_KEY = ["settings", "storage", "inventory"] as const;
export const STORAGE_JOB_KEY = ["settings", "storage", "job"] as const;

/** Polled by the app-wide banner. The backend reads the volume live on every call, and the call
 * is one statvfs, so a minute is only about how soon the banner notices -- not about cost. */
export const STORAGE_STATUS_POLL_MS = 60_000;

export function useStorageStatus() {
  return useQuery({
    queryKey: STORAGE_STATUS_KEY,
    queryFn: ({ signal }) => apiClient.get<StorageStatus>("/api/settings/storage/status", signal),
    refetchInterval: STORAGE_STATUS_POLL_MS,
    staleTime: 30_000,
  });
}

export const fetchStorageInventory = (signal?: AbortSignal) =>
  apiClient.get<StorageInventory>("/api/settings/storage", signal);

export const fetchStorageJob = (signal?: AbortSignal) =>
  apiClient.get<{ job: StorageJob | null }>("/api/settings/storage/job", signal);

export const saveStorageThreshold = (threshold_pct: number) =>
  apiClient.put<StorageStatus>("/api/settings/storage/threshold", { threshold_pct });

export const startStorageDelete = (element: string, from_date: string, to_date: string) =>
  apiClient.post<{ job: StorageJob }>("/api/settings/storage/delete", { element, from_date, to_date });

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null || !Number.isFinite(bytes)) return "—";
  if (bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const exponent = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  const value = bytes / Math.pow(1024, exponent);
  return `${value >= 10 || exponent === 0 ? Math.round(value) : value.toFixed(1)} ${units[exponent]}`;
}

/** "12 Aug 2026" from an ISO date, without a timezone shift (the backend's dates are IST days). */
export function formatDay(iso: string | null | undefined): string {
  if (!iso) return "—";
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  if (!y || !m || !d) return iso;
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return `${d} ${months[m - 1]} ${y}`;
}

/** What an element's date coverage reads as: "1 Aug 2026 – 24 Sep 2026 · 38 days". */
export function describeCoverage(
  el: Pick<StorageElement, "from" | "to" | "days" | "count" | "count_unit" | "bytes">,
): string {
  const parts: string[] = [];
  if (el.from && el.to) {
    parts.push(el.from === el.to ? formatDay(el.from) : `${formatDay(el.from)} – ${formatDay(el.to)}`);
  }
  if (el.days) parts.push(`${el.days} day${el.days === 1 ? "" : "s"}`);
  if (el.count != null && el.count_unit) {
    const unit = el.count === 1 && el.count_unit.endsWith("s") ? el.count_unit.slice(0, -1) : el.count_unit;
    parts.push(`${el.count.toLocaleString("en-IN")} ${unit}`);
  }
  // A whole database has no date range to show; only something with no bytes is "Empty".
  if (parts.length) return parts.join(" · ");
  return el.bytes > 0 ? "" : "Empty";
}
