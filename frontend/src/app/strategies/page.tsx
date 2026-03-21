// Client component so auth cookies are included with browser fetch.
"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { AppShell } from "@/components/layout/AppShell";
import { apiClient } from "@/lib/api-client";

type UnderlyingRow = {
  stock_code: string;
  long_name?: string;
  expiry_dates?: string[];
};

type StockCodesResponse = {
  stock_codes: UnderlyingRow[];
};

type UncoveredShortsDataResponse = {
  options: unknown[];
};

export default function StrategiesPage() {
  const q = useQuery({
    queryKey: ["strategies", "lists"],
    queryFn: async () => {
      const [hedge, vertical, uncovered] = await Promise.all([
        apiClient.get<StockCodesResponse>("/hedge/data"),
        apiClient.get<StockCodesResponse>("/vertical-spread/data"),
        apiClient.get<UncoveredShortsDataResponse>("/uncovered-shorts/data"),
      ]);
      return { hedge, vertical, uncovered };
    },
  });

  const hedge = q.data?.hedge;
  const vertical = q.data?.vertical;
  const uncovered = q.data?.uncovered;

  const hedgeCount = hedge?.stock_codes.length ?? 0;
  const uncoveredCount = (uncovered?.options || []).length;
  const verticalCount = vertical?.stock_codes.length ?? 0;

  return (
    <AppShell>
      {q.isLoading ? (
        <div className="app-card p-4">Loading strategies...</div>
      ) : q.error ? (
        <div className="app-alert-error">
          Unable to load strategies:{" "}
          {q.error instanceof Error ? q.error.message : "Unknown error"}
        </div>
      ) : (
        <section className="app-card space-y-4 p-4">
          <header className="flex items-center justify-between">
            <h2 className="app-text-heading">Strategies workspace</h2>
          </header>
          <div className="app-card-muted p-3 text-sm text-zinc-700 dark:text-zinc-300">
            <div className="font-medium text-zinc-900 dark:text-zinc-100">
              Strategy Builder
            </div>
            <p className="mt-1 app-text-muted">
              Outlook-first payoff, margin, and multi-leg execution.
            </p>
            <Link
              href="/strategy-builder"
              className="app-link mt-3 inline-block text-xs"
            >
              Open Strategy Builder →
            </Link>
          </div>
          <div className="grid gap-3 md:grid-cols-3">
            <div className="app-card-muted p-3 text-sm text-zinc-700 dark:text-zinc-300">
              <div className="font-medium text-zinc-900 dark:text-zinc-100">
                Hedge
              </div>
              <div className="mt-1 app-text-muted">
                {hedgeCount} hedgeable symbols available.
              </div>
            </div>
            <div className="app-card-muted p-3 text-sm text-zinc-700 dark:text-zinc-300">
              <div className="font-medium text-zinc-900 dark:text-zinc-100">
                Uncovered shorts
              </div>
              <div className="mt-1 app-text-muted">
                {uncoveredCount} uncovered-short candidates fetched.
              </div>
            </div>
            <div className="app-card-muted p-3 text-sm text-zinc-700 dark:text-zinc-300">
              <div className="font-medium text-zinc-900 dark:text-zinc-100">
                Vertical spreads
              </div>
              <div className="mt-1 app-text-muted">
                {verticalCount} underlyings with spread data.
              </div>
            </div>
          </div>
        </section>
      )}
    </AppShell>
  );
}
