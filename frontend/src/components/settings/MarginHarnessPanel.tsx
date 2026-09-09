"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AsyncLabelSpan } from "@/components/ui/AsyncLabelSpan";
import { Checkbox } from "@/components/ui/Checkbox";
import { formatApiDateTime } from "@/lib/format-iso-date";
import {
  downloadMarginHarnessRun,
  fetchMarginHarnessRuns,
  startMarginHarnessRun,
  type MarginHarnessRun,
} from "@/lib/settings/margin-harness";

const SPAN_METHOD_LABELS: Record<string, string> = {
  scan_only: "Scan only",
  scan_minus_nov_bs: "Scan − NOV (Black-Scholes)",
  scan_minus_nov_file: "Scan − NOV (exchange premium)",
  som_floor_minus_nov_file: "max(scan, SOM) − NOV",
};

const ELM_METHOD_LABELS: Record<string, string> = {
  none: "No ELM",
  portfolio_flat_index_only: "Portfolio: flat 2%, index only",
  strategy_builder_tiered: "Strategy Builder: tiered",
  exchange_prescribed: "Exchange: 2% / 3.5%",
  exchange_prescribed_no_expiry_waiver: "Exchange, no expiry waiver",
};

function bestLabel(run: MarginHarnessRun): string {
  const best = run.summary?.best_combination;
  if (!best) return "—";
  const span = SPAN_METHOD_LABELS[best.span_method] ?? best.span_method;
  const elm = ELM_METHOD_LABELS[best.elm_method] ?? best.elm_method;
  return `${span} + ${elm}`;
}

