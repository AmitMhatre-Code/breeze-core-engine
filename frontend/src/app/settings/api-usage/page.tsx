"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { AppShell } from "@/components/layout/AppShell";
import { AsyncLabelSpan } from "@/components/ui/AsyncLabelSpan";
import { apiClient } from "@/lib/api-client";

type ApiUsageByApiItem = {
  usage_date: string;
  api_name: string;
  call_count: number;
};

type ApiUsageByRouteItem = {
  usage_date: string;
  route_id: string;
  call_count: number;
};

type ApiUsageData = {
  user_id: string;
  days: number;
  by_api: ApiUsageByApiItem[];
  by_route: ApiUsageByRouteItem[];
  rate_limit_pause_seconds?: number;
};

type ApiUsagePreferences = {
  user_id: string;
  rate_limit_pause_seconds: number;
};

type Mode = "api" | "route";

export default function ApiUsageSettingsPage() {
  const qc = useQueryClient();
  const [mode, setMode] = useState<Mode>("api");
  const [days, setDays] = useState<number>(30);
  const [pauseDraft, setPauseDraft] = useState<string>("");

  const q = useQuery({
    queryKey: ["settings", "api-usage", days],
    queryFn: () => apiClient.get<ApiUsageData>(`/api/settings/api-usage/data?days=${days}`),
  });

  const prefQ = useQuery({
    queryKey: ["settings", "api-usage-preferences"],
    queryFn: () =>
      apiClient.get<ApiUsagePreferences>("/api/settings/api-usage/preferences"),
  });

  useEffect(() => {
    if (prefQ.data) {
      setPauseDraft(String(prefQ.data.rate_limit_pause_seconds));
    }
  }, [prefQ.data?.rate_limit_pause_seconds]);

  const savePause = useMutation({
    mutationFn: (seconds: number) =>
      apiClient.post<ApiUsagePreferences>("/api/settings/api-usage/preferences", {
        rate_limit_pause_seconds: seconds,
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["settings", "api-usage-preferences"] });
      void qc.invalidateQueries({ queryKey: ["settings", "api-usage"] });
    },
  });

  const grouped = useMemo(() => {
    const source = mode === "api" ? q.data?.by_api ?? [] : q.data?.by_route ?? [];
    const out = new Map<string, Array<{ key: string; count: number }>>();
    for (const row of source) {
      const date = row.usage_date;
      const key = mode === "api" ? (row as ApiUsageByApiItem).api_name : (row as ApiUsageByRouteItem).route_id;
      const existing = out.get(date) ?? [];
      existing.push({ key, count: row.call_count });
      out.set(date, existing);
    }
    return Array.from(out.entries()).map(([date, rows]) => ({
      date,
      total: rows.reduce((acc, r) => acc + r.count, 0),
      rows,
    }));
  }, [mode, q.data]);

  return (
    <AppShell>
      <section className="app-card space-y-4 p-4">
        <Link href="/settings" className="app-link text-xs inline-block">
          Back to Settings
        </Link>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-xl app-text-heading">API Usage</h2>
          <label className="text-xs text-zinc-600 dark:text-zinc-400">
            Days
            <div className="relative ml-2 inline-block">
              <select
                value={days}
                onChange={(e) => setDays(Number(e.target.value))}
                className="appearance-none rounded-lg border border-zinc-300/80 bg-white/95 py-1.5 pl-3 pr-9 text-xs font-medium text-zinc-900 shadow-sm outline-none transition-all hover:border-zinc-400 focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:hover:border-zinc-600 dark:focus:border-blue-400 dark:focus:ring-blue-400/20"
              >
                <option value={7}>7</option>
                <option value={30}>30</option>
                <option value={60}>60</option>
                <option value={90}>90</option>
              </select>
              <svg
                aria-hidden="true"
                viewBox="0 0 20 20"
                fill="none"
                className="pointer-events-none absolute right-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-zinc-500 dark:text-zinc-400"
              >
                <path d="M6 8l4 4 4-4" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </div>
          </label>
        </div>

        <div className="rounded-lg border border-zinc-200/90 bg-zinc-50/80 p-4 dark:border-zinc-800 dark:bg-zinc-900/40">
          <h3 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
            Rate limit backoff
          </h3>
          <p className="mt-1 text-xs leading-relaxed text-zinc-600 dark:text-zinc-400">
            When ICICI returns HTTP 429 or 503 (rate throttling), the app waits this many seconds
            before retrying. For order place and cancel flows, a countdown is shown on screen. For
            Strategy Builder option-chain fetches, the pause happens on the server. No pacing is
            applied until a throttle response occurs.
          </p>
          <div className="mt-4">
            <label
              htmlFor="rate-limit-pause-seconds"
              className="mb-1.5 block text-xs font-medium text-zinc-700 dark:text-zinc-300"
            >
              Seconds to wait
            </label>
            <div className="flex flex-wrap items-center gap-2">
              <input
                id="rate-limit-pause-seconds"
                type="number"
                min={1}
                max={300}
                step={1}
                inputMode="numeric"
                className="min-h-[2.25rem] w-[6.5rem] rounded-lg border border-zinc-300/80 bg-white/95 px-3 py-2 text-sm font-medium tabular-nums text-zinc-900 shadow-sm outline-none transition-[border-color,box-shadow] [-moz-appearance:textfield] [appearance:textfield] hover:border-zinc-400 focus:border-sky-500 focus:ring-2 focus:ring-sky-500/25 disabled:cursor-not-allowed disabled:opacity-60 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:hover:border-zinc-600 dark:focus:border-sky-400 dark:focus:ring-sky-400/20 [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none"
                value={pauseDraft}
                onChange={(e) => setPauseDraft(e.target.value)}
                disabled={prefQ.isLoading || savePause.isPending}
              />
              <button
                type="button"
                className="app-btn-primary inline-flex min-h-[2.25rem] items-center rounded-lg px-4 py-2 text-sm font-medium shadow-sm transition-shadow hover:shadow-md disabled:shadow-none"
                disabled={
                  prefQ.isLoading ||
                  savePause.isPending ||
                  pauseDraft.trim() === ""
                }
                aria-busy={savePause.isPending}
                onClick={() => {
                  const n = parseInt(pauseDraft.trim(), 10);
                  if (!Number.isFinite(n) || n < 1 || n > 300) {
                    alert("Enter a whole number between 1 and 300.");
                    return;
                  }
                  savePause.mutate(n, {
                    onError: (e) =>
                      alert(e instanceof Error ? e.message : "Save failed"),
                    onSuccess: () => alert("Saved."),
                  });
                }}
              >
                <AsyncLabelSpan
                  busy={savePause.isPending}
                  idleLabel="Save"
                  busyLabel="Saving…"
                />
              </button>
            </div>
          </div>
          {prefQ.error ? (
            <p className="mt-2 text-xs text-red-600 dark:text-red-400">
              {prefQ.error instanceof Error ? prefQ.error.message : "Could not load preference"}
            </p>
          ) : null}
        </div>

        <div
          className="inline-flex items-center rounded-lg border border-zinc-200 bg-zinc-100/70 p-1 dark:border-zinc-800 dark:bg-zinc-900/60"
          role="tablist"
          aria-label="Usage view toggle"
        >
          <button
            type="button"
            onClick={() => setMode("api")}
            role="tab"
            aria-selected={mode === "api"}
            className={`rounded-md px-3 py-1.5 text-xs font-medium transition-all ${
              mode === "api"
                ? "bg-white text-zinc-900 shadow-sm dark:bg-zinc-800 dark:text-zinc-100"
                : "text-zinc-600 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100"
            }`}
          >
            API-wise
          </button>
          <button
            type="button"
            onClick={() => setMode("route")}
            role="tab"
            aria-selected={mode === "route"}
            className={`rounded-md px-3 py-1.5 text-xs font-medium transition-all ${
              mode === "route"
                ? "bg-white text-zinc-900 shadow-sm dark:bg-zinc-800 dark:text-zinc-100"
                : "text-zinc-600 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100"
            }`}
          >
            Route-wise
          </button>
        </div>

        {q.isLoading && (
          <div className="text-sm text-zinc-500 dark:text-zinc-400">Loading usage…</div>
        )}

        {q.error && (
          <div className="text-sm text-red-700 dark:text-red-300">
            {q.error instanceof Error ? q.error.message : "Unable to load usage"}
          </div>
        )}

        {!q.isLoading && !q.error && grouped.length === 0 && (
          <div className="text-sm text-zinc-600 dark:text-zinc-300">
            No Breeze calls recorded in the selected range.
          </div>
        )}

        <div className="space-y-3">
          {grouped.map((day) => (
            <div key={day.date} className="rounded-lg border border-zinc-200 p-3 dark:border-zinc-800">
              <div className="mb-2 flex items-center justify-between text-sm">
                <span className="font-medium text-zinc-900 dark:text-zinc-100">{day.date}</span>
                <span className="text-zinc-600 dark:text-zinc-400">Total: {day.total}</span>
              </div>
              <div className="overflow-x-auto">
                <table className="min-w-full text-xs">
                  <thead>
                    <tr className="text-left text-zinc-500 dark:text-zinc-400">
                      <th className="pb-2 pr-3">{mode === "api" ? "API" : "Route"}</th>
                      <th className="pb-2 text-right">Calls</th>
                    </tr>
                  </thead>
                  <tbody>
                    {day.rows.map((row) => (
                      <tr key={`${day.date}-${row.key}`} className="border-t border-zinc-100 dark:border-zinc-900">
                        <td className="py-2 pr-3 text-zinc-800 dark:text-zinc-200">{row.key}</td>
                        <td className="py-2 text-right text-zinc-900 dark:text-zinc-100">{row.count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ))}
        </div>
      </section>
    </AppShell>
  );
}
