"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { AppShell } from "@/components/layout/AppShell";
import { RevokedTradingPageGuard } from "@/components/license/RevokedTradingPageGuard";
import { OptionChainUnderlyingSearch } from "@/components/order/OptionChainUnderlyingSearch";
import { OrderExecutionConfirmDialog } from "@/components/order/OrderExecutionConfirmDialog";
import { RateLimitPauseOverlay } from "@/components/order/RateLimitPauseOverlay";
import { ExpirySelectPill } from "@/components/strategy-builder/ExpirySelectPill";
import { ProposedStrategyTradeCard } from "@/components/strategy-builder/ProposedStrategyTradeCard";
import {
  StrategyLegsPanel,
  type LegMarginEntry,
} from "@/components/strategy-builder/StrategyLegsPanel";
import { StrategyPayoffPanel } from "@/components/strategy-builder/StrategyPayoffPanel";
import { apiClient } from "@/lib/api-client";
import { proposeTrades } from "@/lib/strategy-builder/api";
import { sortExpiryDatesAsc } from "@/lib/strategy-builder/expiry";
import {
  legsQtySignature,
  parseSpanMarginFromResponse,
} from "@/lib/strategy-builder/leg-ui-helpers";
import { proposedLegsToStrategyLegs } from "@/lib/strategy-builder/map-proposed-legs";
import { sb } from "@/lib/strategy-builder/ui";
import type {
  MarginApiResponse,
  ProposedTrade,
  ProposeTradesSuccess,
  StrategyLeg,
  UnderlyingsApiResponse,
} from "@/lib/strategy-builder/types";
import { useBreakChunkQty } from "@/lib/use-break-chunk-qty";
import { useRateLimitCountdown } from "@/lib/use-rate-limit-countdown";

const MARGIN_LACS_MAX = 999_999;

function parsePositiveNum(v: string): number | null {
  const n = parseFloat(v.replace(/,/g, ""));
  return Number.isFinite(n) && n > 0 ? n : null;
}