export function MarginHarnessPanel() {
  const queryClient = useQueryClient();
  const [includeOpenPositions, setIncludeOpenPositions] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [downloadingId, setDownloadingId] = useState<string | null>(null);

  const q = useQuery({
    queryKey: ["margin-harness-runs"],
    queryFn: fetchMarginHarnessRuns,
    // Only while a run is in flight: a paced 50-call run takes a minute or two.
    refetchInterval: (query) => (query.state.data?.running ? 3000 : false),
  });

  const runMut = useMutation({
    mutationFn: () => startMarginHarnessRun(includeOpenPositions),
    onSuccess: () => {
      setError(null);
      void queryClient.invalidateQueries({ queryKey: ["margin-harness-runs"] });
    },
    onError: (e) => setError(e instanceof Error ? e.message : "Could not start the run"),
  });

  const state = q.data;
  const isLive = (state?.broker_mode ?? "").toLowerCase() === "live";
  const running = Boolean(state?.running);
  const runs = state?.runs ?? [];

  async function onDownload(runId: string) {
    setDownloadingId(runId);
    setError(null);
    try {
      await downloadMarginHarnessRun(runId);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Download failed");
    } finally {
      setDownloadingId(null);
    }
  }

  return (
    <div className="space-y-3 rounded-[10px] border border-border px-4 py-3.5">
      <div>
        <h3 className="text-heading font-bold text-foreground">Margin comparison harness</h3>
        <p className="mt-1 text-table leading-relaxed text-muted">
          Prices a fixed set of structures with ICICI&apos;s margin calculator, then with every
          SPAN and exposure-margin method this app contains, and ranks them by how close each
          lands. Needs live broker calls, so it only works on the production instance whose IP
          is registered with ICICI. A run costs roughly one broker call per case — about 1% of
          the daily quota — and is classified advisory, so it sheds before order placement does.
        </p>
      </div>

      {!isLive && state ? (
        <div className="app-alert-error text-xs">
          Broker mode is <strong>{state.broker_mode}</strong>. The harness needs{" "}
          <strong>live</strong> — margin figures in any other mode are fabricated.
        </div>
      ) : null}

      {error ? <div className="app-alert-error text-xs">{error}</div> : null}

      <div className="flex flex-wrap items-center gap-3 border-t border-border-soft pt-3">
        <label className="flex items-center gap-2 text-xs text-muted">
          <Checkbox
            checked={includeOpenPositions}
            disabled={running || runMut.isPending}
            onChange={setIncludeOpenPositions}
            aria-label="Include current open positions"
          />
          Include current open positions
        </label>
        <button
          type="button"
          className="app-btn-primary"
          disabled={running || runMut.isPending || !isLive}
          onClick={() => runMut.mutate()}
        >
          <AsyncLabelSpan
            busy={running || runMut.isPending}
            busyLabel={running ? "Run in progress…" : "Starting…"}
            idleLabel="Run comparison"
          />
        </button>
        {running ? (
          <span className="text-xs text-muted">
            Pricing case by case — broker calls are serialized, so this takes a minute or two.
          </span>
        ) : null}
      </div>

      {runs.length === 0 ? (
        <p className="text-xs text-muted">
          No comparison runs yet. Run this on an ordinary trading day and again on an expiry
          day — the difference between the two is what separates the exposure-margin models.
        </p>
      ) : (
        <div className="app-table-wrap">
          <table className="min-w-full text-left text-table">
            <thead className="app-table-head">
              <tr>
                <th className="px-2.5 py-2 font-semibold whitespace-nowrap">Started</th>
                <th className="px-2.5 py-2 font-semibold whitespace-nowrap">Status</th>
                <th className="px-2.5 py-2 text-right font-semibold whitespace-nowrap">Cases</th>
                <th className="px-2.5 py-2 text-right font-semibold whitespace-nowrap">Calls</th>
                <th className="px-2.5 py-2 font-semibold whitespace-nowrap">Closest method</th>
                <th className="px-2.5 py-2 text-right font-semibold whitespace-nowrap">Mean err</th>
                <th className="px-2.5 py-2 font-semibold whitespace-nowrap">JSON</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => (
                <tr key={run.id} className="app-table-row">
                  <td className="px-2.5 py-2 whitespace-nowrap text-foreground">
                    {formatApiDateTime(run.started_at)}
                  </td>
                  <td className="px-2.5 py-2 whitespace-nowrap">
                    <span
                      className={
                        run.status === "completed"
                          ? "text-foreground"
                          : run.status === "running"
                            ? "text-accent-strong"
                            : "text-down"
                      }
                    >
                      {run.status}
                    </span>
                    {run.error ? (
                      <span className="ml-1 text-muted" title={run.error}>
                        ⓘ
                      </span>
                    ) : null}
                  </td>
                  <td className="px-2.5 py-2 text-right font-mono tabular-nums whitespace-nowrap text-foreground">
                    {run.priced_count}/{run.case_count}
                    {run.failed_count > 0 ? (
                      <span className="text-down"> (+{run.failed_count} failed)</span>
                    ) : null}
                  </td>
                  <td className="px-2.5 py-2 text-right font-mono tabular-nums whitespace-nowrap text-muted">
                    {run.broker_calls}
                  </td>
                  <td className="px-2.5 py-2 text-foreground">{bestLabel(run)}</td>
                  <td className="px-2.5 py-2 text-right font-mono tabular-nums whitespace-nowrap text-foreground">
                    {run.summary?.best_combination
                      ? `${run.summary.best_combination.mean_abs_pct.toFixed(2)}%`
                      : "—"}
                  </td>
                  <td className="px-2.5 py-2 whitespace-nowrap">
                    {run.status === "completed" ? (
                      <button
                        type="button"
                        className="text-xs font-medium text-accent-strong hover:underline disabled:opacity-50"
                        disabled={downloadingId === run.id}
                        onClick={() => void onDownload(run.id)}
                      >
                        {downloadingId === run.id ? "Preparing…" : "Download"}
                      </button>
                    ) : (
                      <span className="text-xs text-muted">—</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {runs[0]?.summary?.marginism?.compared_cases ? (
        <p className="text-xs text-muted">
          Independent cross-check (marginism {runs[0].summary.marginism.library_version ?? "?"}):{" "}
          {runs[0].summary.marginism.comparable_cases > 0 ? (
            <>
              our SPAN engine and marginism agree on{" "}
              <strong>
                {runs[0].summary.marginism.agreeing_cases}/
                {runs[0].summary.marginism.comparable_cases}
              </strong>{" "}
              cases priced off the exact same snapshot
              {runs[0].summary.marginism.max_abs_pct_vs_legacy != null
                ? ` (largest divergence ${runs[0].summary.marginism.max_abs_pct_vs_legacy.toFixed(3)}%)`
                : ""}
              .{" "}
              {runs[0].summary.marginism.mean_abs_pct_vs_icici_span != null
                ? `Both sit ${runs[0].summary.marginism.mean_abs_pct_vs_icici_span.toFixed(1)}% from ICICI's SPAN on average — a gap two implementations of the same algorithm cannot close.`
                : ""}
            </>
          ) : (
            <>
              {runs[0].summary.marginism.compared_cases} case(s) priced, but none against the exact
              snapshot they were compared on — intraday SPAN revisions make those figures
              non-comparable.
            </>
          )}
        </p>
      ) : runs[0]?.summary?.marginism?.unavailable_reasons?.length ? (
        <p className="text-xs text-muted">
          Independent cross-check unavailable: {runs[0].summary.marginism.unavailable_reasons[0]}
        </p>
      ) : null}

      {runs[0]?.summary?.icici_non_span_sample_count ? (
        <p className="text-xs text-muted">
          Latest run:{" "}
          {runs[0].summary.icici_non_span_seen_non_zero
            ? "ICICI returned a non-zero non_span_margin_required — exposure margin is readable directly from the broker response."
            : "ICICI returned non_span_margin_required = 0 on every case — its quoted figure is a single number, so the exposure model has to be inferred."}
        </p>
      ) : null}
    </div>
  );
}
