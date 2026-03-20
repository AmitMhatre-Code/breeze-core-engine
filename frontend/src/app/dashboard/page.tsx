"use client";

import { useCallback, useMemo } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AppShell } from "@/components/layout/AppShell";
import { InterpretationBadge } from "@/components/dashboard/InterpretationBadge";
import { Vix30dChart } from "@/components/dashboard/Vix30dChart";
import {
  interpretAtmIvPercent,
  interpretIndiaVix,
  interpretPcrOi,
} from "@/lib/dashboard-interpretation";
import { getHomeMarginTiles, type HomeDataResponse } from "@/lib/home-data";
import { formatIndianMoneyCompact } from "@/lib/format-money-in";
import { apiClient } from "@/lib/api-client";

type Vix30Point = { date: string; value: number };

type DashboardVixCore = {
  current_vix: number | null;
  nifty_spot: number | null;
  vix_30d: Vix30Point[];
  error?: string | null;
};

type DashboardVixOptions = {
  nifty_spot: number | null;
  next_expiry: string | null;
  atm_iv: number | null;
  expected_range: [number, number] | null;
  expected_move_pct: number | null;
  put_call_ratio: number | null;
  error?: string | null;
};

type PortfolioPositionRow = {
  current_profit?: number | null;
  pnl?: number | null;
  span_margin_required?: number | string | null;
};

type PortfolioApiResponse = {
  Status: number;
  Error?: string;
  Success?: {
    positions?: PortfolioPositionRow[];
  };
};