export default function StrategyBuilderNewPage() {
  const { secondsRemaining } = useRateLimitCountdown();
  const [segmentExchange, setSegmentExchange] = useState<"NFO" | "BFO">("NFO");
  const [stockCode, setStockCode] = useState("");
  const [expiryDate, setExpiryDate] = useState("");
  const [rangeLower, setRangeLower] = useState("");
  const [rangeUpper, setRangeUpper] = useState("");
  const [marginLacs, setMarginLacs] = useState("");
  const [maxLossLacs, setMaxLossLacs] = useState("");
  const [provisionElm, setProvisionElm] = useState(false);
  const [legs, setLegs] = useState<StrategyLeg[]>([]);
  const [proposedData, setProposedData] = useState<ProposeTradesSuccess | null>(
    null,
  );
  const [selectedTradeId, setSelectedTradeId] = useState<string | null>(null);
  const [generateError, setGenerateError] = useState<string | null>(null);
  const [executePreviewOpen, setExecutePreviewOpen] = useState(false);
  const [ivShockPct, setIvShockPct] = useState(0);
  const [showGreeks, setShowGreeks] = useState(false);
  const [showToday, setShowToday] = useState(true);
  const [legMarginCache, setLegMarginCache] = useState<
    Record<string, LegMarginEntry>
  >({});
  const [legMarginFetchingId, setLegMarginFetchingId] = useState<string | null>(
    null,
  );
  const [strategyMarginValidSig, setStrategyMarginValidSig] = useState<
    string | null
  >(null);

  const uq = useQuery({
    queryKey: ["strategy-builder", "underlyings", segmentExchange],
    queryFn: () =>
      apiClient.get<UnderlyingsApiResponse>(
        `/strategy-builder/underlyings?exchange_code=${segmentExchange}`,
      ),
  });

  const expiryOptions = useMemo(() => {
    const entry = uq.data?.underlyings?.find((u) => u.stock_code === stockCode);
    return sortExpiryDatesAsc(entry?.expiry_dates ?? []);
  }, [uq.data, stockCode]);

  const lotSize = proposedData?.lot_size ?? 1;
  const spot = proposedData?.spot_price ?? null;
  const atmIv = proposedData?.atm_iv ?? null;

  const rangeLowerNum = parsePositiveNum(rangeLower);
  const rangeUpperNum = parsePositiveNum(rangeUpper);
  const marginLacsNum = parsePositiveNum(marginLacs);
  const maxLossLacsNum = parsePositiveNum(maxLossLacs);

  const rangeValid =
    rangeLowerNum != null &&
    rangeUpperNum != null &&
    rangeLowerNum < rangeUpperNum;

  const rangeBeyondSpotWarning = useMemo(() => {
    if (spot == null || !rangeValid || rangeLowerNum == null || rangeUpperNum == null)
      return null;
    const loPct = Math.abs(rangeLowerNum - spot) / spot;
    const hiPct = Math.abs(rangeUpperNum - spot) / spot;
    if (loPct > 0.1 || hiPct > 0.1) {
      return "Range bound is more than 10% away from spot price.";
    }
    return null;
  }, [spot, rangeValid, rangeLowerNum, rangeUpperNum]);

  const canGenerate =
    Boolean(stockCode.trim() && expiryDate.trim()) &&
    rangeValid &&
    marginLacsNum != null &&
    maxLossLacsNum != null;

  const generateM = useMutation({
    mutationFn: () =>
      proposeTrades({
        exchange_code: segmentExchange,
        stock_code: stockCode.trim(),
        expiry_date: expiryDate.trim(),
        range_lower: rangeLowerNum!,
        range_upper: rangeUpperNum!,
        margin_lacs: marginLacsNum!,
        max_loss_lacs: maxLossLacsNum!,
        provision_elm: provisionElm,
      }),
    onSuccess: (res) => {
      if (res.Status !== 200 || !res.Success) {
        setGenerateError(res.Error ?? "Failed to generate trades.");
        setProposedData(null);
        setSelectedTradeId(null);
        setLegs([]);
        return;
      }
      setGenerateError(null);
      setProposedData(res.Success);
      setSelectedTradeId(null);
      setLegs([]);
      setLegMarginCache({});
    },
    onError: (e: Error) => {
      setGenerateError(e.message || "Failed to generate trades.");
      setProposedData(null);
    },
  });

  const trades: ProposedTrade[] = proposedData?.trades ?? [];

  const selectTrade = useCallback(
    (trade: ProposedTrade) => {
      if (trade.status !== "ok" || !trade.legs.length) return;
      setSelectedTradeId(trade.strategy_id);
      setLegs(proposedLegsToStrategyLegs(trade.legs, lotSize));
      setLegMarginCache({});
      setStrategyMarginValidSig(null);
    },
    [lotSize],
  );

  const legsWithQtyForMargin = useMemo(
    () => legs.filter((l) => l.lots > 0),
    [legs],
  );

  const marginLegKeyStructural = useMemo(
    () =>
      JSON.stringify({
        segmentExchange,
        stockCode,
        expiryDate,
        lotSize,
        legs: legs.map((l) => ({
          id: l.id,
          strike: l.strike,
          right: l.right,
          side: l.side,
        })),
      }),
    [segmentExchange, stockCode, expiryDate, lotSize, legs],
  );

  const marginQ = useQuery({
    queryKey: ["strategy-builder", "margin", marginLegKeyStructural],
    queryFn: () =>
      apiClient.post<MarginApiResponse>("/strategy-builder/margin", {
        legs: legsWithQtyForMargin.map((l) => ({
          stock_code: stockCode,
          exchange_code: segmentExchange,
          expiry_date: expiryDate,
          product_type: "Options",
          right: l.right,
          strike_price: String(l.strike),
          quantity: String(Math.round(l.lots * lotSize)),
          price: String(l.premiumPerUnit ?? 0),
          action: l.side,
        })),
      }),
    enabled: Boolean(
      stockCode && expiryDate && legsWithQtyForMargin.length > 0,
    ),
    staleTime: 5000,
  });

  const spanMargin = parseSpanMarginFromResponse(marginQ.data);
  const strategyBuilderMarginWarnings = useMemo(() => {
    const raw = (marginQ.data?.Success as { warnings?: unknown } | undefined)
      ?.warnings;
    if (!Array.isArray(raw)) return [];
    return raw
      .map((w) => {
        if (!w || typeof w !== "object") return "";
        return String((w as Record<string, unknown>).message ?? "").trim();
      })
      .filter(Boolean);
  }, [marginQ.data]);

  useEffect(() => {
    if (marginQ.isFetching || !marginQ.isSuccess || !marginQ.data) return;
    setStrategyMarginValidSig(legsQtySignature(legs));
  }, [marginQ.isFetching, marginQ.dataUpdatedAt, marginQ.isSuccess, marginQ.data, legs]);

  const strategyMarginQtyStale =
    marginQ.isSuccess &&
    strategyMarginValidSig !== null &&
    strategyMarginValidSig !== legsQtySignature(legs);

  const fetchLegMargin = useCallback(
    async (leg: StrategyLeg) => {
      if (!stockCode || !expiryDate || leg.lots <= 0) return;
      setLegMarginFetchingId(leg.id);
      try {
        const res = await apiClient.post<MarginApiResponse>(
          "/strategy-builder/margin",
          {
            legs: [
              {
                stock_code: stockCode,
                exchange_code: segmentExchange,
                expiry_date: expiryDate,
                product_type: "Options",
                right: leg.right,
                strike_price: String(leg.strike),
                quantity: String(Math.round(leg.lots * lotSize)),
                price: String(leg.premiumPerUnit ?? 0),
                action: leg.side,
              },
            ],
          },
        );
        const span = parseSpanMarginFromResponse(res);
        setLegMarginCache((prev) => ({
          ...prev,
          [leg.id]: {
            lots: leg.lots,
            span,
            error:
              span == null
                ? String(res.Error ?? "Margin unavailable")
                : undefined,
          },
        }));
      } finally {
        setLegMarginFetchingId(null);
      }
    },
    [stockCode, expiryDate, segmentExchange, lotSize],
  );

  const totalsNetPremium = useMemo(() => {
    let t = 0;
    for (const l of legs) {
      if (l.lots <= 0) continue;
      const units = l.lots * lotSize;
      const prem = (l.premiumPerUnit ?? 0) * units;
      t += l.side === "Sell" ? prem : -prem;
    }
    return t;
  }, [legs, lotSize]);

  const totalsMargin = useMemo(() => {
    let sum = 0;
    let hasPositiveLots = false;
    const hasMarginFetchInFlight = legMarginFetchingId != null;
    let hasMissingFreshMargin = false;
    for (const l of legs) {
      if (l.lots <= 0) continue;
      hasPositiveLots = true;
      const entry = legMarginCache[l.id];
      if (!entry || entry.lots !== l.lots) {
        hasMissingFreshMargin = true;
        continue;
      }
      if (entry.span != null && Number.isFinite(entry.span)) {
        sum += entry.span;
      } else {
        hasMissingFreshMargin = true;
      }
    }
    return { sum, hasPositiveLots, hasMarginFetchInFlight, hasMissingFreshMargin };
  }, [legs, legMarginCache, legMarginFetchingId]);

  const { chunkQty, setChunkQty, defaultsQuery: chunkDefaultsQ, chunkReady } =
    useBreakChunkQty({
      stockCode,
      exchangeCode: segmentExchange,
      expiryDisplay: expiryDate,
      enabled: executePreviewOpen,
    });

  const strategyExecuteLegs = useMemo(
    () =>
      legs
        .filter((l) => l.lots > 0)
        .map((l) => ({
          strike: l.strike,
          right: l.right,
          side: l.side,
          quantity: Math.round(l.lots * lotSize),
          premiumPerUnit: l.premiumPerUnit ?? 0,
        })),
    [legs, lotSize],
  );

  const onSegmentChange = (ex: "NFO" | "BFO") => {
    setSegmentExchange(ex);
    setStockCode("");
    setExpiryDate("");
    setLegs([]);
    setProposedData(null);
    setSelectedTradeId(null);
    setGenerateError(null);
  };

  return (
    <AppShell>
      <RevokedTradingPageGuard>
        {secondsRemaining !== null ? (
          <RateLimitPauseOverlay secondsRemaining={secondsRemaining} />
        ) : null}
        <div className="space-y-5">
          <header>
            <h1 className="text-xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
              Strategy Builder (New)
            </h1>
            <p className="mt-2 max-w-2xl text-sm leading-relaxed text-zinc-600 dark:text-zinc-400">
              Parameter-driven strategy proposals with batched market data
            </p>
          </header>

          <section className={`${sb.section} relative z-20 space-y-5`}>
            <h2 className={sb.sectionTitle}>1. Underlying &amp; Expiry</h2>
            <div
              className="flex min-h-[2.75rem] flex-col overflow-visible rounded-md border border-zinc-200/90 bg-zinc-100 shadow-sm dark:border-transparent dark:bg-[#1b1c1f] sm:flex-row sm:items-center"
              role="toolbar"
            >
              <div className="flex shrink-0 items-center border-b border-zinc-200 px-2 py-2 dark:border-zinc-700/70 sm:border-b-0 sm:border-r sm:py-0 sm:ps-2.5 sm:pe-2">
                <div className="inline-flex rounded-lg bg-zinc-200/70 p-0.5 ring-1 ring-zinc-300/70 dark:bg-black/30 dark:ring-zinc-700/70">
                  {(["NFO", "BFO"] as const).map((ex) => (
                    <button
                      key={ex}
                      type="button"
                      onClick={() => onSegmentChange(ex)}
                      className={`rounded-md px-3 py-1.5 text-xs font-semibold sm:text-sm ${
                        segmentExchange === ex
                          ? "bg-white text-zinc-900 shadow-sm dark:bg-zinc-700 dark:text-white"
                          : "text-zinc-600 dark:text-zinc-400"
                      }`}
                    >
                      {ex === "NFO" ? "NSE" : "BSE"}
                    </button>
                  ))}
                </div>
              </div>
              <div className="relative z-30 flex min-w-0 max-w-[min(100%,26rem)] flex-1 items-center px-3 py-2 sm:border-r sm:dark:border-zinc-700/70">
                <OptionChainUnderlyingSearch
                  variant="ticker"
                  chainBar
                  underlyings={uq.data?.underlyings ?? []}
                  value={stockCode}
                  disabled={uq.isLoading}
                  spot={spot}
                  onChange={(code) => {
                    setStockCode(code);
                    setExpiryDate("");
                    setLegs([]);
                    setProposedData(null);
                    setSelectedTradeId(null);
                  }}
                />
              </div>
              <div className="flex shrink-0 items-center px-3 py-2 sm:pe-3.5">
                <ExpirySelectPill
                  layout="toolbar"
                  tone="darkToolbar"
                  dates={expiryOptions}
                  value={expiryDate}
                  disabled={!stockCode}
                  onChange={(d) => {
                    setExpiryDate(d);
                    setLegs([]);
                    setProposedData(null);
                    setSelectedTradeId(null);
                  }}
                />
              </div>
            </div>
          </section>

          <section
            id="strategy-builder-parameters"
            className={`${sb.section} space-y-4`}
          >
            <h2 className={sb.sectionTitle}>2. Parameters</h2>
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              <label className="block">
                <span className={sb.fieldLabel}>Range lower (absolute)</span>
                <input
                  type="number"
                  className={sb.input}
                  value={rangeLower}
                  onChange={(e) => setRangeLower(e.target.value)}
                  min={0}
                  step={1}
                />
              </label>
              <label className="block">
                <span className={sb.fieldLabel}>Range upper (absolute)</span>
                <input
                  type="number"
                  className={sb.input}
                  value={rangeUpper}
                  onChange={(e) => setRangeUpper(e.target.value)}
                  min={0}
                  step={1}
                />
              </label>
              <label className="block">
                <span className={sb.fieldLabel}>Margin to deploy (Lacs)</span>
                <input
                  type="number"
                  className={sb.input}
                  value={marginLacs}
                  onChange={(e) => setMarginLacs(e.target.value)}
                  min={0}
                  max={MARGIN_LACS_MAX}
                  step={0.1}
                />
              </label>
              <label className="block">
                <span className={sb.fieldLabel}>Maximum loss (Lacs)</span>
                <input
                  type="number"
                  className={sb.input}
                  value={maxLossLacs}
                  onChange={(e) => setMaxLossLacs(e.target.value)}
                  min={0}
                  max={MARGIN_LACS_MAX}
                  step={0.1}
                />
              </label>
              <label className="block">
                <span className={sb.fieldLabel}>Spot price (SPP)</span>
                <input
                  type="text"
                  className={sb.input}
                  readOnly
                  value={spot != null ? spot.toLocaleString("en-IN") : "—"}
                />
              </label>
              <div className="flex items-end">
                <label className={`${sb.checkboxRow} pb-2.5`}>
                  <input
                    type="checkbox"
                    className={sb.checkbox}
                    checked={provisionElm}
                    onChange={(e) => setProvisionElm(e.target.checked)}
                  />
                  Provision ELM (2%)
                </label>
              </div>
            </div>
            {rangeLowerNum != null &&
            rangeUpperNum != null &&
            rangeLowerNum >= rangeUpperNum ? (
              <p className="text-sm text-red-600 dark:text-red-400">
                Range lower must be less than range upper.
              </p>
            ) : null}
            {rangeBeyondSpotWarning ? (
              <p className="text-sm text-amber-700 dark:text-amber-300">
                {rangeBeyondSpotWarning}
              </p>
            ) : null}
            {generateError ? (
              <p className="text-sm text-red-600 dark:text-red-400">
                {generateError}
              </p>
            ) : null}
            <button
              type="button"
              className={sb.btnPrimary}
              disabled={!canGenerate || generateM.isPending}
              onClick={() => generateM.mutate()}
            >
              {generateM.isPending ? "Generating…" : "Generate Trades"}
            </button>
          </section>

          <section
            id="strategy-builder-proposed-trades"
            className={`${sb.section} space-y-4`}
          >
            <h2 className={sb.sectionTitle}>3. Proposed Trades</h2>
            {!trades.length ? (
              <p className="text-sm text-zinc-500 dark:text-zinc-400">
                Fill parameters and click Generate Trades to see strategy
                proposals.
              </p>
            ) : (
              <div className="flex flex-wrap justify-center gap-3 sm:justify-start">
                {trades.map((trade) => (
                  <ProposedStrategyTradeCard
                    key={trade.strategy_id}
                    trade={trade}
                    lotSize={lotSize}
                    spot={spot}
                    atmIv={atmIv}
                    expiryDate={expiryDate}
                    selected={selectedTradeId === trade.strategy_id}
                    onSelect={() => selectTrade(trade)}
                  />
                ))}
              </div>
            )}
          </section>

          <StrategyLegsPanel
            stockCode={stockCode}
            expiryDate={expiryDate}
            lotSize={lotSize}
            legs={legs}
            onLegsChange={setLegs}
            legMarginCache={legMarginCache}
            legMarginFetchingId={legMarginFetchingId}
            onFetchLegMargin={(leg) => void fetchLegMargin(leg)}
            totalsNetPremium={totalsNetPremium}
            totalsMargin={totalsMargin}
            onExecute={() => setExecutePreviewOpen(true)}
            executeDisabled={
              !legs.length ||
              legs.some((x) => x.lots <= 0) ||
              !stockCode ||
              !expiryDate
            }
          />

          <StrategyPayoffPanel
            legs={legs}
            spot={spot}
            atmIv={atmIv}
            expiryDate={expiryDate}
            lotSize={lotSize}
            ivShockPct={ivShockPct}
            onIvShockChange={setIvShockPct}
            showToday={showToday}
            onShowTodayChange={setShowToday}
            showGreeks={showGreeks}
            onShowGreeksChange={setShowGreeks}
            spanMargin={spanMargin}
            marginFetching={marginQ.isFetching}
            marginQtyStale={strategyMarginQtyStale}
            onRefreshMargin={() => void marginQ.refetch()}
            marginError={marginQ.data?.Error ?? null}
            marginWarnings={strategyBuilderMarginWarnings}
          />
        </div>

        <OrderExecutionConfirmDialog
          open={executePreviewOpen}
          onClose={() => setExecutePreviewOpen(false)}
          stockCode={stockCode}
          exchangeCode={segmentExchange}
          expiryDisplay={expiryDate}
          legs={strategyExecuteLegs}
          controlledChunk={{
            chunkQty,
            onChunkQtyChange: setChunkQty,
            defaultsQuery: chunkDefaultsQ,
            chunkReady,
          }}
        />
      </RevokedTradingPageGuard>
    </AppShell>
  );
}
