"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AppShell } from "@/components/layout/AppShell";
import { InterpretationBadge } from "@/components/dashboard/InterpretationBadge";
import { DashboardMetricSkeleton } from "@/components/dashboard/DashboardLoading";
import { MarketConnectionOverlay } from "@/components/dashboard/MarketConnectionOverlay";
import { Vix30dChart } from "@/components/dashboard/Vix30dChart";
import { useWsHealth } from "@/lib/use-ws-health";
import {
  interpretAtmIvPercent,
  interpretIndiaVix,
  interpretPcrOi,
} from "@/lib/dashboard-interpretation";
import { getHomeMarginTiles, type HomeDataResponse } from "@/lib/home-data";
import {
  fetchDashboardBootstrap,
  fetchDashboardVixHistory,
  fetchDashboardVixOptions,
  hydrateDashboardQueryCache,
  type DashboardVixCore,
  type DashboardVixOptions,
  type PortfolioApiResponse,
} from "@/lib/dashboard-bootstrap";
import { formatIndianMoneyCompact, moneyToneClass } from "@/lib/format-money-in";
import { ApiHttpError, apiClient } from "@/lib/api-client";
import {
  getMarketOutlook,
  outlookFetchErrorMessage,
  type OutlookResponse,
  type OutlookSummaryCategory,
} from "@/lib/outlook-api";

type Vix30Point = { date: string; value: number };

type MarketOutlookBadgePhase = "idle" | "loading" | "cached" | "updated" | "unavailable";

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
function spanMarginForPositionRow(p: {
  span_margin_required?: number | string | null;
}): number {
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
  strike_highest_call_oi: null,
  strike_highest_put_oi: null,
});

/** NIFTY index / strike style values in the volatility widget (whole points). */
function formatNiftyIndexInt(v: number | null | undefined): string {
  if (typeof v !== "number" || !Number.isFinite(v)) return "—";
  return Math.round(v).toLocaleString("en-IN");
}

/** NIFTY spot headline value (2 decimals), distinct from the whole-point range formatting above. */
function formatNiftySpotDecimal(v: number | null | undefined): string {
  if (typeof v !== "number" || !Number.isFinite(v)) return "—";
  return v.toLocaleString("en-IN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

/** Exact absolute change from a percent-trend field: prev = current/(1+pct/100). */
function absoluteChangeFromPct(
  current: number | null | undefined,
  pct: number | null | undefined,
): number | null {
  if (typeof current !== "number" || !Number.isFinite(current)) return null;
  if (typeof pct !== "number" || !Number.isFinite(pct)) return null;
  const denom = 1 + pct / 100;
  if (denom === 0) return null;
  const prev = current / denom;
  return current - prev;
}

/** 2σ range from the existing 1σ (lognormal) range: doubling the exponent squares the ratio to spot. */
function twoSigmaRange(
  expectedRange: [number, number] | null | undefined,
  spot: number | null | undefined,
): [number, number] | null {
  if (!Array.isArray(expectedRange) || expectedRange.length !== 2) return null;
  if (typeof spot !== "number" || !Number.isFinite(spot) || spot <= 0) return null;
  const [lower1, upper1] = expectedRange;
  if (typeof lower1 !== "number" || typeof upper1 !== "number") return null;
  return [(lower1 * lower1) / spot, (upper1 * upper1) / spot];
}

/** "DD-Mon-YYYY" -> "DD Mon" (drop year, dash -> space). */
function formatExpiryShort(expiry: string | null | undefined): string | null {
  if (!expiry) return null;
  const parts = expiry.split("-");
  if (parts.length < 2) return expiry;
  return `${parts[0]} ${parts[1]}`;
}

/** Compact ₹ with a "+" prefix for positive P&L tiles (short "L"/"Cr" suffix, matching the Terminal design). */
function formatSignedMoneyShort(amount: number): string {
  const formatted = formatIndianMoneyCompact(amount, { shortSuffix: true });
  return amount > 0 ? `+${formatted}` : formatted;
}

function formatDashboardTimestamp(ms: number | undefined): string {
  if (!ms) return "—";
  const d = new Date(ms);
  const datePart = d.toLocaleDateString("en-IN", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    timeZone: "Asia/Kolkata",
  });
  const timePart = d.toLocaleTimeString("en-IN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: "Asia/Kolkata",
  });
  return `${datePart}, ${timePart} IST`;
}

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

function useLazySection(rootMargin = "200px"): [boolean, (node: Element | null) => void] {
  const [enabled, setEnabled] = useState(false);

  const setRef = useCallback(
    (node: Element | null) => {
      if (!node || enabled) return;
      if (typeof window === "undefined" || typeof IntersectionObserver === "undefined") {
        setEnabled(true);
        return;
      }
      const observer = new IntersectionObserver(
        (entries) => {
          if (entries.some((entry) => entry.isIntersecting)) {
            setEnabled(true);
            observer.disconnect();
          }
        },
        { rootMargin },
      );
      observer.observe(node);
    },
    [enabled, rootMargin],
  );

  return [enabled, setRef];
}