function coerceMarginField(v: unknown): number | null {
  if (v == null || v === "") return null;
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string") {
    const t = v.trim();
    if (!t || t === "*") return null;
    const n = Number(t);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

/** Per-row: SPAN only (dashboard “margin used” from positions excludes ELM). */
function spanMarginForPositionRow(p: PortfolioPositionRow): number {
  const span = coerceMarginField(p.span_margin_required);
  if (span != null && span > 0) return span;
  return 0;
}

function sumMarginUsedFromPositions(
  data: PortfolioApiResponse | undefined,
): number | null {
  if (!data || data.Status !== 200) return null;
  const positions = data.Success?.positions;
  if (!positions?.length) return 0;
  let t = 0;
  for (const p of positions) {
    t += spanMarginForPositionRow(p);
  }
  return t;
}

const emptyOpts = (): DashboardVixOptions => ({
  nifty_spot: null,
  next_expiry: null,
  atm_iv: null,
  expected_range: null,
  expected_move_pct: null,
  put_call_ratio: null,
});

function sumOpenPositionsPnl(data: PortfolioApiResponse | undefined): number | null {
  if (!data || data.Status !== 200) return null;
  const positions = data.Success?.positions;
  if (!positions?.length) return 0;
  let t = 0;
  for (const p of positions) {
    const cp = p.current_profit;
    if (typeof cp === "number" && Number.isFinite(cp)) {
      t += cp;
      continue;
    }
    const b = p.pnl;
    if (typeof b === "number" && Number.isFinite(b)) t += b;
  }
  return t;
}

export default function DashboardPage() {
  const queryClient = useQueryClient();

  const homeQ = useQuery({
    queryKey: ["home", "data"],
    queryFn: () => apiClient.get<HomeDataResponse>("/home/data"),
    staleTime: 30_000,
  });

  const portQ = useQuery({
    queryKey: ["portfolio", "positions"],
    queryFn: () => apiClient.get<PortfolioApiResponse>("/portfolio/data"),
    staleTime: 30_000,
  });

  const coreQ = useQuery({
    queryKey: ["dashboard", "vix"],
    queryFn: () => apiClient.get<DashboardVixCore>("/dashboard/vix"),
  });

  const optsQ = useQuery({
    queryKey: ["dashboard", "vix-options"],
    queryFn: async () => {
      try {
        return await apiClient.get<DashboardVixOptions>(
          "/dashboard/vix/options",
        );
      } catch (e) {
        return {
          ...emptyOpts(),
          error:
            e instanceof Error
              ? e.message
              : "Could not load options / IV metrics.",
        };
      }
    },
  });

  const core = coreQ.data;
  const opts = optsQ.data;
  const { funds, marginUsed: marginUsedFromHome } = getHomeMarginTiles(
    homeQ.data?.margin,
  );
  const marginUsedFromPositions = useMemo(
    () => sumMarginUsedFromPositions(portQ.data),
    [portQ.data],
  );
  // Home often reports margin used as 0 even with F&O risk; prefer positions when home is 0/null.
  const marginUsedDisplay =
    marginUsedFromHome != null && marginUsedFromHome > 0
      ? marginUsedFromHome
      : portQ.data?.Status === 200 && marginUsedFromPositions != null
        ? marginUsedFromPositions
        : marginUsedFromHome ?? null;
  const marginUsedPending =
    marginUsedFromHome == null && portQ.isPending && marginUsedDisplay == null;

  const openPnl = useMemo(() => sumOpenPositionsPnl(portQ.data), [portQ.data]);

  const dashboardWarnings = useMemo(() => {
    const w: string[] = [];
    const h = homeQ.data;
    if (h?.customer && typeof h.customer === "object") {
      const st = (h.customer as { Status?: number }).Status;
      if (st != null && st !== 200) {
        w.push("Customer details could not be loaded.");
      }
    }
    if (h?.margin && typeof h.margin === "object") {
      const st = (h.margin as { Status?: number }).Status;
      if (st != null && st !== 200) {
        w.push("Margin could not be loaded.");
      }
    }
    const pd = portQ.data;
    if (pd && pd.Status != null && pd.Status !== 200) {
      w.push("Positions could not be loaded.");
    }
    return w;
  }, [homeQ.data, portQ.data]);

  const openPositionCount =
    portQ.data?.Status === 200
      ? (portQ.data.Success?.positions?.length ?? 0)
      : null;

  const niftySpot =
    typeof opts?.nifty_spot === "number"
      ? opts.nifty_spot
      : typeof core?.nifty_spot === "number"
        ? core.nifty_spot
        : null;

  const vixInterp =
    typeof core?.current_vix === "number"
      ? interpretIndiaVix(core.current_vix)
      : null;
  const ivInterp =
    typeof opts?.atm_iv === "number" ? interpretAtmIvPercent(opts.atm_iv) : null;
  const pcrInterp =
    typeof opts?.put_call_ratio === "number"
      ? interpretPcrOi(opts.put_call_ratio)
      : null;

  const loading = homeQ.isPending || coreQ.isPending || optsQ.isPending;
  const blockingError = homeQ.error ?? coreQ.error;

  const volatilityFetching = coreQ.isFetching || optsQ.isFetching;
  const refreshVolatility = useCallback(() => {
    void Promise.all([
      queryClient.refetchQueries({ queryKey: ["dashboard", "vix"] }),
      queryClient.refetchQueries({ queryKey: ["dashboard", "vix-options"] }),
    ]);
  }, [queryClient]);

  return (
    <AppShell contentWidth="wide">
      {loading ? (
        <div className="app-card p-4">Loading dashboard...</div>
      ) : blockingError ? (
        <div className="app-alert-error">
          Unable to load dashboard:{" "}
          {blockingError instanceof Error
            ? blockingError.message
            : "Unknown error"}
        </div>
      ) : (
        <div className="grid min-w-0 gap-4 md:grid-cols-3">
          {dashboardWarnings.length > 0 ? (
            <div
              className="app-card col-span-full border-amber-200 bg-amber-50 p-3 text-sm text-amber-950 dark:border-amber-900/40 dark:bg-amber-950/25 dark:text-amber-100"
              role="alert"
            >
              <strong className="font-medium">
                Something went wrong loading some data.
              </strong>{" "}
              {dashboardWarnings.join(" ")} If this persists or you see invalid
              customer or session messages, try{" "}
              <a
                href="/logout"
                className="font-medium text-amber-900 underline dark:text-amber-50"
              >
                logging out
              </a>{" "}
              and logging back in.
            </div>
          ) : null}
          <section className="app-card col-span-2 min-w-0 space-y-3 p-4">
            <header className="flex items-center justify-between">
              <h2 className="app-text-heading">Account Overview</h2>
              <span className="app-text-muted uppercase tracking-wide">
                Intraday
              </span>
            </header>
            <div className="grid gap-3 md:grid-cols-3">
              <div className="app-card-muted p-3">
                <div className="text-[11px] uppercase tracking-wide text-zinc-500">
                  Open positions P&amp;L
                </div>
                <div
                  className={[
                    "mt-1 text-lg font-semibold tabular-nums",
                    portQ.isPending
                      ? "text-zinc-400 dark:text-zinc-500"
                      : portQ.isError || openPnl == null
                        ? "text-zinc-900 dark:text-zinc-300"
                        : openPnl > 0
                          ? "text-emerald-600 dark:text-emerald-400"
                          : openPnl < 0
                            ? "text-red-600 dark:text-red-400"
                            : "text-zinc-900 dark:text-zinc-200",
                  ].join(" ")}
                  title="Sum of MTM (current_profit) from open options positions; falls back to broker P&amp;L if needed"
                >
                  {portQ.isPending
                    ? "…"
                    : portQ.isError || openPnl == null
                      ? "—"
                      : formatIndianMoneyCompact(openPnl)}
                </div>
                {openPositionCount != null ? (
                  <div className="mt-0.5 text-[10px] app-text-muted">
                    {openPositionCount}{" "}
                    {openPositionCount === 1 ? "position" : "positions"}
                  </div>
                ) : null}
              </div>
              <div className="app-card-muted p-3">
                <div className="text-[11px] uppercase tracking-wide text-zinc-500">
                  Margin used
                </div>
                <div
                  className="mt-1 text-lg font-semibold text-zinc-900 tabular-nums dark:text-zinc-100"
                  title={
                    marginUsedFromHome != null && marginUsedFromHome > 0
                      ? "From ICICI margin situation (actual_margin_avl / cash / utilization)"
                      : portQ.data?.Status === 200
                        ? "Sum of span_margin_required across open NFO/BFO options (ELM not included)"
                        : undefined
                  }
                >
                  {marginUsedPending
                    ? "…"
                    : marginUsedDisplay != null
                      ? formatIndianMoneyCompact(marginUsedDisplay)
                      : "—"}
                </div>
              </div>
              <div className="app-card-muted p-3">
                <div className="text-[11px] uppercase tracking-wide text-zinc-500">
                  Funds available
                </div>
                <div className="mt-1 text-lg font-semibold text-zinc-900 tabular-nums dark:text-zinc-100">
                  {funds != null ? formatIndianMoneyCompact(funds) : "—"}
                </div>
              </div>
            </div>
          </section>
          <section className="app-card min-w-0 space-y-3 p-4">
            <header className="flex items-center justify-between gap-2">
              <h2 className="app-text-heading">NIFTY Volatility</h2>
              <div className="flex min-w-0 shrink-0 items-center gap-2">
                <span className="app-text-muted max-w-[9.5rem] truncate text-right sm:max-w-none">
                  {opts?.next_expiry ? `Exp ${opts.next_expiry}` : "NIFTY"}
                </span>
                <button
                  type="button"
                  onClick={refreshVolatility}
                  disabled={volatilityFetching}
                  title="Refresh VIX, IV, and range"
                  aria-label="Refresh volatility data"
                  className="inline-flex shrink-0 rounded-md p-0.5 text-zinc-500 transition hover:bg-zinc-100 hover:text-zinc-800 disabled:opacity-50 dark:text-zinc-400 dark:hover:bg-zinc-800/70 dark:hover:text-zinc-200"
                >
                  <VolatilityRefreshIcon spinning={volatilityFetching} />
                </button>
              </div>
            </header>
            <div className="app-card-muted space-y-2 p-3 text-sm">
              <div className="flex items-start justify-between gap-2 text-xs text-zinc-600 dark:text-zinc-400">
                <span className="shrink-0 pt-0.5">VIX</span>
                <span className="flex min-w-0 flex-wrap items-center justify-end gap-x-0.5 text-right">
                  <span className="font-medium text-zinc-900 dark:text-zinc-100">
                    {typeof core?.current_vix === "number"
                      ? core.current_vix.toFixed(2)
                      : "—"}
                  </span>
                  {vixInterp ? (
                    <InterpretationBadge
                      label={vixInterp.label}
                      tooltip={vixInterp.tooltip}
                      tone={vixInterp.tone}
                    />
                  ) : null}
                </span>
              </div>
              <div className="flex items-center justify-between text-xs text-zinc-600 dark:text-zinc-400">
                <span>NIFTY spot</span>
                <span className="font-medium text-zinc-900 dark:text-zinc-100">
                  {typeof niftySpot === "number" ? niftySpot.toFixed(2) : "—"}
                </span>
              </div>
              <div className="flex items-start justify-between gap-2 text-xs text-zinc-600 dark:text-zinc-400">
                <span className="shrink-0 pt-0.5">ATM IV</span>
                <span className="flex min-w-0 flex-wrap items-center justify-end gap-x-0.5 text-right font-medium text-zinc-900 dark:text-zinc-100">
                  {typeof opts?.atm_iv === "number"
                    ? `${opts.atm_iv.toFixed(2)}%`
                    : "—"}
                  {ivInterp ? (
                    <InterpretationBadge
                      label={ivInterp.label}
                      tooltip={ivInterp.tooltip}
                      tone={ivInterp.tone}
                    />
                  ) : null}
                </span>
              </div>
              <div className="flex items-center justify-between text-xs text-zinc-600 dark:text-zinc-400">
                <span>1σ range (ATM)</span>
                <span className="max-w-[58%] text-right font-medium text-zinc-900 dark:text-zinc-100">
                  {Array.isArray(opts?.expected_range) &&
                  opts.expected_range.length === 2 ? (
                    <>
                      {opts.expected_range[0].toFixed(2)} –{" "}
                      {opts.expected_range[1].toFixed(2)}
                      {typeof opts.expected_move_pct === "number" ? (
                        <span className="mt-0.5 block text-[10px] font-normal text-zinc-500">
                          ±{opts.expected_move_pct.toFixed(2)}% to expiry
                        </span>
                      ) : null}
                    </>
                  ) : (
                    "—"
                  )}
                </span>
              </div>
              <div className="flex items-start justify-between gap-2 text-xs text-zinc-600 dark:text-zinc-400">
                <span className="shrink-0 pt-0.5">Put:call (OI)</span>
                <span className="flex min-w-0 flex-wrap items-center justify-end gap-x-0.5 text-right font-medium text-zinc-900 dark:text-zinc-100">
                  {typeof opts?.put_call_ratio === "number"
                    ? opts.put_call_ratio.toFixed(2)
                    : "—"}
                  {pcrInterp ? (
                    <InterpretationBadge
                      label={pcrInterp.label}
                      tooltip={pcrInterp.tooltip}
                      tone={pcrInterp.tone}
                    />
                  ) : null}
                </span>
              </div>
            </div>
            {opts?.error ? (
              <p className="text-[11px] text-amber-800 dark:text-amber-200/90">
                IV / PCR: {opts.error}
              </p>
            ) : null}
            {core?.error ? (
              <p className="text-[11px] text-amber-800 dark:text-amber-200/90">
                VIX: {core.error}
              </p>
            ) : null}
          </section>
          <section className="app-card col-span-full min-w-0 p-4">
            <header className="mb-3 flex items-center justify-between">
              <h2 className="app-text-heading">India VIX — 30 days</h2>
            </header>
            <Vix30dChart series={core?.vix_30d ?? []} />
          </section>
        </div>
      )}
    </AppShell>
  );
}

function VolatilityRefreshIcon({ spinning }: { spinning: boolean }) {
  return (
    <svg
      width="13"
      height="13"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.25"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      className={spinning ? "animate-spin" : undefined}
    >
      <path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" />
      <path d="M3 3v5h5" />
      <path d="M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16" />
      <path d="M16 16h5v5" />
    </svg>
  );
}
