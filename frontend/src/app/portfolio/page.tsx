// Client component so auth cookies are included with browser fetch.
"use client";

import { useQuery } from "@tanstack/react-query";
import { AppShell } from "@/components/layout/AppShell";
import { apiClient } from "@/lib/api-client";
import {
  OpenPositionsTable,
  type PortfolioPositionRecord,
} from "@/components/portfolio/OpenPositionsTable";

type IciciApiResponse = {
  Status: number;
  Error?: string;
  Success?: {
    positions?: PortfolioPositionRecord[];
  };
};

export default function PortfolioPage() {
  const q = useQuery({
    queryKey: ["portfolio", "positions"],
    queryFn: async () => apiClient.get<IciciApiResponse>("/portfolio/data"),
  });

  const data = q.data;
  const positions = data?.Success?.positions ?? [];

  return (
    <AppShell contentWidth="wide">
      {q.isLoading ? (
        <div className="app-card p-4">Loading portfolio...</div>
      ) : q.error ? (
        <div className="app-alert-error">
          Unable to load portfolio:{" "}
          {q.error instanceof Error ? q.error.message : "Unknown error"}
        </div>
      ) : (
        <section className="app-card min-w-0 space-y-3 p-4">
          <header className="space-y-1">
            <div className="flex items-center justify-between">
              <h2 className="text-base font-semibold text-zinc-900 dark:text-zinc-100">
                Portfolio
              </h2>
              {data && data.Status !== 200 && (
                <span className="text-xs text-red-600 dark:text-red-400">
                  {data.Error || "Unable to load portfolio"}
                </span>
              )}
            </div>
            <p className="text-sm app-text-muted">
              Open positions in NFO/BFO options only
            </p>
          </header>
          <OpenPositionsTable positions={positions} />
        </section>
      )}
    </AppShell>
  );
}