export default function DashboardPage() {
  const queryClient = useQueryClient();
  const [marketEnabled, marketTriggerRef] = useLazySection("300px");
  const [chartEnabled, chartTriggerRef] = useLazySection("0px");

  const bootstrapQ = useQuery({
    queryKey: ["dashboard", "bootstrap"],
    queryFn: fetchDashboardBootstrap,
    staleTime: 30_000,
  });

  // NIFTY/SENSEX system chains may still be warming up right after login; show a
  // brief connection overlay instead of racing widgets against a cold WS subscribe.
  // Capped at 10s so a stuck/failed prefetch never blocks the page -- the
  // dashboard's own data hooks below already wait correctly on chain readiness
  // server-side regardless, so this is purely cosmetic once it steps aside.
  const wsHealthQ = useWsHealth();
  const [marketConnectionGateExpired, setMarketConnectionGateExpired] = useState(false);
  useEffect(() => {
    const id = setTimeout(() => setMarketConnectionGateExpired(true), 10_000);
    return () => clearTimeout(id);
  }, []);
  const showMarketConnectionOverlay =
    !marketConnectionGateExpired &&
    wsHealthQ.data?.market_open === true &&
    wsHealthQ.data?.status === "gray";

  useEffect(() => {
    if (!bootstrapQ.data) return;
    hydrateDashboardQueryCache(queryClient, bootstrapQ.data);
  }, [bootstrapQ.data, queryClient]);

  const homeQ = useQuery({
    queryKey: ["home", "data"],
    queryFn: () => apiClient.get<HomeDataResponse>("/home/data"),
    staleTime: 30_000,
    enabled: false,
  });

  const bootstrapPortfolio = bootstrapQ.data?.portfolio;
  const portfolioBootstrapFailed = Boolean(
    bootstrapPortfolio &&
      bootstrapPortfolio.Status != null &&
      bootstrapPortfolio.Status !== 200,
  );

  const portQ = useQuery({
    queryKey: ["portfolio", "positions"],
    queryFn: () => apiClient.get<PortfolioApiResponse>("/portfolio/data"),
    staleTime: 30_000,
    enabled: portfolioBootstrapFailed,
    retry: 1,
  });

  const coreQ = useQuery({
    queryKey: ["dashboard", "vix"],
    queryFn: () => apiClient.get<DashboardVixCore>("/dashboard/vix"),
    enabled: false,
  });

  const optsQ = useQuery({
    queryKey: ["dashboard", "vix-options"],
    queryFn: async () => {
      try {
        return await fetchDashboardVixOptions();
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
    staleTime: 30_000,
    enabled: Boolean(bootstrapQ.data),
  });

  const historyQ = useQuery({
    queryKey: ["dashboard", "vix-history"],
    queryFn: fetchDashboardVixHistory,
    staleTime: 30_000,
    enabled: chartEnabled && Boolean(bootstrapQ.data),
  });

  const marketOutlookQ = useQuery({
    queryKey: ["dashboard", "market-outlook"],
    queryFn: () => getMarketOutlook(false),
    staleTime: 120_000,
    enabled: marketEnabled,
    retry: false,
  });

  const [outlookShowUpdatedBadge, setOutlookShowUpdatedBadge] = useState(false);
  const outlookUpdatedTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (outlookUpdatedTimerRef.current) {
        clearTimeout(outlookUpdatedTimerRef.current);
        outlookUpdatedTimerRef.current = null;
      }
    };
  }, []);

  const refreshOutlookM = useMutation({
    mutationFn: async () => {
      const market = await getMarketOutlook(true);
      return { market };
    },
    onSuccess: (data) => {
      queryClient.setQueryData<OutlookResponse>(["dashboard", "market-outlook"], data.market);
      if (outlookUpdatedTimerRef.current) clearTimeout(outlookUpdatedTimerRef.current);
      setOutlookShowUpdatedBadge(true);
      outlookUpdatedTimerRef.current = setTimeout(() => {
        setOutlookShowUpdatedBadge(false);
        outlookUpdatedTimerRef.current = null;
      }, 12_000);
    },
  });

  const marketOutlookBadgePhase = useMemo((): MarketOutlookBadgePhase => {
    if (!marketEnabled) return "idle";
    const noCacheYet =
      marketOutlookQ.data?.warning?.error_code === "no_cached_outlook";
    const hasData = Boolean(marketOutlookQ.data);
    const loading =
      !hasData && (marketOutlookQ.isPending || refreshOutlookM.isPending);
    if (loading) return "loading";
    if (!hasData) return "unavailable";
    if (noCacheYet) return "unavailable";
    if (outlookShowUpdatedBadge) return "updated";
    return "cached";
  }, [
    marketEnabled,
    marketOutlookQ.data,
    marketOutlookQ.data?.warning?.error_code,
    marketOutlookQ.isPending,
    refreshOutlookM.isPending,
    outlookShowUpdatedBadge,
  ]);

  const marketOutlookHeaderAlert = useMemo(() => {
    if (!marketEnabled) return null;
    const loadError = outlookQueryLoadError(marketOutlookQ.error, marketOutlookQ.isError);
    return buildMarketOutlookHeaderAlert({
      data: marketOutlookQ.data,
      loadError,
      refreshError: refreshOutlookM.error as Error | null,
    });
  }, [
    marketEnabled,
    marketOutlookQ.data,
    marketOutlookQ.error,
    marketOutlookQ.isError,
    refreshOutlookM.error,
  ]);

  const homeData = bootstrapQ.isPending
    ? undefined
    : (bootstrapQ.data?.home ?? homeQ.data);
  const portData = bootstrapQ.isPending
    ? undefined
    : (portQ.data ?? bootstrapPortfolio);
  const portfolioStillRetrying =
    portfolioBootstrapFailed && (portQ.isPending || portQ.isFetching);
  const coreBase = bootstrapQ.data?.vix ?? coreQ.data;
  const opts = optsQ.data;
  const vixSeries = useMemo(
    () => historyQ.data?.vix_30d ?? coreBase?.vix_30d ?? [],
    [historyQ.data?.vix_30d, coreBase?.vix_30d],
  );
  const core = coreBase
    ? { ...coreBase, vix_30d: vixSeries }
    : undefined;

  const { funds, marginUsed: marginUsedFromHome } = getHomeMarginTiles(
    homeData?.margin,
  );
  const marginUsedFromPositions = useMemo(
    () => sumMarginUsedFromPositions(portData),
    [portData],
  );
  // Home often reports margin used as 0 even with F&O risk; prefer positions when home is 0/null.
  const marginUsedDisplay =
    marginUsedFromHome != null && marginUsedFromHome > 0
      ? marginUsedFromHome
      : portData?.Status === 200 && marginUsedFromPositions != null
        ? marginUsedFromPositions
        : marginUsedFromHome ?? null;

  const openPnl = useMemo(() => sumOpenPositionsPnl(portData), [portData]);

  const dashboardWarnings = useMemo(() => {
    if (bootstrapQ.isPending || portfolioStillRetrying) {
      return [];
    }
    const w: string[] = [];
    const h = homeData;
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
    const pd = portData;
    if (pd && pd.Status != null && pd.Status !== 200) {
      w.push("Positions could not be loaded.");
    }
    return w;
  }, [bootstrapQ.isPending, homeData, portData, portfolioStillRetrying]);

  const openPositionCount =
    portData?.Status === 200
      ? (portData.Success?.positions?.length ?? 0)
      : null;

  // Prefer spot from /dashboard/vix (same request as VIX) so NIFTY shows immediately; opts may trail.
  const niftySpot =
    typeof core?.nifty_spot === "number"
      ? core.nifty_spot
      : typeof opts?.nifty_spot === "number"
        ? opts.nifty_spot
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

  // IV / OI / PCR come from /dashboard/vix/options (slow); do not block VIX chart or NIFTY spot from core.
  const optsLoading =
    optsQ.isPending || (optsQ.isFetching && !optsQ.data);

  const accountLoading = bootstrapQ.isPending || portfolioStillRetrying;
  const volatilityCoreLoading = bootstrapQ.isPending;
  const vixHistoryLoading =
    chartEnabled &&
    Boolean(bootstrapQ.data) &&
    (historyQ.isPending || historyQ.isFetching) &&
    (historyQ.data?.vix_30d?.length ?? 0) === 0;

  const volatilityFetching =
    bootstrapQ.isFetching || optsQ.isFetching || historyQ.isFetching;
  const refreshDashboard = useCallback(() => {
    void Promise.all([
      queryClient.invalidateQueries({ queryKey: ["dashboard", "bootstrap"] }),
      queryClient.invalidateQueries({ queryKey: ["dashboard", "vix-options"] }),
      queryClient.invalidateQueries({ queryKey: ["dashboard", "vix-history"] }),
    ]);
  }, [queryClient]);

  const daysPnl = bootstrapQ.data?.days_pnl;
  const marginUsedPct =
    marginUsedDisplay != null && funds != null && marginUsedDisplay + funds > 0
      ? Math.min(100, Math.max(0, (marginUsedDisplay / (marginUsedDisplay + funds)) * 100))
      : null;
  const niftyChangeAbs = absoluteChangeFromPct(niftySpot, core?.nifty_spot_trend_pct);
  const vixChangeAbs = absoluteChangeFromPct(core?.current_vix, core?.vix_trend_pct);
  const expectedRange2Sigma = twoSigmaRange(opts?.expected_range, niftySpot);
  const expectedMove2SigmaPct =
    typeof opts?.expected_move_pct === "number" ? opts.expected_move_pct * 2 : null;
  const expectedMove2SigmaPts =
    expectedRange2Sigma != null
      ? Math.round((expectedRange2Sigma[1] - expectedRange2Sigma[0]) / 2)
      : null;
  const nextExpiryShort = formatExpiryShort(opts?.next_expiry);
  const oiCallStrike =
    typeof opts?.strike_highest_call_oi === "number"
      ? Math.round(opts.strike_highest_call_oi)
      : null;
  const oiPutStrike =
    typeof opts?.strike_highest_put_oi === "number"
      ? Math.round(opts.strike_highest_put_oi)
      : null;
  const oiRangeLo =
    oiCallStrike != null && oiPutStrike != null
      ? Math.min(oiCallStrike, oiPutStrike)
      : (oiCallStrike ?? oiPutStrike);
  const oiRangeHi =
    oiCallStrike != null && oiPutStrike != null
      ? Math.max(oiCallStrike, oiPutStrike)
      : null;
  const oiRangeHalfWidthPts =
    oiRangeLo != null && oiRangeHi != null ? (oiRangeHi - oiRangeLo) / 2 : null;
  const oiRangeHalfWidthPct =
    oiRangeHalfWidthPts != null &&
    typeof niftySpot === "number" &&
    Number.isFinite(niftySpot) &&
    niftySpot > 0
      ? (oiRangeHalfWidthPts / niftySpot) * 100
      : null;
  const vix30dSlice = useMemo(() => {
    if (vixSeries.length === 0) return vixSeries;
    const lastDate = new Date(vixSeries[vixSeries.length - 1].date);
    const cutoff = new Date(lastDate);
    cutoff.setDate(cutoff.getDate() - 29);
    return vixSeries.filter((p) => new Date(p.date) >= cutoff);
  }, [vixSeries]);

  return (
    <AppShell>
      {showMarketConnectionOverlay ? <MarketConnectionOverlay /> : null}
      <div className="mx-auto max-w-[1200px] space-y-4">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="app-text-title">Dashboard</h1>
            <p className="mt-0.5 text-xs app-text-muted">
              Snapshot ·{" "}
              <span className="font-mono">
                {formatDashboardTimestamp(bootstrapQ.dataUpdatedAt)}
              </span>
            </p>
          </div>
          <button
            type="button"
            onClick={refreshDashboard}
            disabled={volatilityFetching}
            className="inline-flex items-center justify-center gap-1.5 rounded-lg bg-transparent px-3.5 py-1.5 font-semibold uppercase tracking-wide text-accent-strong transition hover:bg-border-soft disabled:pointer-events-none disabled:opacity-60 text-micro"
          >
            <VolatilityRefreshIcon spinning={volatilityFetching} />
            Refresh
          </button>
        </div>

        {dashboardWarnings.length > 0 ? (
          <div
            className="app-card border-amber-accent/40 bg-amber-tint p-3 text-sm text-amber-accent"
            role="alert"
          >
            <strong className="font-medium">
              Something went wrong loading some data.
            </strong>{" "}
            {dashboardWarnings.join(" ")} If this persists or you see invalid
            customer or session messages, try{" "}
            <a href="/logout" className="font-medium underline">
              logging out
            </a>{" "}
            and logging back in.
          </div>
        ) : null}

        {bootstrapQ.error ? (
          <div className="app-alert-error text-sm">
            Unable to load dashboard data:{" "}
            {bootstrapQ.error instanceof Error
              ? bootstrapQ.error.message
              : "Unknown error"}
          </div>
        ) : null}

        <div className="grid gap-3 sm:grid-cols-2 md:grid-cols-4">
          <MetricTile
            label="Open P&L"
            loading={accountLoading}
            value={
              bootstrapQ.isError || openPnl == null
                ? null
                : formatSignedMoneyShort(openPnl)
            }
            toneClassName={openPnl != null ? moneyToneClass(openPnl) : undefined}
            caption={
              openPositionCount != null
                ? `${openPositionCount} open ${openPositionCount === 1 ? "position" : "positions"}`
                : undefined
            }
          />
          <MetricTile
            label="Day's P&L"
            loading={accountLoading}
            value={
              daysPnl?.total_day_pnl == null
                ? null
                : formatSignedMoneyShort(daysPnl.total_day_pnl)
            }
            toneClassName={
              daysPnl?.total_day_pnl != null
                ? moneyToneClass(daysPnl.total_day_pnl)
                : undefined
            }
            caption="vs previous close"
          />
          <MetricTile
            label="Margin used"
            loading={accountLoading}
            value={
              marginUsedDisplay == null
                ? null
                : formatIndianMoneyCompact(marginUsedDisplay, { shortSuffix: true })
            }
            caption={
              marginUsedPct != null ? <ProgressBar pct={marginUsedPct} /> : undefined
            }
          />
          <MetricTile
            label="Free margin"
            loading={accountLoading}
            value={
              funds == null ? null : formatIndianMoneyCompact(funds, { shortSuffix: true })
            }
            caption="Cash + collateral"
          />
        </div>

        <div className="grid gap-3 md:grid-cols-2">
          <section className="app-card min-w-0 overflow-hidden">
            <header className="flex flex-wrap items-baseline justify-between gap-2 border-b border-border-soft p-3">
              <div className="flex min-w-0 flex-wrap items-baseline gap-2">
                <h2 className="text-lg font-semibold">NIFTY 50</h2>
                {volatilityCoreLoading ? (
                  <DashboardMetricSkeleton className="w-20" />
                ) : (
                  <span className="font-mono text-lg font-semibold tabular-nums text-foreground">
                    {formatNiftySpotDecimal(niftySpot)}
                  </span>
                )}
                {!volatilityCoreLoading && niftyChangeAbs != null ? (
                  <span
                    className={[
                      "font-mono text-xs font-medium tabular-nums",
                      moneyToneClass(niftyChangeAbs),
                    ].join(" ")}
                  >
                    {niftyChangeAbs >= 0 ? "+" : ""}
                    {niftyChangeAbs.toFixed(2)}
                  </span>
                ) : null}
                {!volatilityCoreLoading &&
                typeof core?.nifty_spot_trend_pct === "number" ? (
                  <span className="app-text-muted text-xs">
                    · {core.nifty_spot_trend_pct >= 0 ? "+" : ""}
                    {core.nifty_spot_trend_pct.toFixed(2)}%
                  </span>
                ) : null}
              </div>
              <span className="app-text-muted shrink-0 rounded-full border border-border px-2 py-0.5 font-mono text-heading">
                {optsLoading ? (
                  <span className="app-skeleton inline-block h-3 w-14 rounded-sm border-0" />
                ) : nextExpiryShort ? (
                  `Exp ${nextExpiryShort}`
                ) : (
                  "—"
                )}
              </span>
            </header>
            {bootstrapQ.error ? (
              <div className="app-alert-error m-3 text-sm">
                Unable to load volatility data:{" "}
                {bootstrapQ.error instanceof Error
                  ? bootstrapQ.error.message
                  : "Unknown error"}
              </div>
            ) : (
              <div className="divide-y divide-border-soft text-sm">
                <div className="grid divide-border-soft sm:grid-cols-2 sm:divide-x">
                  <div className="p-3">
                    <div className="text-heading uppercase tracking-wide text-faint">
                      ATM IV
                    </div>
                    <div className="mt-1 font-mono text-lg font-semibold text-foreground">
                      {optsLoading ? (
                        <DashboardMetricSkeleton className="w-20" />
                      ) : typeof opts?.atm_iv === "number" ? (
                        `${opts.atm_iv.toFixed(1)}%`
                      ) : (
                        "—"
                      )}
                    </div>
                    {!optsLoading && ivInterp ? (
                      <div className="mt-1">
                        <InterpretationBadge
                          label={ivInterp.label}
                          tooltip={ivInterp.tooltip}
                          tone={ivInterp.tone}
                        />
                      </div>
                    ) : null}
                  </div>

                  <div className="p-3">
                    <div className="text-heading uppercase tracking-wide text-faint">
                      PCR (OI)
                    </div>
                    <div className="mt-1 font-mono text-lg font-semibold text-foreground">
                      {optsLoading ? (
                        <DashboardMetricSkeleton className="w-14" />
                      ) : typeof opts?.put_call_ratio === "number" ? (
                        opts.put_call_ratio.toFixed(2)
                      ) : (
                        "—"
                      )}
                    </div>
                    {!optsLoading && pcrInterp ? (
                      <div className="mt-1">
                        <InterpretationBadge
                          label={pcrInterp.label}
                          tooltip={pcrInterp.tooltip}
                          tone={pcrInterp.tone}
                        />
                      </div>
                    ) : null}
                  </div>
                </div>

                <div className="p-3">
                  <div className="text-heading uppercase tracking-wide text-faint">
                    Expected range (based on 2σ)
                  </div>
                  <div className="font-mono text-lg font-semibold text-foreground">
                    {optsLoading ? (
                      <DashboardMetricSkeleton className="w-36" />
                    ) : expectedRange2Sigma ? (
                      <>
                        {formatNiftyIndexInt(expectedRange2Sigma[0])} –{" "}
                        {formatNiftyIndexInt(expectedRange2Sigma[1])}
                        {expectedMove2SigmaPct != null ? (
                          <span className="mt-0.5 block text-body font-normal text-faint">
                            ±{expectedMove2SigmaPct.toFixed(1)}%
                            {expectedMove2SigmaPts != null
                              ? ` · ±${expectedMove2SigmaPts.toLocaleString("en-IN")} pts`
                              : null}
                          </span>
                        ) : null}
                      </>
                    ) : (
                      "—"
                    )}
                  </div>
                </div>

                <div className="p-3">
                  <div
                    className="text-heading uppercase tracking-wide text-faint"
                    title="From lowest to highest of the strikes with max call OI and max put OI (nearest expiry)"
                  >
                    Expected range (based on highest OI)
                  </div>
                  <div className="font-mono text-lg font-semibold text-foreground">
                    {optsLoading ? (
                      <DashboardMetricSkeleton className="w-36" />
                    ) : oiRangeLo != null ? (
                      <>
                        {oiRangeLo.toLocaleString("en-IN")}
                        {oiRangeHi != null
                          ? ` – ${oiRangeHi.toLocaleString("en-IN")}`
                          : null}
                        {oiRangeHalfWidthPct != null ? (
                          <span className="mt-0.5 block text-body font-normal text-faint">
                            ±{oiRangeHalfWidthPct.toFixed(1)}%
                            {oiRangeHalfWidthPts != null
                              ? ` · ±${Math.round(oiRangeHalfWidthPts).toLocaleString("en-IN")} pts`
                              : null}
                          </span>
                        ) : null}
                      </>
                    ) : (
                      "—"
                    )}
                  </div>
                </div>
              </div>
            )}
            {opts?.error ? (
              <p className="px-3 pb-3 text-heading text-amber-accent">
                IV / PCR: {opts.error}
              </p>
            ) : null}
          </section>

          <section className="app-card min-w-0 space-y-3 p-3">
            <header className="flex items-center justify-between gap-2">
              <h2 className="app-text-heading">India VIX</h2>
              <span className="app-text-muted text-heading">
                30-day
              </span>
            </header>
            {bootstrapQ.error ? (
              <div className="app-alert-error text-sm">
                Unable to load volatility data:{" "}
                {bootstrapQ.error instanceof Error
                  ? bootstrapQ.error.message
                  : "Unknown error"}
              </div>
            ) : (
              <>
                <div className="flex flex-wrap items-baseline gap-2">
                  {volatilityCoreLoading ? (
                    <DashboardMetricSkeleton className="w-16" />
                  ) : (
                    <span className="font-mono text-2xl font-semibold tabular-nums text-foreground">
                      {typeof core?.current_vix === "number"
                        ? core.current_vix.toFixed(2)
                        : "—"}
                    </span>
                  )}
                  {!volatilityCoreLoading && vixChangeAbs != null ? (
                    <span
                      className={[
                        "font-mono text-sm font-medium tabular-nums",
                        moneyToneClass(vixChangeAbs),
                      ].join(" ")}
                    >
                      {vixChangeAbs >= 0 ? "+" : ""}
                      {vixChangeAbs.toFixed(2)}
                    </span>
                  ) : null}
                </div>
                {!volatilityCoreLoading && vixInterp ? (
                  <InterpretationBadge
                    label={vixInterp.label}
                    tooltip={vixInterp.tooltip}
                    tone={vixInterp.tone}
                  />
                ) : null}
                <div ref={chartTriggerRef} aria-hidden className="h-px w-full" />
                {!chartEnabled ? (
                  <div className="text-sm app-text-muted">
                    Load chart when visible...
                  </div>
                ) : (
                  <Vix30dChart series={vix30dSlice} loading={vixHistoryLoading} compact />
                )}
              </>
            )}
            {core?.error ? (
              <p className="text-heading text-amber-accent">
                VIX: {core.error}
              </p>
            ) : null}
          </section>
        </div>

        <div ref={marketTriggerRef} aria-hidden className="h-px w-full" />
        <section className="app-card min-w-0 space-y-3 p-4">
          <header className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <span className="flex size-7 shrink-0 items-center justify-center rounded-lg bg-accent-tint">
                <SparkleIcon />
              </span>
              <h2 className="app-text-heading">AI Market Outlook</h2>
              <MarketOutlookConnectionBadge
                phase={marketOutlookBadgePhase}
                asOf={marketOutlookQ.data?.as_of}
              />
            </div>
            <button
              type="button"
              disabled={refreshOutlookM.isPending}
              className="inline-flex items-center justify-center gap-1.5 rounded-lg bg-transparent px-3.5 py-1.5 font-semibold uppercase tracking-wide transition hover:bg-border-soft disabled:pointer-events-none disabled:opacity-60 text-micro text-muted"
              onClick={() => refreshOutlookM.mutate()}
            >
              <OutlookRefreshIcon spinning={refreshOutlookM.isPending} />
              Regenerate
            </button>
          </header>
          {marketOutlookHeaderAlert ? (
            <div
              className={[
                "text-xs",
                marketOutlookHeaderAlert.tone === "warning"
                  ? "text-amber-accent"
                  : "text-down",
              ].join(" ")}
              role="alert"
            >
              {marketOutlookHeaderAlert.text}
            </div>
          ) : null}
          {!marketEnabled ? (
            <div className="text-xs app-text-muted">Load when visible...</div>
          ) : (
            <OutlookParagraph
              data={marketOutlookQ.data}
              pendingInitial={
                !marketOutlookQ.data &&
                (marketOutlookQ.isPending || refreshOutlookM.isPending)
              }
              loadError={outlookQueryLoadError(
                marketOutlookQ.error,
                marketOutlookQ.isError,
              )}
              refreshError={refreshOutlookM.error as Error | null}
            />
          )}
        </section>
      </div>
    </AppShell>
  );
}

