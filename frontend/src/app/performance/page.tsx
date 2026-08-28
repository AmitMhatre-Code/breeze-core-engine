"use client";

import { Suspense, useCallback, useId, useMemo, useState } from "react";
import dynamic from "next/dynamic";
import { useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { AppShell } from "@/components/layout/AppShell";
import { FinancialYearDropdown } from "@/components/performance/FinancialYearDropdown";
import { PerformanceExportDialog } from "@/components/performance/PerformanceExportDialog";
import { apiClient } from "@/lib/api-client";
import { formatIndianMoneyCompact } from "@/lib/format-money-in";
import {
  buildPerformanceDataPath,
  iciciSuccess,
  padMonthlyToFinancialYear,
  padWeeklyToFinancialYear,
  parseMonthlyPerformance,
  parseWeeklyPerformance,
  type FinancialYearOption,
  type PerformanceDataPayload,
} from "@/lib/performance-data";

/** Compact ₹, explicit sign (unicode minus, no parens) — Terminal Pro money convention.
 * `sign: "always"` also prefixes a "+" for positive (headline P&L totals only). */
function formatMoneyCompact(
  amount: number,
  opts?: { sign?: "always" | "negative-only" },
): string {
  const magnitude = formatIndianMoneyCompact(Math.abs(amount), {
    shortSuffix: true,
    skipK: true,
  });
  if (amount < 0) return `−${magnitude}`;
  if (amount > 0 && opts?.sign === "always") return `+${magnitude}`;
  return magnitude;
}

function moneyTone(amount: number): string {
  if (amount > 0) return "text-up";
  if (amount < 0) return "text-down";
  return "text-foreground";
}

const PerformancePeriodChart = dynamic(
  () =>
    import("@/components/performance/PerformancePeriodChart").then(
      (m) => m.PerformancePeriodChart,
    ),
  {
    ssr: false,
    loading: () => (
      <div className="app-skeleton min-h-[300px] rounded-md border-0" />
    ),
  },
);

type FundsSuccess = {
  total_bank_balance?: number;
  allocated_equity?: number;
  block_by_trade_equity?: number;
  allocated_fno?: number;
  block_by_trade_fno?: number;
  allocated_commodity?: number;
  block_by_trade_commodity?: number;
  allocated_currency?: number;
  block_by_trade_currency?: number;
  unallocated_balance?: number;
};

type MarginSuccess = {
  cash_limit?: number;
  actual_margin_ute?: number;
  actual_margin_avl?: number;
};

type AnnualisedRoiBreakdown = {
  net_pnl?: number;
  elapsed_days?: number;
  total_margin?: number;
};

type PerformanceSuccess = {
  net_pnl?: number;
  premium_earned?: number;
  premium_paid?: number;
  brokerage?: number;
  taxes?: number;
  annualised_roi?: number;
  annualised_roi_breakdown?: AnnualisedRoiBreakdown;
};

function num(v: unknown, fallback = 0): number {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
}

function buildPerformanceRoiTooltip(
  roiFraction: number,
  breakdown: AnnualisedRoiBreakdown | undefined,
): string {
  if (
    breakdown == null ||
    breakdown.total_margin == null ||
    breakdown.elapsed_days == null
  ) {
    return [
      "Annualised ROI (FY): (P&L ÷ total margin) × (365 ÷ elapsed FY days).",
      "The value is shown as a percentage (×100).",
      "Total margin = deployed + free margin.",
      "Elapsed FY days are counted from 1-Apr to today for in-progress FY.",
    ].join("\n");
  }
  const pnl = num(breakdown.net_pnl);
  const days = Math.max(1, Math.round(num(breakdown.elapsed_days, 1)));
  const margin = num(breakdown.total_margin);
  const pnlS = `₹${pnl.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  const marginS = `₹${margin.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  const pct = roiFraction * 100;
  return [
    "Annualised ROI (FY):",
    "(P&L ÷ total margin) × (365 ÷ elapsed FY days)",
    "",
    `P&L = ${pnlS}`,
    `elapsed FY days = ${days}`,
    `total margin (deployed + free) = ${marginS}`,
    "",
    `= (${pnl.toLocaleString("en-IN", { maximumFractionDigits: 2 })} ÷ ${margin.toLocaleString("en-IN", { maximumFractionDigits: 2 })}) × (365 ÷ ${days})`,
    `× 100 on screen ≈ ${pct.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}%`,
  ].join("\n");
}

function PerformancePageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const fyLabelId = useId();
  const [chartViewMode, setChartViewMode] = useState<"chart" | "table">(
    "chart",
  );
  const [granularity, setGranularity] = useState<"month" | "week">("month");
  const [exportOpen, setExportOpen] = useState(false);
  const path = useMemo(
    () => buildPerformanceDataPath(searchParams),
    [searchParams],
  );

  const q = useQuery({
    queryKey: ["performance", "data", path],
    queryFn: () => apiClient.get<PerformanceDataPayload>(path),
  });

  const data = q.data;
  const funds = data ? iciciSuccess<FundsSuccess>(data.funds) : undefined;
  const margin = data ? iciciSuccess<MarginSuccess>(data.margin) : undefined;
  const perfOk = data?.performance && typeof data.performance === "object";
  const perfBlock = perfOk ? data.performance : undefined;
  const performance = perfBlock
    ? iciciSuccess<PerformanceSuccess>(perfBlock)
    : undefined;
  const performanceRoiTooltip = useMemo(() => {
    if (!performance) return "";
    return buildPerformanceRoiTooltip(
      num(performance.annualised_roi),
      performance.annualised_roi_breakdown,
    );
  }, [performance]);
  const perfStatus =
    perfBlock && typeof perfBlock === "object"
      ? (perfBlock as { Status?: number }).Status
      : undefined;
  const perfError =
    perfBlock && typeof perfBlock === "object"
      ? (perfBlock as { Error?: string }).Error
      : undefined;

  const monthlyActual = useMemo(
    () => (data ? parseMonthlyPerformance(data.performance) : []),
    [data],
  );
  const monthly = useMemo(
    () => padMonthlyToFinancialYear(monthlyActual, data?.start),
    [monthlyActual, data],
  );
  const weekly = useMemo(
    () =>
      padWeeklyToFinancialYear(
        data ? parseWeeklyPerformance(data.performance) : [],
        data?.start,
        data?.end,
      ),
    [data],
  );
  const periodRows = granularity === "week" ? weekly : monthly;

  const years = useMemo(() => {
    const y = data?.years;
    return y?.length ? y : [];
  }, [data]);

  const selectFiscalYear = useCallback(
    (y: FinancialYearOption) => {
      const p = new URLSearchParams();
      p.set("fy", y.year);
      p.set("start", y.start);
      p.set("end", y.end);
      router.replace(`/performance?${p.toString()}`);
    },
    [router],
  );

  const urlFy = searchParams.get("fy");
  const selectedFy = useMemo(() => {
    if (!years.length) return "";
    if (urlFy && years.some((y) => y.year === urlFy)) return urlFy;
    const applied = data?.fy;
    if (applied && years.some((y) => y.year === applied)) return applied;
    return years[0].year;
  }, [data, urlFy, years]);

  return (
    <AppShell>
      <div className="mx-auto max-w-[1200px]">
      {q.isPending ? (
        <div className="grid grid-cols-[minmax(0,1fr)] gap-4 md:grid-cols-2 xl:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <div key={i} className="app-card min-h-[180px] p-4">
              <div className="h-full min-h-[140px] w-full app-skeleton rounded-sm border-0" />
            </div>
          ))}
        </div>
      ) : q.error ? (
        <div className="app-alert-error">
          {q.error instanceof Error ? q.error.message : "Unable to load"}
        </div>
      ) : !data ? (
        <div className="app-alert-error">No data returned.</div>
      ) : (
        <div className="grid min-w-0 gap-4">
          <header className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <h1 className="app-text-title">Performance</h1>
              <p className="text-xs text-muted">
                Bank balance, margins, and options P&amp;L
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2.5">
              {years.length > 0 ? (
                <FinancialYearDropdown
                  labelId={fyLabelId}
                  years={years}
                  selectedYear={selectedFy}
                  onSelect={selectFiscalYear}
                />
              ) : null}
              <button
                type="button"
                className="flex h-9 items-center gap-1.5 rounded-[9px] border border-border bg-transparent px-3.5 text-xs font-semibold text-accent-strong transition hover:bg-accent-tint"
                onClick={() => setExportOpen(true)}
              >
                <svg
                  width="14"
                  height="14"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2.2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  aria-hidden
                >
                  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                  <polyline points="7 10 12 15 17 10" />
                  <line x1="12" y1="15" x2="12" y2="3" />
                </svg>
                Export
              </button>
            </div>
          </header>

          <div className="grid grid-cols-[minmax(0,1fr)] gap-4 md:grid-cols-2 xl:grid-cols-3">
            <section className="app-card flex min-w-0 flex-col p-4">
              <h2 className="text-heading font-bold uppercase tracking-[.07em] text-faint">
                Bank balance
              </h2>
              {funds ? (
                <>
                  <p className="mt-2 font-mono text-[25px] font-semibold tabular-nums text-foreground">
                    {formatMoneyCompact(num(funds.total_bank_balance))}
                  </p>
                  <hr className="my-3 border-border-soft" />
                  <dl className="space-y-1.5 text-xs text-muted">
                    {(
                      [
                        [
                          "Equity",
                          num(funds.allocated_equity) +
                            num(funds.block_by_trade_equity),
                        ],
                        [
                          "F&O",
                          num(funds.allocated_fno) +
                            num(funds.block_by_trade_fno),
                        ],
                        [
                          "Commodity",
                          num(funds.allocated_commodity) +
                            num(funds.block_by_trade_commodity),
                        ],
                        [
                          "Currency",
                          num(funds.allocated_currency) +
                            num(funds.block_by_trade_currency),
                        ],
                      ] as const
                    ).map(([label, amount]) => (
                      <div className="flex justify-between gap-2" key={label}>
                        <dt>{label}</dt>
                        <dd
                          className={`font-mono tabular-nums ${amount === 0 ? "text-muted" : "text-foreground"}`}
                        >
                          {formatMoneyCompact(amount)}
                        </dd>
                      </div>
                    ))}
                    <div className="flex justify-between gap-2">
                      <dt>Unallocated</dt>
                      <dd
                        className={`font-mono tabular-nums ${moneyTone(num(funds.unallocated_balance))}`}
                      >
                        {formatMoneyCompact(num(funds.unallocated_balance))}
                      </dd>
                    </div>
                  </dl>
                </>
              ) : (
                <p className="mt-2 text-sm text-amber-accent">
                  Funds could not be loaded (broker response not OK).
                </p>
              )}
            </section>

            <section className="app-card flex min-w-0 flex-col p-4">
              <h2 className="text-heading font-bold uppercase tracking-[.07em] text-faint">
                Margins
              </h2>
              {margin ? (
                <>
                  <p className="mt-2 font-mono text-[25px] font-semibold tabular-nums text-foreground">
                    {formatMoneyCompact(num(margin.cash_limit))}
                  </p>
                  <hr className="my-3 border-border-soft" />
                  <dl className="space-y-1.5 text-xs text-muted">
                    <div className="flex justify-between gap-2">
                      <dt>Utilised</dt>
                      <dd className="font-mono tabular-nums text-down">
                        {formatMoneyCompact(-num(margin.actual_margin_ute))}
                      </dd>
                    </div>
                    <div className="flex justify-between gap-2">
                      <dt>Available</dt>
                      <dd
                        className={`font-mono tabular-nums ${moneyTone(num(margin.actual_margin_avl))}`}
                      >
                        {formatMoneyCompact(num(margin.actual_margin_avl))}
                      </dd>
                    </div>
                  </dl>
                  <p className="mt-3 text-heading italic text-faint">
                    *Margin may be under-calculated (ICICI API does not return upstreamed amount)
                  </p>
                </>
              ) : (
                <p className="mt-2 text-sm text-amber-accent">
                  Margin could not be loaded. Realised P&amp;L for the year is
                  unavailable until margin loads.
                </p>
              )}
            </section>

            <section className="app-card flex min-w-0 flex-col p-4 md:col-span-2 xl:col-span-1">
              <h2 className="text-heading font-bold uppercase tracking-[.07em] text-faint">
                FY {data.fy || selectedFy || "—"}{" "}P&amp;L statement
              </h2>
              {performance ? (
                <>
                  <p
                    className={`mt-2 font-mono text-2xl font-semibold tabular-nums ${moneyTone(num(performance.net_pnl))}`}
                  >
                    {formatMoneyCompact(num(performance.net_pnl), {
                      sign: "always",
                    })}
                  </p>
                  <hr className="my-3 border-border-soft" />
                  <dl className="space-y-1.5 text-xs text-muted">
                    <div className="flex justify-between gap-2">
                      <dt>Premium earned</dt>
                      <dd
                        className={`font-mono tabular-nums ${moneyTone(num(performance.premium_earned))}`}
                      >
                        {formatMoneyCompact(num(performance.premium_earned))}
                      </dd>
                    </div>
                    <div className="flex justify-between gap-2">
                      <dt>Premium paid</dt>
                      <dd className="font-mono tabular-nums text-down">
                        {formatMoneyCompact(-num(performance.premium_paid))}
                      </dd>
                    </div>
                    <div className="flex justify-between gap-2">
                      <dt>Brokerage</dt>
                      <dd className="font-mono tabular-nums text-down">
                        {formatMoneyCompact(-num(performance.brokerage))}
                      </dd>
                    </div>
                    <div className="flex justify-between gap-2">
                      <dt>Taxes</dt>
                      <dd className="font-mono tabular-nums text-down">
                        {formatMoneyCompact(-num(performance.taxes))}
                      </dd>
                    </div>
                    <div className="flex justify-between gap-2 border-t border-dashed border-border-soft pt-[3px]">
                      <dt
                        title={performanceRoiTooltip}
                        className="font-semibold text-foreground"
                      >
                        Annualised ROI
                      </dt>
                      <dd
                        title={performanceRoiTooltip}
                        className={`font-mono tabular-nums font-semibold ${
                          num(performance.annualised_roi) >= 0
                            ? "text-up"
                            : "text-down"
                        }`}
                      >
                        {(num(performance.annualised_roi) * 100).toLocaleString(
                          "en-IN",
                          {
                            minimumFractionDigits: 2,
                            maximumFractionDigits: 2,
                          },
                        )}
                        %
                      </dd>
                    </div>
                  </dl>
                </>
              ) : (
                <p className="mt-2 text-sm text-amber-accent">
                  {perfStatus != null && perfStatus !== 200
                    ? `Performance unavailable (${perfStatus}${perfError ? `: ${perfError}` : ""}).`
                    : "Performance data not available."}
                </p>
              )}
            </section>
          </div>

          <section className="app-card min-w-0">
            <div className="flex flex-col gap-3 border-b border-border-soft px-4 pb-3 pt-4 sm:flex-row sm:items-center sm:justify-between">
              <h2 className="text-heading font-bold uppercase tracking-[.07em] text-faint">
                {granularity === "week" ? "Weekly" : "Monthly"} financial
                overview
              </h2>
              <div className="flex flex-wrap items-center gap-2.5">
              <div className="inline-flex rounded-[9px] border border-border bg-panel2 p-[3px]">
                {(
                  [
                    ["month", "Monthly"],
                    ["week", "Weekly"],
                  ] as const
                ).map(([value, label]) => (
                  <button
                    key={value}
                    type="button"
                    className={[
                      "rounded-[6px] px-3.5 py-1.5 font-mono text-table font-semibold transition",
                      granularity === value
                        ? "bg-accent-strong text-accent-ink"
                        : "text-muted hover:text-foreground",
                    ].join(" ")}
                    aria-pressed={granularity === value}
                    onClick={() => setGranularity(value)}
                  >
                    {label}
                  </button>
                ))}
              </div>
              <div className="inline-flex rounded-[9px] border border-border bg-panel2 p-[3px]">
                <button
                  type="button"
                  className={[
                    "rounded-[6px] px-3.5 py-1.5 font-mono text-table font-semibold transition",
                    chartViewMode === "chart"
                      ? "bg-accent-strong text-accent-ink"
                      : "text-muted hover:text-foreground",
                  ].join(" ")}
                  aria-pressed={chartViewMode === "chart"}
                  onClick={() => setChartViewMode("chart")}
                >
                  Chart
                </button>
                <button
                  type="button"
                  className={[
                    "rounded-[6px] px-3.5 py-1.5 font-mono text-table font-semibold transition",
                    chartViewMode === "table"
                      ? "bg-accent-strong text-accent-ink"
                      : "text-muted hover:text-foreground",
                  ].join(" ")}
                  aria-pressed={chartViewMode === "table"}
                  onClick={() => setChartViewMode("table")}
                >
                  Table
                </button>
              </div>
              </div>
            </div>
            <div className="p-4">
              <PerformancePeriodChart
                rows={periodRows}
                viewMode={chartViewMode}
                periodLabel={granularity === "week" ? "Week" : "Month"}
              />
            </div>
          </section>

          <PerformanceExportDialog
            open={exportOpen}
            onClose={() => setExportOpen(false)}
            monthly={monthlyActual}
            summary={{
              fyLabel: `FY ${data.fy || selectedFy || "—"}`,
              totalBankBalance: num(funds?.total_bank_balance),
              cashLimit: num(margin?.cash_limit),
              netPnl: num(performance?.net_pnl),
              premiumEarned: num(performance?.premium_earned),
              premiumPaid: num(performance?.premium_paid),
              brokerage: num(performance?.brokerage),
              taxes: num(performance?.taxes),
              annualisedRoiPct: num(performance?.annualised_roi) * 100,
            }}
          />
        </div>
      )}
      </div>
    </AppShell>
  );
}

export default function PerformancePage() {
  return (
    <Suspense
      fallback={
        <AppShell>
          <div className="mx-auto max-w-[1200px] grid grid-cols-[minmax(0,1fr)] gap-4 md:grid-cols-2 xl:grid-cols-3">
            {[0, 1, 2].map((i) => (
              <div key={i} className="app-card min-h-[180px] p-4">
              <div className="h-full min-h-[140px] w-full app-skeleton rounded-sm border-0" />
            </div>
            ))}
          </div>
        </AppShell>
      }
    >
      <PerformancePageInner />
    </Suspense>
  );
}
