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
import { MasonryGrid } from "@/components/strategy-builder/MasonryGrid";
import { ProposedStrategyTradeCard } from "@/components/strategy-builder/ProposedStrategyTradeCard";
import {
  TradeSortLink,
  type TradeSortKey,
} from "@/components/strategy-builder/TradeSortLink";
import { SectionGate } from "@/components/strategy-builder/SectionGate";
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
import {
  computeTradePop,
  computeTradeScore,
  isUnlimitedMaxLoss,
} from "@/lib/strategy-builder/trade-metrics";
import { sb } from "@/lib/strategy-builder/ui";
import type {
  MarginApiResponse,
  Outlook,
  ProposedTrade,
  ProposeTradesSuccess,
  RiskRewardProfile,
  StrategyCategory,
  StrategyLeg,
  UnderlyingsApiResponse,
} from "@/lib/strategy-builder/types";
import { tradeSelectionKey } from "@/lib/strategy-builder/types";

const DEFAULT_MIN_POP_PCT = 65;
import { useBreakChunkQty } from "@/lib/use-break-chunk-qty";
import { useRateLimitCountdown } from "@/lib/use-rate-limit-countdown";

const MARGIN_LACS_MAX = 999_999;

const CATEGORY_LABELS: Record<StrategyCategory, string> = {
  income: "Income Strategies",
  bullish: "Bullish Strategies",
  bearish: "Bearish Strategies",
};

const MIN_POP_HINT =
  "Sets how far OTM income shorts are placed. Higher PoP → further OTM (lower delta).";

const RISK_PROFILE_OPTIONS: {
  id: RiskRewardProfile;
  label: string;
  tooltip: string;
}[] = [
  {
    id: "conservative",
    label: "Conservative",
    tooltip:
      "Long leg ~0.40Δ, short leg ~0.20Δ on spreads. Lower premium, needs a larger move.",
  },
  {
    id: "moderate",
    label: "Moderate",
    tooltip:
      "Long leg ~0.50Δ, short leg ~0.30Δ on spreads. Balanced directional exposure.",
  },
  {
    id: "aggressive",
    label: "Aggressive",
    tooltip:
      "Long leg ~0.60Δ, short leg ~0.35Δ on spreads. Higher premium, profits on a smaller move.",
  },
];

function FieldHint({ text }: { text: string }) {
  return (
    <span
      title={text}
      aria-label={text}
      className="inline-flex size-4 shrink-0 cursor-help items-center justify-center rounded-full text-[10px] font-bold leading-none text-zinc-400 ring-1 ring-zinc-300/80 dark:text-zinc-500 dark:ring-zinc-600"
    >
      i
    </span>
  );
}

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
    setMinPopPct: (v: string) => void;
    setLegs: (v: StrategyLeg[]) => void;
    setProposedData: (v: ProposeTradesSuccess | null) => void;
    setSelectedTradeId: (v: string | null) => void;
    setGenerateError: (v: string | null) => void;
    setOutlookFilter: (v: Set<Outlook>) => void;
    setTradeSort: (v: TradeSortKey) => void;
    setActiveCategory: (v: StrategyCategory | null) => void;
  },
  clearError = false,
) {
  setters.setMinPopPct(String(DEFAULT_MIN_POP_PCT));
  setters.setLegs([]);
  setters.setProposedData(null);
  setters.setSelectedTradeId(null);
  setters.setOutlookFilter(new Set(ALL_OUTLOOKS));
  setters.setTradeSort("score");
  setters.setActiveCategory(null);
  if (clearError) setters.setGenerateError(null);
}

