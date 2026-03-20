"use client";

import { useQuery } from "@tanstack/react-query";
import { AppShell } from "@/components/layout/AppShell";
import { apiClient } from "@/lib/api-client";

export default function PerformancePage() {
  const p = useQuery({
    queryKey: ["performance", "data"],
    queryFn: () => apiClient.get<Record<string, unknown>>("/performance/data"),
  });

  return (
    <AppShell>
      {p.isLoading ? (
        <div className="text-sm text-zinc-500 dark:text-zinc-400">Loading performance…</div>
      ) : p.error ? (
        <div className="text-sm text-red-700 dark:text-red-300">
          {p.error instanceof Error ? p.error.message : "Unable to load"}
        </div>
      ) : (
        <section className="app-card p-4">
          <h2 className="app-text-heading">Performance</h2>
          <pre className="app-pre mt-4 max-h-[60vh]">
            {JSON.stringify(p.data, null, 2)}
          </pre>
        </section>
      )}
    </AppShell>
  );
}
