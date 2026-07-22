"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { HelpLink } from "@/components/help/HelpLink";
import { AsyncLabelSpan } from "@/components/ui/AsyncLabelSpan";
import { SettingsScreenHeader } from "@/components/settings/SettingsScreenHeader";
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

type ApiUsageByCategoryItem = {
  usage_date: string;
  category: string;
  call_count: number;
};

type ApiUsageData = {
  user_id: string;
  days: number;
  by_api: ApiUsageByApiItem[];
  by_route: ApiUsageByRouteItem[];
  by_category: ApiUsageByCategoryItem[];
  rate_limit_pause_seconds?: number;
};

type ApiUsagePreferences = {
  user_id: string;
  rate_limit_pause_seconds: number;
};

type Mode = "api" | "route";

const CATEGORY_LABELS: Record<string, string> = {
  order_placement: "Order placement",
  market_quotes: "Market quotes",
  portfolio: "Portfolio calls",
  other: "Other",
};
const CATEGORY_ORDER = ["order_placement", "market_quotes", "portfolio", "other"];

const DAY_OPTIONS = [7, 30, 60, 90];

function ActivityIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M22 12h-4l-3 9L9 3l-3 9H2" />
    </svg>
  );
}

function DaysDropdown({ value, onChange }: { value: number; onChange: (v: number) => void }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const fn = (e: MouseEvent) => {
      if (rootRef.current?.contains(e.target as Node)) return;
      setOpen(false);
    };
    document.addEventListener("mousedown", fn);
    return () => document.removeEventListener("mousedown", fn);
  }, [open]);

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="listbox"
        aria-expanded={open}
        className={[
          "flex min-w-[4.5rem] items-center justify-between gap-2 rounded-[9px] border px-3 py-1.5 font-mono text-xs text-foreground transition",
          open ? "border-accent bg-panel2 ring-2 ring-accent/25" : "border-border bg-panel2 hover:border-accent/50",
        ].join(" ")}
      >
        <span>{value}d</span>
        <svg
          width="12"
          height="12"
          viewBox="0 0 12 12"
          fill="none"
          className={`shrink-0 text-faint transition-transform ${open ? "-rotate-180" : ""}`}
          aria-hidden
        >
          <path d="M2.5 4.25L6 7.75l3.5-3.5" stroke="currentColor" strokeWidth="1.25" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>
      {open ? (
        <ul
          role="listbox"
          className="absolute right-0 top-[calc(100%+6px)] z-30 min-w-full overflow-hidden rounded-[10px] border border-border bg-elevated py-1 shadow-pop"
        >
          {DAY_OPTIONS.map((d) => (
            <li key={d}>
              <button
                type="button"
                role="option"
                aria-selected={d === value}
                onClick={() => {
                  onChange(d);
                  setOpen(false);
                }}
                className={[
                  "flex w-full items-center justify-between px-3.5 py-2 text-left font-mono text-xs",
                  d === value ? "bg-accent-tint font-semibold text-accent-strong" : "text-foreground hover:bg-panel2",
                ].join(" ")}
              >
                {d} days
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

export function ApiUsageScreen() {
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
    queryFn: () => apiClient.get<ApiUsagePreferences>("/api/settings/api-usage/preferences"),
  });

  useEffect(() => {
    if (prefQ.data) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- syncs local draft from server preference once it loads
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

  const categoryByDay = useMemo(() => {
    const out = new Map<string, Record<string, number>>();
    for (const row of q.data?.by_category ?? []) {
      const bucket = out.get(row.usage_date) ?? {};
      bucket[row.category] = (bucket[row.category] ?? 0) + row.call_count;
      out.set(row.usage_date, bucket);
    }
    return Array.from(out.entries())
      .sort(([a], [b]) => (a < b ? 1 : -1))
      .map(([date, counts]) => ({ date, counts }));
  }, [q.data?.by_category]);

  return (
    <div>
      <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
        <SettingsScreenHeader
          icon={<ActivityIcon />}
          title="API Usage"
          description="ICICI Breeze REST call volume and rate-limit pacing."
        />
        <div className="flex shrink-0 items-center gap-3">
          <HelpLink topicId="api-usage-limits" className="text-xs">
            Help
          </HelpLink>
          <DaysDropdown value={days} onChange={setDays} />
        </div>
      </div>

      <div className="space-y-4">
        <section className="app-card space-y-3 p-5">
          <h3 className="text-heading font-bold text-foreground">Rate limit backoff</h3>
          <p className="text-xs leading-relaxed text-muted">
            Minimum seconds between ICICI API calls (0–3 seconds). On HTTP 429 or 503 the app
            also applies exponential backoff (capped at 3 seconds) before retrying. Order place and
            cancel flows show a countdown on screen; all other ICICI traffic is paced globally on
            the server.
          </p>
          <div>
            <label
              htmlFor="rate-limit-pause-seconds"
              className="mb-1.5 block text-micro font-semibold uppercase tracking-[.06em] text-faint"
            >
              Seconds to wait
            </label>
            <div className="flex flex-wrap items-center gap-2">
              <input
                id="rate-limit-pause-seconds"
                type="number"
                min={0}
                max={3}
                step={0.5}
                inputMode="numeric"
                className="h-10 w-28 rounded-t-[3px] border-0 border-b border-muted bg-background dark:bg-elevated px-3 font-mono text-sm tabular-nums text-foreground outline-none transition hover:border-accent focus:border-accent-strong focus:bg-panel disabled:cursor-not-allowed disabled:opacity-60 [-moz-appearance:textfield] [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none"
                value={pauseDraft}
                onChange={(e) => setPauseDraft(e.target.value)}
                disabled={prefQ.isLoading || savePause.isPending}
              />
              <button
                type="button"
                className="app-btn-outline rounded-[9px] px-4 py-2 text-xs"
                disabled={prefQ.isLoading || savePause.isPending || pauseDraft.trim() === ""}
                aria-busy={savePause.isPending}
                onClick={() => {
                  const n = parseFloat(pauseDraft.trim());
                  if (!Number.isFinite(n) || n < 0 || n > 3) {
                    alert("Enter a number between 0 and 3.");
                    return;
                  }
                  savePause.mutate(n, {
                    onError: (e) => alert(e instanceof Error ? e.message : "Save failed"),
                    onSuccess: () => alert("Saved."),
                  });
                }}
              >
                <AsyncLabelSpan busy={savePause.isPending} idleLabel="Save" busyLabel="Saving…" />
              </button>
            </div>
          </div>
          {prefQ.error ? (
            <p className="text-xs text-down">
              {prefQ.error instanceof Error ? prefQ.error.message : "Could not load preference"}
            </p>
          ) : null}
        </section>

        {categoryByDay.length > 0 ? (
          <section className="app-card space-y-3 p-5">
            <h3 className="text-heading font-bold text-foreground">Calls by category</h3>
            <div className="space-y-2.5">
              {categoryByDay.map(({ date, counts }) => (
                <div key={date} className="flex flex-wrap items-center justify-between gap-3 border-t border-border-soft pt-2.5 first:border-t-0 first:pt-0">
                  <span className="font-mono text-xs text-muted">{date}</span>
                  <div className="flex flex-wrap gap-x-5 gap-y-1">
                    {CATEGORY_ORDER.filter((c) => counts[c]).map((c) => (
                      <span key={c} className="text-xs text-muted">
                        {CATEGORY_LABELS[c]}:{" "}
                        <strong className="font-mono font-semibold text-foreground">{counts[c]}</strong>
                      </span>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </section>
        ) : null}

        <section className="space-y-3">
          <div className={"inline-flex rounded-[9px] border border-border bg-panel2 p-[3px]"}>
            <button
              type="button"
              onClick={() => setMode("api")}
              role="tab"
              aria-selected={mode === "api"}
              className={[
                "rounded-[6px] px-3.5 py-1.5 font-mono text-table font-semibold transition",
                mode === "api" ? "bg-accent-strong text-accent-ink" : "text-muted hover:text-foreground",
              ].join(" ")}
            >
              API-wise
            </button>
            <button
              type="button"
              onClick={() => setMode("route")}
              role="tab"
              aria-selected={mode === "route"}
              className={[
                "rounded-[6px] px-3.5 py-1.5 font-mono text-table font-semibold transition",
                mode === "route" ? "bg-accent-strong text-accent-ink" : "text-muted hover:text-foreground",
              ].join(" ")}
            >
              Route-wise
            </button>
          </div>

          {q.isLoading ? <div className="text-sm text-muted">Loading usage…</div> : null}
          {q.error ? (
            <div className="text-sm text-down">
              {q.error instanceof Error ? q.error.message : "Unable to load usage"}
            </div>
          ) : null}
          {!q.isLoading && !q.error && grouped.length === 0 ? (
            <div className="text-sm text-muted">No Breeze calls recorded in the selected range.</div>
          ) : null}

          <div className="space-y-3">
            {grouped.map((day) => (
              <div key={day.date} className="app-card p-4">
                <div className="mb-2 flex items-center justify-between text-sm">
                  <span className="font-mono font-semibold text-foreground">{day.date}</span>
                  <span className="text-muted">
                    Total: <span className="font-mono">{day.total}</span>
                  </span>
                </div>
                <div className="overflow-x-auto">
                  <table className="min-w-full text-xs">
                    <thead>
                      <tr className="text-left text-faint">
                        <th className="pb-2 pr-3 font-semibold uppercase tracking-wide">
                          {mode === "api" ? "API" : "Route"}
                        </th>
                        <th className="pb-2 text-right font-semibold uppercase tracking-wide">Calls</th>
                      </tr>
                    </thead>
                    <tbody>
                      {day.rows.map((row) => (
                        <tr key={`${day.date}-${row.key}`} className="border-t border-border-soft">
                          <td className="py-2 pr-3 font-mono text-foreground">{row.key}</td>
                          <td className="py-2 text-right font-mono text-foreground">{row.count}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}
