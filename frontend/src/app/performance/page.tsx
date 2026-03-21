"use client";

import { Suspense, useCallback, useId, useMemo } from "react";
import dynamic from "next/dynamic";
import { useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { AppShell } from "@/components/layout/AppShell";
import { FinancialYearDropdown } from "@/components/performance/FinancialYearDropdown";
import { apiClient } from "@/lib/api-client";
import { formatIndianMoneyCompact } from "@/lib/format-money-in";
import {
  buildPerformanceDataPath,
  iciciSuccess,
  parseMonthlyPerformance,
  type FinancialYearOption,
  type PerformanceDataPayload,
} from "@/lib/performance-data";

const PerformanceMonthlyChart = dynamic(
  () =>
    import("@/components/performance/PerformanceMonthlyChart").then(
      (m) => m.PerformanceMonthlyChart,
    ),
  {
    ssr: false,
    loading: () => (
      <div className="app-card-muted min-h-[300px] animate-pulse rounded-xl" />
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

type PerformanceSuccess = {
  net_pnl?: number;
  premium_earned?: number;
  premium_paid?: number;
  brokerage?: number;
  taxes?: number;
  annualised_roi?: number;
};

function num(v: unknown, fallback = 0): number {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
}

function PerformancePageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const fyLabelId = useId();
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
  const perfStatus =
    perfBlock && typeof perfBlock === "object"
      ? (perfBlock as { Status?: number }).Status
      : undefined;
  const perfError =
    perfBlock && typeof perfBlock === "object"
      ? (perfBlock as { Error?: string }).Error
      : undefined;

  const monthly = useMemo(
    () => (data ? parseMonthlyPerformance(data.performance) : []),
    [data],
  );

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
    <AppShell contentWidth="wide">
      {q.isPending ? (
        <div className="app-card p-4 text-sm text-zinc-500 dark:text-zinc-400">
          Loading performance…
        </div>
      ) : q.error ? (
        <div className="app-alert-error">
          {q.error instanceof Error ? q.error.message : "Unable to load"}
        </div>
      ) : !data ? (
        <div className="app-alert-error">No data returned.</div>
      ) : (
        <div className="grid min-w-0 gap-4">
          <header className="flex flex-col gap-1 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <h1 className="text-base font-semibold text-zinc-900 dark:text-zinc-100">
                Performance
              </h1>
              <p className="text-xs text-zinc-500 dark:text-zinc-400">
                Bank balance, margins, and options P&amp;L by financial year
                (same sources as the classic app).
              </p>
            </div>
          </header>

          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            <section className="app-card flex flex-col p-4">
              <h2 className="text-[11px] font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                Bank balance
              </h2>
              {funds ? (
                <>
                  <p className="mt-2 text-2xl font-semibold tabular-nums text-zinc-900 dark:text-zinc-50">
                    {formatIndianMoneyCompact(
                      num(funds.total_bank_balance),
                    )}
                  </p>
                  <hr className="my-3 border-zinc-200 dark:border-zinc-800" />
                  <dl className="space-y-1.5 text-xs text-zinc-600 dark:text-zinc-400">
                    <div className="flex justify-between gap-2">
                      <dt>Equity</dt>
                      <dd className="tabular-nums text-zinc-900 dark:text-zinc-200">
                        {formatIndianMoneyCompact(
                          num(funds.allocated_equity) +
                            num(funds.block_by_trade_equity),
                        )}
                      </dd>
                    </div>
                    <div className="flex justify-between gap-2">
                      <dt>F&amp;O</dt>
                      <dd className="tabular-nums text-zinc-900 dark:text-zinc-200">
                        {formatIndianMoneyCompact(
                          num(funds.allocated_fno) +
                            num(funds.block_by_trade_fno),
                        )}
                      </dd>
                    </div>
                    <div className="flex justify-between gap-2">
                      <dt>Commodity</dt>
                      <dd className="tabular-nums text-zinc-900 dark:text-zinc-200">
                        {formatIndianMoneyCompact(
                          num(funds.allocated_commodity) +
                            num(funds.block_by_trade_commodity),
                        )}
                      </dd>
                    </div>
                    <div className="flex justify-between gap-2">
                      <dt>Currency</dt>
                      <dd className="tabular-nums text-zinc-900 dark:text-zinc-200">
                        {formatIndianMoneyCompact(
                          num(funds.allocated_currency) +
                            num(funds.block_by_trade_currency),
                        )}
                      </dd>
                    </div>
                    <div className="flex justify-between gap-2">
                      <dt>Unallocated</dt>
                      <dd className="tabular-nums text-zinc-900 dark:text-zinc-200">
                        {formatIndianMoneyCompact(
                          num(funds.unallocated_balance),
                        )}
                      </dd>
                    </div>
                  </dl>
                </>
              ) : (
                <p className="mt-2 text-sm text-amber-800 dark:text-amber-200/90">
                  Funds could not be loaded (broker response not OK).
                </p>
              )}
            </section>

            <section className="app-card flex flex-col p-4">
              <h2 className="text-[11px] font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                Margins
              </h2>
              {margin ? (
                <>
                  <p className="mt-2 text-2xl font-semibold tabular-nums text-zinc-900 dark:text-zinc-50">
                    {formatIndianMoneyCompact(num(margin.cash_limit))}
                  </p>
                  <hr className="my-3 border-zinc-200 dark:border-zinc-800" />
                  <dl className="space-y-1.5 text-xs text-zinc-600 dark:text-zinc-400">
                    <div className="flex justify-between gap-2">
                      <dt>Utilised</dt>
                      <dd className="tabular-nums text-zinc-900 dark:text-zinc-200">
                        {formatIndianMoneyCompact(
                          -num(margin.actual_margin_ute),
                        )}
                      </dd>
                    </div>
                    <div className="flex justify-between gap-2">
                      <dt>Available</dt>
                      <dd className="tabular-nums text-zinc-900 dark:text-zinc-200">
                        {formatIndianMoneyCompact(
                          num(margin.actual_margin_avl),
                        )}
                      </dd>
                    </div>
                  </dl>
                  <p className="mt-3 text-[11px] italic text-zinc-500 dark:text-zinc-500">
                    *Margin may be under-calculated (ICICI API)
                  </p>
                </>
              ) : (
                <p className="mt-2 text-sm text-amber-800 dark:text-amber-200/90">
                  Margin could not be loaded. Realised P&amp;L for the year is
                  unavailable until margin loads.
                </p>
              )}
            </section>

            <section className="app-card flex flex-col p-4 md:col-span-2 xl:col-span-1">
              <h2 className="text-[11px] font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                FY {data.fy || selectedFy || "—"} P&amp;L statement
              </h2>
              {performance ? (
                <>
                  <p
                    className={[
                      "mt-2 text-2xl font-semibold tabular-nums",
                      num(performance.net_pnl) >= 0
                        ? "text-emerald-600 dark:text-emerald-400"
                        : "text-red-600 dark:text-red-400",
                    ].join(" ")}
                  >
                    {formatIndianMoneyCompact(num(performance.net_pnl))}
                  </p>
                  <hr className="my-3 border-zinc-200 dark:border-zinc-800" />
                  <dl className="space-y-1.5 text-xs text-zinc-600 dark:text-zinc-400">
                    <div className="flex justify-between gap-2">
                      <dt>Premium earned</dt>
                      <dd className="tabular-nums text-zinc-900 dark:text-zinc-200">
                        {formatIndianMoneyCompact(
                          num(performance.premium_earned),
                        )}
                      </dd>
                    </div>
                    <div className="flex justify-between gap-2">
                      <dt>Premium paid</dt>
                      <dd className="tabular-nums text-zinc-900 dark:text-zinc-200">
                        {formatIndianMoneyCompact(
                          num(performance.premium_paid),
                        )}
                      </dd>
                    </div>
                    <div className="flex justify-between gap-2">
                      <dt>Brokerage</dt>
                      <dd className="tabular-nums text-zinc-900 dark:text-zinc-200">
                        {formatIndianMoneyCompact(
                          num(performance.brokerage),
                        )}
                      </dd>
                    </div>
                    <div className="flex justify-between gap-2">
                      <dt>Taxes</dt>
                      <dd className="tabular-nums text-zinc-900 dark:text-zinc-200">
                        {formatIndianMoneyCompact(num(performance.taxes))}
                      </dd>
                    </div>
                    <div className="flex justify-between gap-2">
                      <dt>Annualised ROI</dt>
                      <dd
                        className={[
                          "tabular-nums font-medium",
                          num(performance.annualised_roi) >= 0
                            ? "text-emerald-600 dark:text-emerald-400"
                            : "text-red-600 dark:text-red-400",
                        ].join(" ")}
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
                <p className="mt-2 text-sm text-amber-800 dark:text-amber-200/90">
                  {perfStatus != null && perfStatus !== 200
                    ? `Performance unavailable (${perfStatus}${perfError ? `: ${perfError}` : ""}).`
                    : "Performance data not available."}
                </p>
              )}
            </section>
          </div>

          <section className="app-card min-w-0 p-4">
            <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <h2 className="app-text-heading">Monthly financial overview</h2>
              {years.length > 0 ? (
                <div className="flex flex-wrap items-center gap-x-3 gap-y-2 sm:justify-end">
                  <FinancialYearDropdown
                    labelId={fyLabelId}
                    years={years}
                    selectedYear={selectedFy}
                    onSelect={selectFiscalYear}
                  />
                </div>
              ) : null}
            </div>
            <PerformanceMonthlyChart monthly={monthly} />
          </section>
        </div>
      )}
    </AppShell>
  );
}

export default function PerformancePage() {
  return (
    <Suspense
      fallback={
        <AppShell contentWidth="wide">
          <div className="app-card p-4 text-sm text-zinc-500 dark:text-zinc-400">
            Loading performance…
          </div>
        </AppShell>
      }
    >
      <PerformancePageInner />
    </Suspense>
  );
}
