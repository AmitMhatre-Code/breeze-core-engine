"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AppShell } from "@/components/layout/AppShell";
import { AsyncLabelSpan } from "@/components/ui/AsyncLabelSpan";
import { apiClient } from "@/lib/api-client";

type ScripMasterData = {
  user_id: string;
  master_date?: string | null;
  master_age_days?: number | null;
  has_past_expiries: boolean;
  past_expiries_count: number;
  message?: string | null;
};

export default function ScripMasterSettingsPage() {
  const qc = useQueryClient();
  const [refreshProgress, setRefreshProgress] = useState(0);

  const q = useQuery({
    queryKey: ["settings", "scrip-master"],
    queryFn: () => apiClient.get<ScripMasterData>("/api/settings/scrip-master/data"),
  });

  const refreshMut = useMutation({
    mutationFn: () => apiClient.post("/api/settings/scrip-master/refresh", {}),
    onMutate: () => {
      setRefreshProgress(8);
    },
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["settings", "scrip-master"] }),
  });

  useEffect(() => {
    let tickTimer: ReturnType<typeof setInterval> | undefined;
    let doneTimer: ReturnType<typeof setTimeout> | undefined;

    if (refreshMut.isPending) {
      tickTimer = setInterval(() => {
        setRefreshProgress((prev) => {
          const step = Math.max(1, Math.round((95 - prev) / 9));
          return Math.min(95, prev + step);
        });
      }, 350);
    } else if (refreshMut.isSuccess) {
      doneTimer = setTimeout(() => {
        setRefreshProgress(100);
        setTimeout(() => {
          setRefreshProgress(0);
          refreshMut.reset();
        }, 900);
      }, 0);
    } else if (refreshMut.isError) {
      doneTimer = setTimeout(() => {
        setRefreshProgress(0);
      }, 0);
    }

    return () => {
      if (tickTimer) clearInterval(tickTimer);
      if (doneTimer) clearTimeout(doneTimer);
    };
  }, [refreshMut.isPending, refreshMut.isSuccess, refreshMut.isError, refreshMut]);

  const refreshStatusText = useMemo(() => {
    if (refreshMut.isPending) return "Refreshing scrip master...";
    if (refreshMut.isSuccess && refreshProgress > 0) return "Refresh complete";
    return "";
  }, [refreshMut.isPending, refreshMut.isSuccess, refreshProgress]);

  return (
    <AppShell>
      <section className="app-card space-y-4 p-4">
        <Link href="/settings" className="app-link text-xs inline-block">
          Back to Settings
        </Link>
        <h2 className="text-xl app-text-heading">Scrip Master</h2>

        {q.isLoading && <div className="text-sm text-zinc-500 dark:text-zinc-400">Loading…</div>}
        {q.error && (
          <div className="app-alert-error text-xs">
            {q.error instanceof Error ? q.error.message : "Unable to load scrip master details"}
          </div>
        )}

        {q.data && (
          <div className="space-y-4">
            <div className="space-y-2 text-xs text-zinc-700 dark:text-zinc-300">
              <p>
                Refresh information for all NSE &amp; BSE scrips (stock codes, expiries, lot sizes and more) from the
                Master Data file provided by ICICI{" "}
                <a
                  href="https://directlink.icicidirect.com/NewSecurityMaster/SecurityMaster.zip"
                  target="_blank"
                  rel="noreferrer"
                  className="app-link"
                >
                  here
                </a>
                .
              </p>
            </div>

            <div className="rounded-lg border border-zinc-200 p-3 text-xs dark:border-zinc-800">
              <div>
                Master date:{" "}
                <span className="font-medium text-zinc-900 dark:text-zinc-100">
                  {q.data.master_date ?? "—"}
                </span>
              </div>
              <div>
                Master age:{" "}
                <span className="font-medium text-zinc-900 dark:text-zinc-100">
                  {q.data.master_age_days ?? "—"} day{q.data.master_age_days === 1 ? "" : "s"}
                </span>
              </div>
            </div>

            {q.data.has_past_expiries && (
              <div className="rounded-lg border border-red-300 bg-red-50 px-3 py-2 text-xs text-red-800 dark:border-red-900/40 dark:bg-red-950/20 dark:text-red-300">
                Warning: scrip master contains {q.data.past_expiries_count} expiry value
                {q.data.past_expiries_count === 1 ? "" : "s"} in the past.
              </div>
            )}

            {q.data.message && <div className="text-xs text-amber-800 dark:text-amber-300">{q.data.message}</div>}

            <div className="pt-1">
              <button
                type="button"
                className="app-btn-outline"
                onClick={() => refreshMut.mutate()}
                disabled={refreshMut.isPending}
                aria-busy={refreshMut.isPending}
              >
                <AsyncLabelSpan
                  busy={refreshMut.isPending}
                  idleLabel="Refresh Scrip Master"
                  busyLabel="Refreshing..."
                />
              </button>
            </div>

            {refreshProgress > 0 && (
              <div className="space-y-1">
                <div className="h-2 w-full overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-800">
                  <div
                    className="h-full bg-sky-600 transition-all duration-300 dark:bg-sky-500"
                    style={{ width: `${refreshProgress}%` }}
                  />
                </div>
                <div className="text-xs text-zinc-500 dark:text-zinc-400">
                  {refreshStatusText} {refreshMut.isPending ? `${Math.round(refreshProgress)}%` : ""}
                </div>
              </div>
            )}
          </div>
        )}
      </section>
    </AppShell>
  );
}