export default function StrategyBuilderNewPage() {
  const { secondsRemaining } = useRateLimitCountdown();
  const [segmentExchange, setSegmentExchange] = useState<"NFO" | "BFO">("NFO");
  const [stockCode, setStockCode] = useState("");
  const [expiryDate, setExpiryDate] = useState("");
  const [minPopPct, setMinPopPct] = useState(String(DEFAULT_MIN_POP_PCT));
  const [riskRewardProfile, setRiskRewardProfile] =
    useState<RiskRewardProfile>("moderate");
  const [marginLacs, setMarginLacs] = useState("");
  const [maxLossLacs, setMaxLossLacs] = useState("");
  const [provisionElm, setProvisionElm] = useState(false);
  const [activeCategory, setActiveCategory] = useState<StrategyCategory | null>(
    null,
  );
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
  const [tradeSort, setTradeSort] = useState<TradeSortKey>("score");
  const [auditDownloading, setAuditDownloading] = useState(false);
  const [auditError, setAuditError] = useState<string | null>(null);
  const [unlimitedRiskTrade, setUnlimitedRiskTrade] = useState<ProposedTrade | null>(
    null,
  );
  const prevSection2ReadyRef = useRef(false);
  const prevSection3ReadyRef = useRef(false);
  const prevSection4ReadyRef = useRef(false);

  const downstreamSetters = {
    setMinPopPct,
    setLegs,
    setProposedData,
    setSelectedTradeId,
    setGenerateError,
    setOutlookFilter,
    setTradeSort,
    setActiveCategory,
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

  const marginLacsNum = parsePositiveNum(marginLacs);
  const maxLossLacsNum = parsePositiveNum(maxLossLacs);
  const minPopPctNum = (() => {
    const n = parseFloat(minPopPct.replace(/,/g, ""));
    if (!Number.isFinite(n)) return null;
    return Math.min(99, Math.max(1, n));
  })();

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

  const canGenerateShared =
    section2Ready && marginLacsNum != null && maxLossLacsNum != null;
  const canGenerateIncome = canGenerateShared && minPopPctNum != null;
  const canGenerateDirectional = canGenerateShared;

  const generateM = useMutation({
    mutationFn: (category: StrategyCategory) =>
      proposeTrades({
        exchange_code: segmentExchange,
        stock_code: stockCode.trim(),
        expiry_date: expiryDate.trim(),
        margin_lacs: marginLacsNum!,
        max_loss_lacs: maxLossLacsNum!,
        min_pop_pct:
          category === "income"
            ? minPopPctNum!
            : (minPopPctNum ?? DEFAULT_MIN_POP_PCT),
        provision_elm: provisionElm,
        strategy_category: category,
        risk_reward_profile: riskRewardProfile,
      }),
    onSuccess: (res, category) => {
      if (res.Status !== 200 || !res.Success) {
        setGenerateError(res.Error ?? "Failed to generate trades.");
        setProposedData(null);
        setSelectedTradeId(null);
        setLegs([]);
        return;
      }
      setGenerateError(null);
      setProposedData(res.Success);
      setActiveCategory(category);
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

    const withMetrics = list.map((t) => {
      const pop = computeTradePop(t, spot, atmIv, expiryDate, lotSize);
      return {
        trade: t,
        pop,
        score: computeTradeScore(t, pop),
      };
    });

    if (tradeSort === "score") {
      withMetrics.sort((a, b) => (b.score ?? -Infinity) - (a.score ?? -Infinity));
      return withMetrics.map((x) => x.trade);
    }

    if (tradeSort === "pop") {
      withMetrics.sort((a, b) => (b.pop ?? -1) - (a.pop ?? -1));
      return withMetrics.map((x) => x.trade);
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

  const applySelectedTrade = useCallback(
    (trade: ProposedTrade) => {
      setSelectedTradeId(tradeSelectionKey(trade));
      setLegs(proposedLegsToStrategyLegs(trade.legs, lotSize));
      setLegMarginCache({});
      setStrategyMarginValidSig(null);
    },
    [lotSize],
  );

  const selectTrade = useCallback(
    (trade: ProposedTrade) => {
      if (trade.status !== "ok" || !trade.legs.length) return;
      if (isUnlimitedMaxLoss(trade.max_loss)) {
        setUnlimitedRiskTrade(trade);
        return;
      }
      applySelectedTrade(trade);
    },
    [applySelectedTrade],
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
                <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                  <label className={sb.fieldRow}>
                    <span className={`${sb.fieldLabelInline} min-w-[9.5rem]`}>
                      Margin to deploy (Lacs)
                    </span>
                    <input
                      type="number"
                      className={`${sb.input} min-w-0 flex-1`}
                      value={marginLacs}
                      onChange={(e) => setMarginLacs(e.target.value)}
                      min={0}
                      max={MARGIN_LACS_MAX}
                      step={0.1}
                    />
                  </label>
                  <label className={sb.fieldRow}>
                    <span className={`${sb.fieldLabelInline} min-w-[9.5rem]`}>
                      Maximum loss (Lacs)
                    </span>
                    <input
                      type="number"
                      className={`${sb.input} min-w-0 flex-1`}
                      value={maxLossLacs}
                      onChange={(e) => setMaxLossLacs(e.target.value)}
                      min={0}
                      max={MARGIN_LACS_MAX}
                      step={0.1}
                    />
                  </label>
                  <div className={sb.fieldRow}>
                    <div
                      className={`${sb.checkboxRow} gap-2 text-xs font-medium leading-snug text-zinc-600 dark:text-zinc-400`}
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

                <div className="grid gap-4 lg:grid-cols-2">
                  <div className={sb.parameterCard}>
                    <h3 className={sb.parameterCardTitle}>Income strategies</h3>
                    <label className={sb.fieldRow}>
                      <span
                        className={`${sb.fieldLabelInline} flex min-w-[9.5rem] items-center gap-1.5`}
                      >
                        Minimum PoP (%)
                        <FieldHint text={MIN_POP_HINT} />
                      </span>
                      <input
                        type="number"
                        className={`${sb.input} min-w-0 flex-1`}
                        value={minPopPct}
                        onChange={(e) => setMinPopPct(e.target.value)}
                        min={1}
                        max={99}
                        step={1}
                      />
                    </label>
                    {minPopPctNum == null && minPopPct.trim() !== "" ? (
                      <p className="text-sm text-red-600 dark:text-red-400">
                        Minimum PoP must be between 1 and 99.
                      </p>
                    ) : null}
                    <button
                      type="button"
                      className={`${sb.btnPrimary} w-full`}
                      disabled={!canGenerateIncome || generateM.isPending}
                      onClick={() => generateM.mutate("income")}
                    >
                      {generateM.isPending && generateM.variables === "income"
                        ? "Generating…"
                        : CATEGORY_LABELS.income}
                    </button>
                  </div>

                  <div className={sb.parameterCard}>
                    <h3 className={sb.parameterCardTitle}>Directional strategies</h3>
                    <div className={sb.fieldRow}>
                      <span className={`${sb.fieldLabelInline} min-w-[9.5rem]`}>
                        Risk / reward profile
                      </span>
                      <div className="flex min-w-0 flex-1 flex-wrap gap-2">
                        {RISK_PROFILE_OPTIONS.map((opt) => (
                          <button
                            key={opt.id}
                            type="button"
                            title={opt.tooltip}
                            className={`inline-flex items-center rounded-lg border px-3.5 py-2.5 text-sm font-medium transition ${
                              riskRewardProfile === opt.id
                                ? "border-sky-600 bg-sky-50 text-sky-800 dark:border-sky-500 dark:bg-sky-950/50 dark:text-sky-200"
                                : "border-zinc-300/80 bg-white/95 text-zinc-700 hover:border-zinc-400 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-300"
                            }`}
                            onClick={() => setRiskRewardProfile(opt.id)}
                          >
                            {opt.label}
                          </button>
                        ))}
                      </div>
                    </div>
                    <div className="grid gap-2 sm:grid-cols-2">
                      {(["bullish", "bearish"] as const).map((category) => (
                        <button
                          key={category}
                          type="button"
                          className={sb.btnPrimary}
                          disabled={!canGenerateDirectional || generateM.isPending}
                          onClick={() => generateM.mutate(category)}
                        >
                          {generateM.isPending && generateM.variables === category
                            ? "Generating…"
                            : CATEGORY_LABELS[category]}
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
              </div>
              {generateError ? (
                <p className="mt-4 text-sm text-red-600 dark:text-red-400">
                  {generateError}
                </p>
              ) : null}
            </section>
          </SectionGate>

          <SectionGate locked={!section3Ready}>
            <section
              id="strategy-builder-proposed-trades"
              className={`${sb.section} space-y-4`}
            >
              <div className="flex flex-wrap items-baseline justify-between gap-3">
                <h2 className={sb.sectionTitle}>
                  3. Proposed Trades
                  {activeCategory ? (
                    <span className="ml-2 text-sm font-normal text-zinc-500 dark:text-zinc-400">
                      — {CATEGORY_LABELS[activeCategory]}
                    </span>
                  ) : null}
                </h2>
                {trades.length > 0 || proposedData?.audit_session_id ? (
                  <div className="flex shrink-0 flex-wrap items-center gap-3 text-[11px]">
                    {trades.length > 0 ? (
                      <>
                        <OutlookFilterButtons
                          selected={outlookFilter}
                          onChange={setOutlookFilter}
                        />
                        <span
                          className="text-zinc-400 dark:text-zinc-500"
                          aria-hidden
                        >
                          ·
                        </span>
                      </>
                    ) : null}
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
              {!trades.length ? (
                <p className="text-sm text-zinc-500 dark:text-zinc-400">
                  Fill parameters and choose a strategy category to see
                  proposals.
                </p>
              ) : displayedTrades.length === 0 ? (
                <p className="text-sm text-zinc-500 dark:text-zinc-400">
                  No strategies match the selected outlook filters.
                </p>
              ) : (
                <MasonryGrid
                  items={displayedTrades}
                  gapClassName="gap-4"
                  getKey={(trade) => tradeSelectionKey(trade)}
                  renderItem={(trade) => (
                    <ProposedStrategyTradeCard
                      trade={trade}
                      lotSize={lotSize}
                      spot={spot}
                      atmIv={atmIv}
                      expiryDate={expiryDate}
                      selected={selectedTradeId === tradeSelectionKey(trade)}
                      onSelect={() => selectTrade(trade)}
                    />
                  )}
                />
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

        {unlimitedRiskTrade ? (
          <div
            className="fixed inset-0 z-[120] flex items-center justify-center bg-black/50 p-4"
            role="presentation"
            onClick={() => setUnlimitedRiskTrade(null)}
            onKeyDown={(e) => {
              if (e.key === "Escape") setUnlimitedRiskTrade(null);
            }}
          >
            <div
              role="dialog"
              aria-modal="true"
              className={`${sb.modalPanel} w-full max-w-md`}
              onClick={(e) => e.stopPropagation()}
              onKeyDown={(e) => e.stopPropagation()}
            >
              <h3 className="text-base font-semibold text-red-700 dark:text-red-400">
                Risk warning — unlimited loss
              </h3>
              <p className="text-sm leading-relaxed text-zinc-600 dark:text-zinc-400">
                {unlimitedRiskTrade.strategy_name} can have large or unlimited
                loss if the market moves against you. Confirm you understand the
                exposure before selecting this strategy.
              </p>
              <div className="flex justify-end gap-2 pt-1">
                <button
                  type="button"
                  className={sb.btnSecondary}
                  onClick={() => setUnlimitedRiskTrade(null)}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  className={sb.btnDanger}
                  onClick={() => {
                    applySelectedTrade(unlimitedRiskTrade);
                    setUnlimitedRiskTrade(null);
                  }}
                >
                  I understand — select
                </button>
              </div>
            </div>
          </div>
        ) : null}
      </RevokedTradingPageGuard>
    </AppShell>
  );
}
