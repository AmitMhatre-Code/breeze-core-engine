"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { AppShell } from "@/components/layout/AppShell";
import { RevokedTradingPageGuard } from "@/components/license/RevokedTradingPageGuard";
import { OptionChainUnderlyingSearch } from "@/components/order/OptionChainUnderlyingSearch";
import { OrderExecutionConfirmDialog } from "@/components/order/OrderExecutionConfirmDialog";
import { RateLimitPauseOverlay } from "@/components/order/RateLimitPauseOverlay";
import { ExpirySelectPill } from "@/components/strategy-builder/ExpirySelectPill";
import { OutlookFilterButtons } from "@/components/strategy-builder/OutlookFilterButtons";
import { ProposedStrategyTradeCard } from "@/components/strategy-builder/ProposedStrategyTradeCard";
import {
  TradeSortLink,
  type TradeSortKey,
} from "@/components/strategy-builder/TradeSortLink";
import { SectionGate } from "@/components/strategy-builder/SectionGate";
import { SpotRangeSlider } from "@/components/strategy-builder/SpotRangeSlider";
import {
  StrategyLegsPanel,
  type LegMarginEntry,
} from "@/components/strategy-builder/StrategyLegsPanel";
import { StrategyPayoffPanel } from "@/components/strategy-builder/StrategyPayoffPanel";
import { apiClient } from "@/lib/api-client";
import {
  downloadStrategyBuilderAudit,
  fetchStrategyBuilderChain,
  proposeTrades,
} from "@/lib/strategy-builder/api";
import { sortExpiryDatesAsc } from "@/lib/strategy-builder/expiry";
import {
  legsQtySignature,
  parseSpanMarginFromResponse,
} from "@/lib/strategy-builder/leg-ui-helpers";
import { proposedLegsToStrategyLegs } from "@/lib/strategy-builder/map-proposed-legs";
import {
  ALL_OUTLOOKS,
  strategyOutlook,
} from "@/lib/strategy-builder/templates";
import { computeTradePop } from "@/lib/strategy-builder/trade-metrics";
import { sb } from "@/lib/strategy-builder/ui";
import type {
  MarginApiResponse,
  Outlook,
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

function parseNum(raw: unknown): number {
  if (typeof raw === "number" && Number.isFinite(raw)) return raw;
  if (typeof raw === "string") {
    const n = parseFloat(raw.replace(/,/g, ""));
    return Number.isFinite(n) ? n : NaN;
  }
  return NaN;
}

function resetDownstream(
  setters: {
    setRangeLower: (v: string) => void;
    setRangeUpper: (v: string) => void;
    setLegs: (v: StrategyLeg[]) => void;
    setProposedData: (v: ProposeTradesSuccess | null) => void;
    setSelectedTradeId: (v: string | null) => void;
    setGenerateError: (v: string | null) => void;
    setOutlookFilter: (v: Set<Outlook>) => void;
    setTradeSort: (v: TradeSortKey) => void;
  },
  clearError = false,
) {
  setters.setRangeLower("");
  setters.setRangeUpper("");
  setters.setLegs([]);
  setters.setProposedData(null);
  setters.setSelectedTradeId(null);
  setters.setOutlookFilter(new Set(ALL_OUTLOOKS));
  setters.setTradeSort("server");
  if (clearError) setters.setGenerateError(null);
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
  const [outlookFilter, setOutlookFilter] = useState<Set<Outlook>>(
    () => new Set(ALL_OUTLOOKS),
  );
  const [tradeSort, setTradeSort] = useState<TradeSortKey>("server");
  const [auditDownloading, setAuditDownloading] = useState(false);
  const [auditError, setAuditError] = useState<string | null>(null);
  const rangeInitKeyRef = useRef<string | null>(null);
  const prevSection2ReadyRef = useRef(false);
  const prevSection3ReadyRef = useRef(false);
  const prevSection4ReadyRef = useRef(false);

  const downstreamSetters = {
    setRangeLower,
    setRangeUpper,
    setLegs,
    setProposedData,
    setSelectedTradeId,
    setGenerateError,
    setOutlookFilter,
    setTradeSort,
  };

  const uq = useQuery({
    queryKey: ["strategy-builder", "underlyings", segmentExchange],
    queryFn: () =>
      apiClient.get<UnderlyingsApiResponse>(
        `/strategy-builder/underlyings?exchange_code=${segmentExchange}`,
      ),
  });

  const chainQ = useQuery({
    queryKey: [
      "strategy-builder",
      "chain",
      stockCode,
      expiryDate,
      segmentExchange,
    ],
    queryFn: ({ signal }) =>
      fetchStrategyBuilderChain(
        {
          stock_code: stockCode.trim(),
          expiry_date: expiryDate.trim(),
          exchange_code: segmentExchange,
        },
        signal,
      ),
    enabled: Boolean(stockCode.trim() && expiryDate.trim()),
  });

  const chainSuccess =
    chainQ.data?.Status === 200 ? chainQ.data.Success : null;
  const chainSpot = chainSuccess?.spot_price ?? null;

  const expiryOptions = useMemo(() => {
    const entry = uq.data?.underlyings?.find((u) => u.stock_code === stockCode);
    return sortExpiryDatesAsc(entry?.expiry_dates ?? []);
  }, [uq.data, stockCode]);

  const chainLotSize = useMemo(() => {
    if (!chainSuccess?.chain_rows?.length) return 1;
    const row = chainSuccess.chain_rows[0];
    const ls = parseNum(row.call?.lot_size) || parseNum(row.put?.lot_size);
    return Number.isFinite(ls) && ls > 0 ? Math.round(ls) : 1;
  }, [chainSuccess]);

  const lotSize = proposedData?.lot_size ?? chainLotSize;
  const spot = chainSpot ?? proposedData?.spot_price ?? null;
  const atmIv = proposedData?.atm_iv ?? null;

  const section1Complete = Boolean(stockCode.trim() && expiryDate.trim());
  const section2Ready =
    section1Complete && chainSpot != null && !chainQ.isFetching;
  const trades: ProposedTrade[] = proposedData?.trades ?? [];
  const section3Ready = proposedData != null && trades.length > 0;
  const section4Ready = legs.length > 0 && selectedTradeId != null;

  const rangeLowerNum = parsePositiveNum(rangeLower);
  const rangeUpperNum = parsePositiveNum(rangeUpper);
  const marginLacsNum = parsePositiveNum(marginLacs);
  const maxLossLacsNum = parsePositiveNum(maxLossLacs);

  const rangeValid =
    rangeLowerNum != null &&
    rangeUpperNum != null &&
    rangeLowerNum < rangeUpperNum;

  useEffect(() => {
    if (chainSpot == null || !section1Complete) return;
    const key = `${segmentExchange}:${stockCode}:${expiryDate}:${chainSpot}`;
    if (rangeInitKeyRef.current === key) return;
    rangeInitKeyRef.current = key;
    setRangeLower(String(Math.round(chainSpot * 0.9)));
    setRangeUpper(String(Math.round(chainSpot * 1.1)));
  }, [chainSpot, section1Complete, segmentExchange, stockCode, expiryDate]);

  useEffect(() => {
    if (section2Ready && !prevSection2ReadyRef.current) {
      document
        .getElementById("strategy-builder-parameters")
        ?.scrollIntoView({ behavior: "smooth", block: "start" });
    }
    prevSection2ReadyRef.current = section2Ready;
  }, [section2Ready]);

  useEffect(() => {
    if (section3Ready && !prevSection3ReadyRef.current) {
      document
        .getElementById("strategy-builder-proposed-trades")
        ?.scrollIntoView({ behavior: "smooth", block: "start" });
    }
    prevSection3ReadyRef.current = section3Ready;
  }, [section3Ready]);

  useEffect(() => {
    if (section4Ready && !prevSection4ReadyRef.current) {
      document
        .getElementById("strategy-builder-legs")
        ?.scrollIntoView({ behavior: "smooth", block: "start" });
    }
    prevSection4ReadyRef.current = section4Ready;
  }, [section4Ready]);

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
    section2Ready &&
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

  const displayedTrades = useMemo(() => {
    let list = trades.filter((t) => {
      const o = strategyOutlook(t.strategy_id);
      return o ? outlookFilter.has(o) : true;
    });

    if (tradeSort === "server") return list;

    const withPop = list.map((t) => ({
      trade: t,
      pop: computeTradePop(t, spot, atmIv, expiryDate, lotSize),
    }));

    if (tradeSort === "pop") {
      withPop.sort((a, b) => (b.pop ?? -1) - (a.pop ?? -1));
      return withPop.map((x) => x.trade);
    }

    if (tradeSort === "net_premium") {
      list = [...list].sort(
        (a, b) => (b.net_premium ?? -Infinity) - (a.net_premium ?? -Infinity),
      );
      return list;
    }

    list = [...list].sort((a, b) => {
      const aLoss = a.max_loss;
      const bLoss = b.max_loss;
      if (aLoss == null && bLoss == null) return 0;
      if (aLoss == null) return 1;
      if (bLoss == null) return -1;
      return aLoss - bLoss;
    });
    return list;
  }, [trades, outlookFilter, tradeSort, spot, atmIv, expiryDate, lotSize]);

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
    rangeInitKeyRef.current = null;
    resetDownstream(downstreamSetters, true);
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
                    rangeInitKeyRef.current = null;
                    resetDownstream(downstreamSetters);
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
                    rangeInitKeyRef.current = null;
                    resetDownstream(downstreamSetters);
                  }}
                />
              </div>
            </div>
          </section>

          <SectionGate locked={!section2Ready}>
            <section
              id="strategy-builder-parameters"
              className={`${sb.section} space-y-4`}
            >
              <h2 className={sb.sectionTitle}>2. Parameters</h2>
              <div className="space-y-4">
                {chainSpot != null ? (
                  <SpotRangeSlider
                    spot={chainSpot}
                    rangeLower={rangeLower}
                    rangeUpper={rangeUpper}
                    onRangeLowerChange={setRangeLower}
                    onRangeUpperChange={setRangeUpper}
                  />
                ) : null}
                <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
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
                  <div className="flex items-end">
                    <div
                      className={`${sb.checkboxRow} gap-2 pb-2.5 text-xs font-medium leading-snug text-zinc-600 dark:text-zinc-400`}
                    >
                      <button
                        type="button"
                        role="switch"
                        aria-checked={provisionElm}
                        aria-label="Toggle Provision for ELM"
                        onClick={() => setProvisionElm(!provisionElm)}
                        className={`relative inline-flex h-5 w-9 items-center rounded-full transition ${
                          provisionElm
                            ? "bg-sky-600"
                            : "bg-zinc-300 dark:bg-zinc-700"
                        }`}
                      >
                        <span
                          className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition ${
                            provisionElm ? "translate-x-4" : "translate-x-0.5"
                          }`}
                        />
                      </button>
                      Provision for ELM
                    </div>
                  </div>
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
          </SectionGate>

          <SectionGate locked={!section3Ready}>
            <section
              id="strategy-builder-proposed-trades"
              className={`${sb.section} space-y-4`}
            >
              <div className="flex flex-wrap items-baseline justify-between gap-3">
                <h2 className={sb.sectionTitle}>3. Proposed Trades</h2>
                {trades.length > 0 || proposedData?.audit_session_id ? (
                  <div className="flex shrink-0 items-center gap-3 text-[11px]">
                    {trades.length > 0 ? (
                      <TradeSortLink value={tradeSort} onChange={setTradeSort} />
                    ) : null}
                    {trades.length > 0 && proposedData?.audit_session_id ? (
                      <span className="text-zinc-400 dark:text-zinc-500" aria-hidden>
                        ·
                      </span>
                    ) : null}
                    {proposedData?.audit_session_id ? (
                      <button
                        type="button"
                        className="font-normal text-sky-600 underline underline-offset-2 hover:text-sky-500 disabled:cursor-wait disabled:opacity-60 dark:text-sky-400 dark:hover:text-sky-300"
                        title="Download build audit log (JSON)"
                        disabled={auditDownloading}
                        onClick={() => {
                          void (async () => {
                            setAuditError(null);
                            setAuditDownloading(true);
                            try {
                              await downloadStrategyBuilderAudit(
                                proposedData.audit_session_id!,
                              );
                            } catch (e) {
                              const msg =
                                e instanceof Error
                                  ? e.message
                                  : "Failed to download audit log";
                              setAuditError(msg);
                            } finally {
                              setAuditDownloading(false);
                            }
                          })();
                        }}
                      >
                        {auditDownloading ? "downloading…" : "download audit"}
                      </button>
                    ) : null}
                  </div>
                ) : null}
              </div>
              {auditError ? (
                <p className="text-sm text-red-600 dark:text-red-400">{auditError}</p>
              ) : null}
              {trades.length > 0 ? (
                <OutlookFilterButtons
                  selected={outlookFilter}
                  onChange={setOutlookFilter}
                />
              ) : null}
              {!trades.length ? (
                <p className="text-sm text-zinc-500 dark:text-zinc-400">
                  Fill parameters and click Generate Trades to see strategy
                  proposals.
                </p>
              ) : displayedTrades.length === 0 ? (
                <p className="text-sm text-zinc-500 dark:text-zinc-400">
                  No strategies match the selected outlook filters.
                </p>
              ) : (
                <div className="flex flex-wrap justify-center gap-3 sm:justify-start">
                  {displayedTrades.map((trade) => (
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
          </SectionGate>

          <SectionGate locked={!section4Ready}>
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
          </SectionGate>
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