function MetricTile({
  label,
  loading,
  value,
  toneClassName,
  caption,
}: {
  label: string;
  loading: boolean;
  value: string | null;
  toneClassName?: string;
  caption?: ReactNode;
}) {
  return (
    <div className="app-card p-[14px_15px]">
      <div className="text-heading uppercase tracking-wide text-faint">
        {label}
      </div>
      <div
        className={[
          "mt-1.5 font-mono text-[25px] font-semibold tabular-nums",
          loading
            ? "text-faint"
            : (toneClassName ?? "text-foreground"),
        ].join(" ")}
      >
        {loading ? <DashboardMetricSkeleton /> : (value ?? "—")}
      </div>
      {caption ? (
        <div className="mt-1.5 text-heading app-text-muted">{caption}</div>
      ) : null}
    </div>
  );
}

function ProgressBar({ pct }: { pct: number }) {
  const clamped = Math.min(100, Math.max(0, pct));
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-full min-w-[3rem] overflow-hidden rounded-full bg-track">
        <div
          className="h-full rounded-full bg-accent-strong"
          style={{ width: `${clamped}%` }}
        />
      </div>
      <span className="font-mono text-heading tabular-nums app-text-muted">
        {clamped.toFixed(0)}%
      </span>
    </div>
  );
}

