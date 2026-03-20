"use client";

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
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

type PortfolioApiResponse = {
  Status: number;
  Success?: {
    positions?: Array<{ pnl: number }>;
  };
};

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
    if (typeof p.pnl === "number" && Number.isFinite(p.pnl)) t += p.pnl;
  }
  return t;
}

export default function DashboardPage() {
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
  const { funds, marginUsed } = getHomeMarginTiles(homeQ.data?.margin);
  const openPnl = useMemo(() => sumOpenPositionsPnl(portQ.data), [portQ.data]);

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

  return (
    <AppShell>
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
        <div className="grid gap-4 md:grid-cols-3">
          <section className="app-card col-span-2 space-y-3 p-4">
            <header className="flex items-center justify-between">
              <h2 className="app-text-heading">Account overview</h2>
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
                  title="Sum of P&amp;L on current open positions from portfolio"
                >
                  {portQ.isPending
                    ? "…"
                    : portQ.isError || openPnl == null
                      ? "—"
                      : formatIndianMoneyCompact(openPnl)}
                </div>
              </div>
              <div className="app-card-muted p-3">
                <div className="text-[11px] uppercase tracking-wide text-zinc-500">
                  Margin used
                </div>
                <div className="mt-1 text-lg font-semibold text-zinc-900 tabular-nums dark:text-zinc-100">
                  {marginUsed != null
                    ? formatIndianMoneyCompact(marginUsed)
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
          <section className="app-card space-y-3 p-4">
            <header className="flex items-center justify-between gap-2">
              <h2 className="app-text-heading">NIFTY & volatility</h2>
              <span className="app-text-muted">
                {opts?.next_expiry ? `Exp ${opts.next_expiry}` : "NIFTY"}
              </span>
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
          <section className="app-card col-span-full p-4">
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
