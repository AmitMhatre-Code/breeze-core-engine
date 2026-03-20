"use client";

import { useQuery } from "@tanstack/react-query";
import { AppShell } from "@/components/layout/AppShell";
import { apiClient } from "@/lib/api-client";

export default function AdminPage() {
  const q = useQuery({
    queryKey: ["admin", "data"],
    queryFn: () =>
      apiClient.get<Record<string, unknown>>("/admin/data"),
  });

  return (
    <AppShell>
      {q.isLoading ? (
        <div className="text-sm text-zinc-500 dark:text-zinc-400">Loading…</div>
      ) : q.error ? (
        <div className="app-card border-red-200 p-4 text-sm text-red-800 dark:border-red-900/50 dark:text-red-300">
          {q.error instanceof Error ? q.error.message : "Forbidden or unavailable"}
          <p className="mt-2 app-text-muted">
            Admin routes require <code className="text-zinc-600 dark:text-zinc-400">is_admin=1</code> in{" "}
            <code className="text-zinc-600 dark:text-zinc-400">user_account</code>.
          </p>
        </div>
      ) : (
        <section className="app-card p-4">
          <h2 className="app-text-heading">Admin</h2>
          <p className="mt-1 app-text-muted">
            Run tests via{" "}
            <code className="text-zinc-600 dark:text-zinc-400">POST /admin/tests/run</code> (not wired in this UI).
          </p>
          <pre className="app-pre mt-4 max-h-[50vh]">
            {JSON.stringify(q.data, null, 2)}
          </pre>
        </section>
      )}
    </AppShell>
  );
}
