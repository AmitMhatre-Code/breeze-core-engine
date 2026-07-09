"use client";

import { useMemo, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { SettingsScreenHeader } from "@/components/settings/SettingsScreenHeader";
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
  "w-24 rounded-t-[2px] border-0 border-b border-muted bg-background dark:bg-elevated px-2 py-1 text-right font-mono text-foreground transition hover:border-accent focus:border-accent-strong focus:bg-panel focus:outline-none";

function rowKey(r: LimitRow) {
  return `${r.short_name}\0${r.exchange_code}\0${r.segment_code ?? ""}`;
}

function GaugeIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M12 14v-3" />
      <path d="M4.6 19a9 9 0 1 1 14.8 0" />
      <path d="M12 5v0" />
    </svg>
  );
}

export function QuantityLimitsScreen() {
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

  return (
    <div>
      <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
        <SettingsScreenHeader
          icon={<GaugeIcon />}
          title="Quantity Limits"
          description="Per-symbol freeze quantity caps enforced before order placement."
        />
        <button
          type="button"
          disabled={m.isPending || merged.length === 0}
          onClick={() => m.mutate()}
          className="app-btn-primary shrink-0 rounded-lg px-3.5 py-2 text-xs"
        >
          Save all
        </button>
      </div>
      {q.isLoading ? (
        <div className="text-sm text-muted">Loading…</div>
      ) : q.error || !q.data ? (
        <div className="text-sm text-down">
          {q.error instanceof Error ? q.error.message : "Unable to load"}
        </div>
      ) : (
        <section className="app-card space-y-4 p-5">
          {q.data.message ? (
            <p className="rounded-[8px] border border-amber-accent/40 bg-amber-tint px-3 py-2 text-heading leading-relaxed text-amber-accent">
              {q.data.message}
            </p>
          ) : null}
          <div className="space-y-1.5 text-xs leading-relaxed text-muted">
            <p className="font-semibold text-foreground">
              Original source of the Freeze Quantity Limits:
            </p>
            <p>
              For BSE: Look for the file called Abridged_CO.ZIP (col MaxTradQty in file
              BSE_EQD_CONTRACT_abr_ddmmyyyy){" "}
              <a
                href="https://www.bseindia.com/members/index.aspx"
                target="_blank"
                rel="noreferrer"
                className="app-link"
              >
                here
              </a>
              .
            </p>
            <p>
              For NSE: Look for the file called NSE_FO_contract_ddmmyyyy.csv.gz (col MaxTadQty){" "}
              <a
                href="https://www.nseindia.com/all-reports-derivatives"
                target="_blank"
                rel="noreferrer"
                className="app-link"
              >
                here
              </a>
              .
            </p>
          </div>
          <div className="max-h-[70vh] overflow-auto rounded-[10px] border border-border">
            <table className="min-w-full text-left text-heading">
              <thead className="sticky top-0 bg-panel2 text-faint">
                <tr>
                  <th className="px-2.5 py-2 font-semibold uppercase tracking-wide">Symbol</th>
                  <th className="px-2.5 py-2 font-semibold uppercase tracking-wide">Exch</th>
                  <th className="px-2.5 py-2 font-semibold uppercase tracking-wide">Segment</th>
                  <th className="px-2.5 py-2 text-right font-semibold uppercase tracking-wide">
                    Qty limit
                  </th>
                </tr>
              </thead>
              <tbody>
                {merged.map((r) => {
                  const k = rowKey(r);
                  return (
                    <tr key={k} className="border-t border-border-soft text-foreground">
                      <td className="px-2.5 py-1.5">{r.short_name}</td>
                      <td className="px-2.5 py-1.5 font-mono">{r.exchange_code}</td>
                      <td className="px-2.5 py-1.5 font-mono">{r.segment_code || "—"}</td>
                      <td className="px-2.5 py-1.5 text-right">
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
      )}
    </div>
  );
}
