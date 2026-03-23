"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { AppShell } from "@/components/layout/AppShell";
import { apiClient } from "@/lib/api-client";

type LimitRow = {
  segment_code: string;
  instrument_name: string;
  short_name: string;
  exchange_code: string;
  qty_limit: number;
  lot_size: number | null;
};

type LimitsData = {
  limits: LimitRow[];
  message?: string | null;
  user_id: string;
};

const inputCls =
  "w-20 rounded border border-zinc-300 bg-white px-1 py-0.5 text-right text-zinc-900 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100";

function rowKey(r: LimitRow) {
  return `${r.short_name}\0${r.exchange_code}\0${r.segment_code ?? ""}`;
}

export default function QuantityLimitsPage() {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["settings", "quantity-limits"],
    queryFn: () => apiClient.get<LimitsData>("/api/settings/quantity-limits/data"),
  });

  const [draft, setDraft] = useState<Record<string, number>>({});

  const merged = useMemo(() => {
    const rows = q.data?.limits ?? [];
    return rows.map((r) => {
      const k = rowKey(r);
      const qv = draft[k];
      return { ...r, qty_limit: qv !== undefined ? qv : r.qty_limit };
    });
  }, [q.data?.limits, draft]);

  const m = useMutation({
    mutationFn: () =>
      apiClient.post("/api/settings/quantity-limits", {
        rows: merged.map((r) => ({
          short_name: r.short_name,
          exchange_code: r.exchange_code,
          segment_code: r.segment_code,
          qty_limit: r.qty_limit,
        })),
      }),
    onSuccess: () => {
      setDraft({});
      void qc.invalidateQueries({ queryKey: ["settings", "quantity-limits"] });
      alert("Quantity limits updated.");
    },
    onError: (e) => alert(e instanceof Error ? e.message : "Save failed"),
  });

  if (q.isLoading) {
    return (
      <AppShell>
        <div className="text-sm text-zinc-500 dark:text-zinc-400">Loading…</div>
      </AppShell>
    );
  }

  if (q.error || !q.data) {
    return (
      <AppShell>
        <div className="text-sm text-red-700 dark:text-red-300">
          {q.error instanceof Error ? q.error.message : "Unable to load"}
        </div>
      </AppShell>
    );
  }

  return (
    <AppShell>
      <section className="app-card space-y-4 p-4">
        <header className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="app-text-heading">Quantity limits</h2>
          <button
            type="button"
            disabled={m.isPending || merged.length === 0}
            onClick={() => m.mutate()}
            className="app-btn-primary px-3 py-1.5 text-xs font-medium"
          >
            Save all
          </button>
        </header>
        <Link href="/settings" className="app-link mt-1 inline-block text-xs">
          Back to Settings
        </Link>
        {q.data.message && (
          <p className="text-xs text-amber-800 dark:text-amber-300">{q.data.message}</p>
        )}
        <div className="max-h-[70vh] overflow-auto rounded-xl border border-zinc-200 dark:border-zinc-800">
          <table className="min-w-full text-left text-[11px]">
            <thead className="sticky top-0 bg-zinc-100 text-zinc-600 dark:bg-zinc-900 dark:text-zinc-500">
              <tr>
                <th className="px-2 py-2">Symbol</th>
                <th className="px-2 py-2">Exch</th>
                <th className="px-2 py-2">Segment</th>
                <th className="px-2 py-2 text-right">Qty limit</th>
              </tr>
            </thead>
            <tbody>
              {merged.map((r) => {
                const k = rowKey(r);
                return (
                  <tr
                    key={k}
                    className="border-t border-zinc-200 text-zinc-800 dark:border-zinc-800/80 dark:text-zinc-300"
                  >
                    <td className="px-2 py-1">{r.short_name}</td>
                    <td className="px-2 py-1">{r.exchange_code}</td>
                    <td className="px-2 py-1">{r.segment_code || "—"}</td>
                    <td className="px-2 py-1 text-right">
                      <input
                        type="number"
                        className={inputCls}
                        value={r.qty_limit}
                        onChange={(e) =>
                          setDraft((d) => ({
                            ...d,
                            [k]: parseInt(e.target.value, 10) || 0,
                          }))
                        }
                      />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>
    </AppShell>
  );
}