function SparkleIcon() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      className="text-accent-strong"
    >
      <path d="M12 3v4M12 17v4M3 12h4M17 12h4" />
      <path d="M6.3 6.3l2.1 2.1M15.6 15.6l2.1 2.1M17.7 6.3l-2.1 2.1M8.4 15.6l-2.1 2.1" />
    </svg>
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

function outlookQueryLoadError(error: unknown, isError: boolean): Error | null {
  if (!isError) return null;
  return error instanceof Error ? error : new Error("Could not load market outlook.");
}

function OutlookRefreshIcon({ spinning }: { spinning: boolean }) {
  return (
    <svg
      width="14"
      height="14"
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

function formatTimeOnly(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleTimeString("en-IN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: "Asia/Kolkata",
  });
}

function MarketOutlookConnectionBadge({
  phase,
  asOf,
}: {
  phase: MarketOutlookBadgePhase;
  asOf?: string | null;
}) {
  if (phase === "idle") {
    return (
      <span className="rounded-full border border-border px-2 py-0.5 text-body uppercase tracking-wide text-faint">
        idle
      </span>
    );
  }

  const time = formatTimeOnly(asOf);
  const cfg =
    phase === "loading"
      ? {
          label: "loading",
          cls: "border-border bg-panel2 text-faint",
        }
      : phase === "updated"
        ? {
            label: time ? `updated · ${time}` : "updated",
            cls: "border-up/40 bg-up-tint text-up",
          }
        : phase === "cached"
          ? {
              label: time ? `cached · ${time}` : "cached",
              cls: "border-border bg-panel2 text-faint",
            }
          : {
              label: "not connected",
              cls: "border-border bg-panel2 text-faint",
            };

  return (
    <span
      className={`rounded-full border px-2 py-0.5 text-body uppercase tracking-wide ${cfg.cls}`}
    >
      {cfg.label}
    </span>
  );
}

function formatOutlookWarning(warning: NonNullable<OutlookResponse["warning"]>): string {
  const code = warning.error_code ? `[${warning.error_code}] ` : "";
  const upstream =
    typeof warning.upstream_status === "number"
      ? ` (upstream HTTP ${warning.upstream_status})`
      : "";
  return `${code}${warning.message}${upstream}`;
}

function formatOutlookProviderError(error: Error | null | undefined): string {
  if (!error) return "";
  if (!(error instanceof ApiHttpError)) return error.message || "Unknown error";

  const payload = error.payload as
    | { detail?: { message?: string; error_code?: string } | string }
    | undefined;
  const detail = payload?.detail;
  if (detail && typeof detail === "object") {
    const code = typeof detail.error_code === "string" ? detail.error_code : null;
    const message = typeof detail.message === "string" ? detail.message : error.message;
    const http = Number.isFinite(error.status) ? ` (HTTP ${error.status})` : "";
    return `${code ? `[${code}] ` : ""}${message}${http}`;
  }
  const http = Number.isFinite(error.status) ? ` (HTTP ${error.status})` : "";
  return `${error.message}${http}`;
}

type MarketOutlookHeaderAlert = { text: string; tone: "warning" | "error" };

function buildMarketOutlookHeaderAlert(opts: {
  data?: OutlookResponse;
  loadError: Error | null;
  refreshError: Error | null;
}): MarketOutlookHeaderAlert | null {
  const { data, loadError, refreshError } = opts;
  const errText = loadError ? outlookFetchErrorMessage(loadError) : null;
  const refreshMsg = refreshError ? formatOutlookProviderError(refreshError) : "";

  if (data?.warning) {
    let text = formatOutlookWarning(data.warning);
    if (data.warning.stale_response_served) {
      text = `${text} This outlook may be outdated; it is from cache because the latest refresh failed.`;
    }
    return { text, tone: "warning" };
  }

  if (data && (errText || refreshMsg)) {
    const parts: string[] = [];
    if (errText) parts.push(errText);
    if (refreshMsg) parts.push(`Refresh failed: ${refreshMsg}`);
    let text = parts.join(" ");
    if (loadError) {
      text = `${text} The outlook below may be from an earlier load.`;
    } else if (refreshError) {
      text = `${text} The outlook below is what was loaded before refresh failed.`;
    }
    return { text, tone: "error" };
  }

  if (!data && (errText || refreshMsg)) {
    const parts: string[] = [];
    if (errText) parts.push(errText);
    if (refreshMsg) parts.push(refreshMsg);
    return { text: parts.join(" "), tone: "error" };
  }

  return null;
}

const OUTLOOK_NUMBER_TOKEN =
  /(\d{1,3}(?:,\d{2,3})*(?:\.\d+)?%?|\d{1,2} (?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec))/g;

/** Bold numeric/date tokens (prices, percentages, strikes, expiry dates) within a line of text. */
function renderOutlookText(text: string): ReactNode[] {
  return text.split(OUTLOOK_NUMBER_TOKEN).map((chunk, i) =>
    i % 2 === 1 ? (
      <strong key={i} className="font-semibold text-foreground">
        {chunk}
      </strong>
    ) : (
      <span key={i}>{chunk}</span>
    ),
  );
}

const OUTLOOK_CONFIDENCE_BADGE_CLS: Record<string, string> = {
  low: "border-border bg-panel2 text-faint",
  medium: "border-amber-accent/40 bg-amber-tint text-amber-accent",
  high: "border-up/40 bg-up-tint text-up",
};

function OutlookConfidenceBadge({ confidence }: { confidence?: string }) {
  if (!confidence) return null;
  const cls = OUTLOOK_CONFIDENCE_BADGE_CLS[confidence] ?? OUTLOOK_CONFIDENCE_BADGE_CLS.low;
  return (
    <span className={`rounded-full border px-2 py-0.5 text-micro uppercase tracking-wide ${cls}`}>
      {confidence} confidence
    </span>
  );
}

const OUTLOOK_SUMMARY_SECTIONS: { category: OutlookSummaryCategory; label: string }[] = [
  { category: "macro_global", label: "Global" },
  { category: "macro_local", label: "Domestic" },
  { category: "positioning", label: "Positioning" },
];

/** Full structured render of the outlook payload: summary, inference, strategy ideas, sources. */
function OutlookContent({ data }: { data: OutlookResponse }) {
  const inference = data.inference;
  const strategyIdeas = data.strategy_ideas ?? [];
  const sources = data.sources ?? [];
  return (
    <div className="space-y-3">
      {data.summary.length > 0
        ? OUTLOOK_SUMMARY_SECTIONS.map(({ category, label }) => {
            const items = data.summary.filter((item) => item.category === category);
            if (items.length === 0) return null;
            return (
              <div key={category}>
                <div className="text-xs font-semibold uppercase tracking-wide text-muted">{label}</div>
                <ul className="list-disc space-y-1 pl-5 text-sm leading-relaxed text-foreground">
                  {items.map((item, i) => (
                    <li key={i}>{renderOutlookText(item.text)}</li>
                  ))}
                </ul>
              </div>
            );
          })
        : null}

      {inference ? (
        <div className="space-y-1.5 rounded-lg border border-border-soft bg-panel2 p-3">
          <div className="flex flex-wrap items-center gap-2">
            {inference.volatility_view ? (
              <span className="text-xs text-muted">
                Volatility:{" "}
                <span className="text-foreground">{renderOutlookText(inference.volatility_view)}</span>
              </span>
            ) : null}
            <OutlookConfidenceBadge confidence={inference.confidence} />
          </div>
          {inference.movement_scenarios?.length ? (
            <ul className="list-disc space-y-0.5 pl-5 text-xs text-muted">
              {inference.movement_scenarios.map((s, i) => (
                <li key={i}>{renderOutlookText(s)}</li>
              ))}
            </ul>
          ) : null}
          {inference.caveats?.length ? (
            <p className="text-xs text-faint italic">{inference.caveats.join(" · ")}</p>
          ) : null}
        </div>
      ) : null}

      {strategyIdeas.length > 0 ? (
        <ul className="space-y-1.5">
          {strategyIdeas.map((idea, i) => (
            <li key={i} className="rounded-lg border border-border-soft bg-panel2 p-3 text-xs">
              <span className="font-semibold uppercase tracking-wide text-foreground">{idea.tag}</span>
              <p className="mt-0.5 text-muted">{renderOutlookText(idea.rationale)}</p>
              {idea.risk_note ? (
                <p className="mt-0.5 text-faint italic">Risk: {idea.risk_note}</p>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}

      {sources.length > 0 ? (
        <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs">
          {sources.map((s, i) => (
            <a
              key={i}
              href={s.url}
              target="_blank"
              rel="noopener noreferrer"
              className="app-link"
              title={s.publisher}
            >
              {s.title}
            </a>
          ))}
        </div>
      ) : null}

      {data.disclaimer ? <p className="text-micro text-faint">{data.disclaimer}</p> : null}
    </div>
  );
}

function OutlookParagraph({
  data,
  pendingInitial,
  loadError,
  refreshError,
}: {
  data?: OutlookResponse;
  pendingInitial: boolean;
  loadError?: Error | null;
  refreshError?: Error | null;
}) {
  const errText = loadError ? outlookFetchErrorMessage(loadError) : null;
  const refreshMsg = refreshError ? formatOutlookProviderError(refreshError) : "";
  if (!data) {
    if (errText || refreshMsg) {
      return null;
    }
    if (pendingInitial) {
      return <div className="text-sm app-text-muted">Loading market outlook...</div>;
    }
    return <div className="text-sm app-text-muted">No outlook available.</div>;
  }
  if (data.warning?.error_code === "no_cached_outlook" && data.summary.length === 0) {
    return (
      <div className="text-sm app-text-muted">
        No outlook in this session yet. Use regenerate above to generate one (calls your AI
        provider).
      </div>
    );
  }
  return <OutlookContent data={data} />;
}
