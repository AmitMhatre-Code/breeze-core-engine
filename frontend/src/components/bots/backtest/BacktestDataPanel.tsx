"use client";

import { formatApiDateTime } from "@/lib/format-iso-date";
import type { BacktestOverview, CoverageSpan, ProbeOption } from "@/lib/bots-backtest";

function span(s: CoverageSpan | undefined): string {
  if (!s || s.days === 0) return "none";
  const short = s.short_days ? ` · ${s.short_days} short` : "";
  return `${s.days} days, ${s.from} → ${s.to}${short}`;
}

function probeLine(label: string, p: ProbeOption | undefined) {
  if (!p) return null;
  const served = p.verdict === "served";
  return (
    <li className="flex flex-wrap justify-between gap-x-3">
      <span className="text-muted">{label}</span>
      <span className={served ? "text-foreground" : "text-down"}>
        {p.verdict ?? "—"}
        {p.minute_bars != null ? ` (${p.minute_bars} bars, ${p.contract ?? "?"})` : ""}
      </span>
    </li>
  );
}

/** What ICICI gave the probe, and what the cache holds. Read-only; actions live in the controls. */
export function BacktestDataPanel({ overview }: { overview: BacktestOverview }) {
  const { probe, coverage } = overview;
  const expired = probe?.expired_nifty_weekly;

  return (
    <section className="space-y-3 rounded-[10px] border border-border px-4 py-3.5">
      <div>
        <h2 className="text-heading font-bold text-foreground">Price history</h2>
        <p className="mt-1 text-table leading-relaxed text-muted">
          Option prices come from ICICI&apos;s traded candles and are cached on this instance.
          Fetching downloads only the contracts a backtest actually needs.
        </p>
      </div>

      <div className="border-t border-border-soft pt-3">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-faint">ICICI probe</h3>
        {probe ? (
          <ul className="mt-2 space-y-1 text-table">
            {probeLine("Expired NIFTY weeklies", expired)}
            {probeLine(`From ${overview.history_start}`, probe.history_start_nifty_weekly)}
            {probeLine("Expired SENSEX weeklies", probe.expired_sensex_weekly)}
            <li className="flex justify-between gap-3">
              <span className="text-muted">Candles per call</span>
              <span className="font-mono tabular-nums text-foreground">{String(probe.per_call_cap ?? "—")}</span>
            </li>
            <li className="flex justify-between gap-3">
              <span className="text-muted">1-second request clock</span>
              <span className="text-foreground">{expired?.request_clock?.toUpperCase() ?? "not measured"}</span>
            </li>
            <li className="text-xs text-faint">Probed {formatApiDateTime(overview.probed_at)}</li>
          </ul>
        ) : (
          <p className="mt-2 text-xs text-muted">
            Not probed yet. The probe spends about a dozen calls to check whether ICICI serves expired
            option contracts. If it doesn&apos;t, only model-priced backtests are possible.
          </p>
        )}
        {expired && expired.verdict !== "served" ? (
          <p className="app-alert-error mt-2 text-xs">
            ICICI did not serve an expired contract, so real-price backtests will find no data. Use
            model prices.
          </p>
        ) : null}
      </div>

      <div className="border-t border-border-soft pt-3">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-faint">Cached</h3>
        <ul className="mt-2 space-y-1 text-table">
          <li className="flex flex-wrap justify-between gap-x-3">
            <span className="text-muted">NIFTY futures</span>
            <span className="text-foreground">{span(coverage.nifty_futures)}</span>
          </li>
          <li className="flex flex-wrap justify-between gap-x-3">
            <span className="text-muted">NIFTY index</span>
            <span className="text-foreground">{span(coverage.nifty_index)}</span>
          </li>
          <li className="flex flex-wrap justify-between gap-x-3">
            <span className="text-muted">SENSEX index</span>
            <span className="text-foreground">{span(coverage.sensex_index)}</span>
          </li>
          <li className="flex flex-wrap justify-between gap-x-3">
            <span className="text-muted">India VIX</span>
            <span className="text-foreground">{coverage.vix.days ? `${coverage.vix.days} days` : "none"}</span>
          </li>
          <li className="flex flex-wrap justify-between gap-x-3">
            <span className="text-muted">Option contracts</span>
            <span className="text-foreground">
              {coverage.option_contracts}
              {coverage.option_windows_empty ? ` · ${coverage.option_windows_empty} empty windows` : ""}
            </span>
          </li>
          <li className="flex flex-wrap justify-between gap-x-3">
            <span className="text-muted">Waiting to fetch</span>
            <span className={coverage.option_needs_pending ? "text-accent-strong" : "text-foreground"}>
              {coverage.option_needs_pending} option windows
            </span>
          </li>
        </ul>
      </div>
    </section>
  );
}
