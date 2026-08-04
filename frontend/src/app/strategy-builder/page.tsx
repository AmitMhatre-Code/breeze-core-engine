"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { AppShell } from "@/components/layout/AppShell";
import { HelpLink } from "@/components/help/HelpLink";
import { RevokedTradingPageGuard } from "@/components/license/RevokedTradingPageGuard";
import { Modal } from "@/components/ui/Modal";
import { OptionChainUnderlyingSearch } from "@/components/shared/order/OptionChainUnderlyingSearch";
import {
  filterRecentStockCodes,
  useRecentlyTradedScrips,
} from "@/lib/use-recently-traded-scrips";
import { QuoteSourceBadge } from "@/components/shared/market-data/QuoteSourceBadge";
import { ExchangeFlipToggle } from "@/components/shared/order/ExchangeFlipToggle";
import { OrderExecutionConfirmDialog } from "@/components/shared/order/OrderExecutionConfirmDialog";
import { ExpirySelectPill } from "@/components/shared/order/ExpirySelectPill";
import { OutlookFilterButtons } from "@/components/strategy-builder/OutlookFilterButtons";
import { MasonryGrid } from "@/components/strategy-builder/MasonryGrid";
import { PopLabel } from "@/components/strategy-builder/PopLabel";
import { PopHelpTrigger } from "@/components/strategy-builder/PopHelpTrigger";
import { ProposedStrategyTradeCard } from "@/components/strategy-builder/ProposedStrategyTradeCard";
import { ProposeTradesProgressBar } from "@/components/strategy-builder/ProposeTradesProgressBar";
import { StrategyExplainabilityPanel } from "@/components/strategy-builder/StrategyExplainabilityPanel";
import {
  TradeSortLink,
  type TradeSortKey,
} from "@/components/strategy-builder/TradeSortLink";
import { SectionGate } from "@/components/strategy-builder/SectionGate";
import { StrategyLegsPanel } from "@/components/strategy-builder/StrategyLegsPanel";
import { StrategyPayoffPanel } from "@/components/strategy-builder/StrategyPayoffPanel";
import { apiClient } from "@/lib/api-client";
import {
  getProposeTradesJobStatus,
  startProposeTradesJob,
} from "@/lib/strategy-builder/api";
import {
  chainQueryOptions,
  chainSuccessForExpiry,
} from "@/lib/strategy-builder/chain-query";
import { buildSigmaSmiles } from "@/lib/strategy-builder/chainIv";
import {
  chainIsLoading,
} from "@/lib/strategy-builder/chain-loading";
import { quoteMetaFromChain } from "@/lib/quote-source";
import {
  ChainBuildStatus,
  inferChainBuildPhase,
} from "@/components/shared/market-data/ChainBuildStatus";
import { useWsSubscriptionHolder } from "@/lib/use-ws-subscription-holder";
import { premiumFromChainRow, strikesFromChain } from "@/lib/strategy-builder/chain-quote";
import { expiryDisplayToYears, sortExpiryDatesAsc } from "@/lib/strategy-builder/expiry";
import {
  computeMarginsCalcKey,
  useOnDemandBasketMargin,
} from "@/lib/strategy-builder/real-margin";
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
  BuilderMode,
  OptionRight,
  OrderSide,
  Outlook,
  ProposedTrade,
  ProposeTradesSuccess,
  StrategyCategory,
  StrategyLeg,
  UnderlyingsApiResponse,
} from "@/lib/strategy-builder/types";
import { tradeSelectionKey } from "@/lib/strategy-builder/types";

const DEFAULT_MIN_POP_PCT = 65;
const DEFAULT_MIN_ANN_RETURN_PCT = 5;
import { useBreakChunkQty } from "@/lib/use-break-chunk-qty";
import { useAggressiveOrderControls } from "@/lib/use-aggressive-order-controls";
import { AggressiveModeControl } from "@/components/order/AggressiveModeControl";

const MARGIN_LACS_MAX = 999_999;

const CATEGORY_LABELS: Record<StrategyCategory, string> = {
  income: "Income Strategies",
  bullish: "Bullish Strategies",
  bearish: "Bearish Strategies",
};

const MIN_ANN_RETURN_HINT =
  "Minimum annualized return on SPAN margin required for any returned income trade.";

const DIRECTIONAL_HINT =
  "Generates Conservative, Moderate, and Aggressive variants automatically from your capital and max-loss limits.";

function FieldHint({ text }: { text: string }) {
  return (
    <span
      title={text}
      aria-label={text}
      className="inline-flex size-4 shrink-0 cursor-help items-center justify-center rounded-full text-body font-bold leading-none text-faint ring-1 ring-border"
    >
      i
    </span>
  );
}

function ChevronIcon({ className = "" }: { className?: string }) {
  return (
    <svg
      width="11"
      height="11"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2.2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      className={className}
    >
      <path d="m6 9 6 6 6-6" />
    </svg>
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

type GeneratedParams = {
  marginLacs: number | null;
  maxLossLacs: number | null;
  provisionElm: boolean;
  minPopPct: number | null;
  minAnnReturnPct: number | null;
};

function resetDownstream(
  setters: {
    setMinPopPct: (v: string) => void;
    setMinAnnReturnPct: (v: string) => void;
    setLegs: (v: StrategyLeg[]) => void;
    setProposedData: (v: ProposeTradesSuccess | null) => void;
    setSelectedTradeId: (v: string | null) => void;
    setGenerateError: (v: string | null) => void;
    setOutlookFilter: (v: Set<Outlook>) => void;
    setTradeSort: (v: TradeSortKey) => void;
    setBuilderMode: (v: BuilderMode) => void;
  },
  clearError = false,
) {
  setters.setMinPopPct(String(DEFAULT_MIN_POP_PCT));
  setters.setMinAnnReturnPct(String(DEFAULT_MIN_ANN_RETURN_PCT));
  setters.setLegs([]);
  setters.setProposedData(null);
  setters.setSelectedTradeId(null);
  setters.setOutlookFilter(new Set(ALL_OUTLOOKS));
  setters.setTradeSort("score");
  setters.setBuilderMode(null);
  if (clearError) setters.setGenerateError(null);
}

export default function StrategyBuilderPage() {
  const subscriptionHolder = useWsSubscriptionHolder();
  const [segmentExchange, setSegmentExchange] = useState<"NFO" | "BFO">("NFO");
  const [stockCode, setStockCode] = useState("");
  const [expiryDate, setExpiryDate] = useState("");
  const [minPopPct, setMinPopPct] = useState(String(DEFAULT_MIN_POP_PCT));
  const [minAnnReturnPct, setMinAnnReturnPct] = useState(
    String(DEFAULT_MIN_ANN_RETURN_PCT),
  );
  const [marginLacs, setMarginLacs] = useState("");
  const [maxLossLacs, setMaxLossLacs] = useState("");
  const [provisionElm, setProvisionElm] = useState(false);
  const [builderMode, setBuilderMode] = useState<BuilderMode>(null);
  const [legs, setLegs] = useState<StrategyLeg[]>([]);
  const [proposedData, setProposedData] = useState<ProposeTradesSuccess | null>(
    null,
  );
  const [lastGeneratedParams, setLastGeneratedParams] =
    useState<GeneratedParams | null>(null);
  const [selectedTradeId, setSelectedTradeId] = useState<string | null>(null);
  const [generateError, setGenerateError] = useState<string | null>(null);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [generatingCategory, setGeneratingCategory] =
    useState<StrategyCategory | null>(null);
  const activeJobIdRef = useRef<string | null>(null);
  const generatingParamsRef = useRef<GeneratedParams | null>(null);
  const [executePreviewOpen, setExecutePreviewOpen] = useState(false);
  const [showBuilderTip, setShowBuilderTip] = useState(false);
  const [showTransparency, setShowTransparency] = useState(false);
  const [outlookFilter, setOutlookFilter] = useState<Set<Outlook>>(
    () => new Set(ALL_OUTLOOKS),
  );
  const [tradeSort, setTradeSort] = useState<TradeSortKey>("score");
  const [unlimitedRiskTrade, setUnlimitedRiskTrade] = useState<ProposedTrade | null>(
    null,
  );
  const prevSection2ReadyRef = useRef(false);
  const prevSection3ReadyRef = useRef(false);
  const prevSection4ReadyRef = useRef(false);

  const downstreamSetters = {
    setMinPopPct,
    setMinAnnReturnPct,
    setLegs,
    setProposedData,
    setSelectedTradeId,
    setGenerateError,
    setOutlookFilter,
    setTradeSort,
    setBuilderMode,
  };

  const uq = useQuery({
    queryKey: ["strategy-builder", "underlyings", segmentExchange],
    queryFn: () =>
      apiClient.get<UnderlyingsApiResponse>(
        `/strategy-builder/underlyings?exchange_code=${segmentExchange}`,
      ),
  });

  const recentScrips = useRecentlyTradedScrips();
  const recentStockCodes = useMemo(
    () => filterRecentStockCodes(recentScrips, uq.data?.underlyings ?? []),
    [recentScrips, uq.data?.underlyings],
  );

  const chainQ = useQuery({
    ...chainQueryOptions({
      queryKeyPrefix: ["strategy-builder"],
      stock_code: stockCode,
      expiry_date: expiryDate,
      exchange_code: segmentExchange,
      subscription_holder: subscriptionHolder,
    }),
  });

  const chainSuccess = chainSuccessForExpiry(chainQ.data, expiryDate);
  const chainRows = chainSuccess?.chain_rows ?? [];
  const strikes = useMemo(() => strikesFromChain(chainRows), [chainRows]);
  const chainSpot = chainSuccess?.spot_price ?? null;
  const chainQuoteMeta = useMemo(
    () => quoteMetaFromChain(chainSuccess),
    [chainSuccess],
  );

  const chainLoading = chainIsLoading(stockCode, expiryDate, chainQ);
  const chainBuildPhase = inferChainBuildPhase({
    quoteMeta: chainQuoteMeta,
    isInitialLoad: chainLoading,
  });

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
  /** Live client-side chain smile — distinct from `atmIv` above (a backend snapshot). */
  const sigmaSmiles = useMemo(
    () => (chainSuccess ? buildSigmaSmiles(chainSuccess, expiryDisplayToYears(expiryDate)) : null),
    [chainSuccess, expiryDate],
  );

  const marginCalc = useOnDemandBasketMargin({
    legs,
    lotSize,
    stockCode,
    exchangeCode: segmentExchange,
    expiryDate,
    spot,
  });

  const section1Complete = Boolean(stockCode.trim() && expiryDate.trim());
  const chainInitiallyLoaded = !chainQ.isPending && chainQ.data != null;
  const section2Ready =
    section1Complete && chainSpot != null && chainInitiallyLoaded;
  const trades: ProposedTrade[] = proposedData?.trades ?? [];
  const relaxedTrades: ProposedTrade[] = proposedData?.relaxed_trades ?? [];
  const section3Ready =
    proposedData != null &&
    (trades.some((t) => t.status === "ok") || relaxedTrades.length > 0);
  const section4Ready = legs.length > 0 && selectedTradeId != null;
  const selectedTradeName = useMemo(() => {
    if (!selectedTradeId) return null;
    const all = [...trades, ...relaxedTrades];
    return (
      all.find((t) => tradeSelectionKey(t) === selectedTradeId)
        ?.strategy_name ?? null
    );
  }, [selectedTradeId, trades, relaxedTrades]);

  const marginLacsNum = parsePositiveNum(marginLacs);
  const maxLossLacsNum = parsePositiveNum(maxLossLacs);
  const allowInfiniteLoss = maxLossLacsNum == null;
  const minPopPctNum = (() => {
    const n = parseFloat(minPopPct.replace(/,/g, ""));
    if (!Number.isFinite(n)) return null;
    return Math.min(99, Math.max(1, n));
  })();
  const minAnnReturnPctNum = (() => {
    const n = parseFloat(minAnnReturnPct.replace(/,/g, ""));
    if (!Number.isFinite(n)) return null;
    return Math.min(100, Math.max(0, n));
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
    section2Ready &&
    marginLacsNum != null &&
    (allowInfiniteLoss || maxLossLacsNum != null);
  const canGenerateIncome =
    canGenerateShared && minPopPctNum != null && minAnnReturnPctNum != null;
  const canGenerateDirectional = canGenerateShared;

  const isResultStale =
    proposedData != null &&
    lastGeneratedParams != null &&
    (marginLacsNum !== lastGeneratedParams.marginLacs ||
      maxLossLacsNum !== lastGeneratedParams.maxLossLacs ||
      provisionElm !== lastGeneratedParams.provisionElm ||
      minPopPctNum !== lastGeneratedParams.minPopPct ||
      minAnnReturnPctNum !== lastGeneratedParams.minAnnReturnPct);

  const applyGenerateSuccess = useCallback(
    (
      success: ProposeTradesSuccess,
      category: StrategyCategory,
      params: GeneratedParams,
    ) => {
      setGenerateError(null);
      setProposedData(success);
      setLastGeneratedParams(params);
      setBuilderMode(category);
      setSelectedTradeId(null);
      setLegs([]);
    },
    [],
  );

  const clearGenerateJob = useCallback(() => {
    setActiveJobId(null);
    setGeneratingCategory(null);
    activeJobIdRef.current = null;
  }, []);

  const startGenerateM = useMutation({
    mutationFn: async (category: StrategyCategory) => {
      const params: GeneratedParams = {
        marginLacs: marginLacsNum,
        maxLossLacs: maxLossLacsNum,
        provisionElm,
        minPopPct: minPopPctNum,
        minAnnReturnPct: minAnnReturnPctNum,
      };
      const res = await startProposeTradesJob({
        exchange_code: segmentExchange,
        stock_code: stockCode.trim(),
        expiry_date: expiryDate.trim(),
        margin_lacs: marginLacsNum!,
        ...(allowInfiniteLoss
          ? { allow_infinite_loss: true }
          : { max_loss_lacs: maxLossLacsNum! }),
        min_pop_pct:
          category === "income"
            ? minPopPctNum!
            : (minPopPctNum ?? DEFAULT_MIN_POP_PCT),
        min_ann_return_pct:
          category === "income" ? minAnnReturnPctNum! : undefined,
        provision_elm: provisionElm,
        strategy_category: category,
      });
      if (res.Status !== 202 || !res.Success?.job_id) {
        throw new Error(res.Error ?? "Failed to start strategy generation.");
      }
      return { jobId: res.Success.job_id, category, params };
    },
    onSuccess: ({ jobId, category, params }) => {
      setGenerateError(null);
      setGeneratingCategory(category);
      generatingParamsRef.current = params;
      activeJobIdRef.current = jobId;
      setActiveJobId(jobId);
    },
    onError: (e: Error) => {
      setGenerateError(e.message || "Failed to generate trades.");
      setProposedData(null);
      clearGenerateJob();
    },
  });

  const jobStatusQ = useQuery({
    queryKey: ["strategy-builder", "propose-job", activeJobId],
    queryFn: ({ signal }) => getProposeTradesJobStatus(activeJobId!, signal),
    enabled: Boolean(activeJobId),
    refetchInterval: (query) => {
      const status = query.state.data?.Success?.status;
      if (status === "done" || status === "error") return false;
      return 750;
    },
  });

  useEffect(() => {
    const payload = jobStatusQ.data?.Success;
    if (!payload || !activeJobId || activeJobIdRef.current !== activeJobId) {
      return;
    }
    if (payload.status === "done" && payload.result) {
      if (generatingCategory && generatingParamsRef.current) {
        applyGenerateSuccess(
          payload.result,
          generatingCategory,
          generatingParamsRef.current,
        );
      }
      clearGenerateJob();
      return;
    }
    if (payload.status === "error") {
      setGenerateError(payload.error ?? "Failed to generate trades.");
      setProposedData(null);
      clearGenerateJob();
    }
  }, [
    jobStatusQ.data,
    activeJobId,
    generatingCategory,
    applyGenerateSuccess,
    clearGenerateJob,
  ]);

  useEffect(() => {
    if (!jobStatusQ.isError || !activeJobId) return;
    const msg =
      jobStatusQ.error instanceof Error
        ? jobStatusQ.error.message
        : "Failed to check generation status.";
    setGenerateError(msg);
    setProposedData(null);
    clearGenerateJob();
  }, [jobStatusQ.isError, jobStatusQ.error, activeJobId, clearGenerateJob]);

  const isGenerating = startGenerateM.isPending || Boolean(activeJobId);
  const jobProgress = jobStatusQ.data?.Success;

  const sortTradeList = useCallback(
    (list: ProposedTrade[]) => {
      if (tradeSort === "server") return list;

      const withMetrics = list.map((t) => {
        const pop = computeTradePop(t, spot, atmIv, expiryDate, lotSize, sigmaSmiles);
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
        return [...list].sort(
          (a, b) => (b.net_premium ?? -Infinity) - (a.net_premium ?? -Infinity),
        );
      }

      return [...list].sort((a, b) => {
        const aLoss = a.max_loss;
        const bLoss = b.max_loss;
        if (aLoss == null && bLoss == null) return 0;
        if (aLoss == null) return 1;
        if (bLoss == null) return -1;
        return aLoss - bLoss;
      });
    },
    [tradeSort, spot, atmIv, sigmaSmiles, expiryDate, lotSize],
  );

  const displayedTrades = useMemo(() => {
    const list = trades.filter((t) => {
      if (t.status !== "ok") return false;
      const o = strategyOutlook(t.strategy_id);
      return o ? outlookFilter.has(o) : true;
    });
    return sortTradeList(list);
  }, [trades, outlookFilter, sortTradeList]);

  const displayedRelaxedTrades = useMemo(() => {
    const list = relaxedTrades.filter((t) => {
      if (t.status !== "ok") return false;
      const o = strategyOutlook(t.strategy_id);
      return o ? outlookFilter.has(o) : true;
    });
    return sortTradeList(list);
  }, [relaxedTrades, outlookFilter, sortTradeList]);

  const applySelectedTrade = useCallback(
    (trade: ProposedTrade) => {
      setSelectedTradeId(tradeSelectionKey(trade));
      const newLegs = proposedLegsToStrategyLegs(trade.legs, lotSize);
      setLegs(newLegs);
      if (trade.span_margin != null) {
        marginCalc.prefillSpanMargin(
          trade.span_margin,
          computeMarginsCalcKey(newLegs),
        );
      }
    },
    [lotSize, marginCalc],
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

  const onRightChange = useCallback(
    (legId: string, right: OptionRight) => {
      setLegs((prev) => {
        const updated = prev.map((x) =>
          x.id === legId ? { ...x, right } : x,
        );
        const leg = updated.find((l) => l.id === legId);
        if (!leg || leg.aggressiveLimit) return updated;
        const prem = premiumFromChainRow(chainRows, leg.strike, right);
        return updated.map((x) =>
          x.id === legId ? { ...x, premiumPerUnit: prem } : x,
        );
      });
    },
    [chainRows],
  );

  const onStrikeChange = useCallback(
    (legId: string, strike: number) => {
      setLegs((prev) => {
        const updated = prev.map((x) =>
          x.id === legId ? { ...x, strike } : x,
        );
        const leg = updated.find((l) => l.id === legId);
        if (!leg || leg.aggressiveLimit) return updated;
        const prem = premiumFromChainRow(chainRows, strike, leg.right);
        return updated.map((x) =>
          x.id === legId ? { ...x, premiumPerUnit: prem } : x,
        );
      });
    },
    [chainRows],
  );

  const onSideChange = useCallback((legId: string, side: OrderSide) => {
    setLegs((prev) =>
      prev.map((x) => (x.id === legId ? { ...x, side } : x)),
    );
  }, []);

  const onAddLeg = useCallback(() => {
    setLegs((prev) => {
      const last = prev[prev.length - 1];
      const strike =
        last?.strike ?? (spot != null ? Math.round(spot / 50) * 50 : 0);
      const right = last?.right ?? "Call";
      const newLeg: StrategyLeg = {
        id: `leg-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`,
        right,
        side: last?.side ?? "Sell",
        strike,
        lots: 1,
        premiumPerUnit: premiumFromChainRow(chainRows, strike, right) ?? undefined,
      };
      return [...prev, newLeg];
    });
  }, [spot, chainRows]);

  const onAggressiveChange = useCallback(
    (legId: string, checked: boolean) => {
      setLegs((prev) => {
        const leg = prev.find((x) => x.id === legId);
        if (!leg) return prev;
        const premiumPerUnit = checked
          ? undefined
          : (premiumFromChainRow(chainRows, leg.strike, leg.right) ?? undefined);
        return prev.map((x) =>
          x.id === legId
            ? { ...x, aggressiveLimit: checked, premiumPerUnit }
            : x,
        );
      });
    },
    [chainRows],
  );

  const onPriceChange = useCallback(
    (legId: string, premiumPerUnit: number | undefined) => {
      setLegs((prev) =>
        prev.map((x) =>
          x.id === legId ? { ...x, premiumPerUnit } : x,
        ),
      );
    },
    [],
  );

  const totalsNetPremium = useMemo(() => {
    let t = 0;
    for (const l of legs) {
      if (l.lots <= 0 || l.aggressiveLimit) continue;
      const units = l.lots * lotSize;
      const prem = (l.premiumPerUnit ?? 0) * units;
      t += l.side === "Sell" ? prem : -prem;
    }
    return t;
  }, [legs, lotSize]);


  const { chunkQty, setChunkQty, defaultsQuery: chunkDefaultsQ, chunkReady } =
    useBreakChunkQty({
      stockCode,
      exchangeCode: segmentExchange,
      expiryDisplay: expiryDate,
      enabled: executePreviewOpen,
    });

  const aggressiveControls = useAggressiveOrderControls();

  const strategyExecuteLegs = useMemo(
    () =>
      legs
        .filter((l) => l.lots > 0)
        .map((l) => ({
          strike: l.strike,
          right: l.right,
          side: l.side,
          quantity: Math.round(l.lots * lotSize),
          premiumPerUnit: l.aggressiveLimit ? 0 : (l.premiumPerUnit ?? 0),
          aggressiveLimit: l.aggressiveLimit ?? false,
          aggressiveMode: aggressiveControls.mode,
          aggressiveTolerancePct: aggressiveControls.tolerancePct,
        }))
        .sort((a, b) => a.strike - b.strike),
    [legs, lotSize, aggressiveControls.mode, aggressiveControls.tolerancePct],
  );

  const anyAggressiveLeg = legs.some((l) => l.lots > 0 && l.aggressiveLimit);

  const onSegmentChange = (ex: "NFO" | "BFO") => {
    setSegmentExchange(ex);
    setStockCode("");
    setExpiryDate("");
    resetDownstream(downstreamSetters, true);
  };

  useEffect(() => {
    try {
      setShowBuilderTip(
        sessionStorage.getItem("strategyBuilderTipsDismissed") !== "1",
      );
    } catch {
      setShowBuilderTip(true);
    }
  }, []);

  const dismissBuilderTip = useCallback(() => {
    setShowBuilderTip(false);
    try {
      sessionStorage.setItem("strategyBuilderTipsDismissed", "1");
    } catch {
      /* ignore */
    }
  }, []);

  return (
    <AppShell>
      <RevokedTradingPageGuard>
        <div className="mx-auto max-w-[1240px] space-y-5">
          <header className="flex items-end justify-between gap-4">
            <div>
              <h1 className="text-xl font-semibold tracking-tight text-foreground">
                Strategy Builder
              </h1>
              <p className="mt-2 max-w-2xl text-sm leading-relaxed text-muted">
                Parameter-driven strategy proposals with batched market data
              </p>
            </div>

            {chainQuoteMeta ? (
              <QuoteSourceBadge
                meta={chainQuoteMeta}
                variant="compact"
                buildKey={`${segmentExchange}:${stockCode}:${expiryDate}`}
                onRefresh={() => chainQ.refetch()}
                refreshing={chainQ.isFetching}
              />
            ) : null}
          </header>

          <section className="relative z-20 divide-y divide-border-soft rounded-[13px] border border-border bg-panel">
          <div className="space-y-4 p-5">
            <h2 className={sb.sectionTitle}>1. Underlying &amp; Expiry</h2>
            <div
              className="flex min-h-[2.75rem] flex-col overflow-visible rounded-[9px] sm:flex-row sm:items-start"
              role="toolbar"
            >
              <div className="flex shrink-0 items-center border-b border-border-soft px-2 py-2 sm:border-b-0 sm:border-r sm:ps-2.5 sm:pe-2">
                <ExchangeFlipToggle value={segmentExchange} onChange={onSegmentChange} />
              </div>
              <div className="relative z-30 flex min-w-0 max-w-[min(100%,17rem)] flex-1 items-center px-3 py-2 sm:border-r sm:border-border-soft">
                {uq.isLoading ? (
                  <span className="text-xs app-text-muted animate-pulse">
                    Loading underlyings…
                  </span>
                ) : (
                <OptionChainUnderlyingSearch
                  variant="ticker"
                  underlyings={uq.data?.underlyings ?? []}
                  value={stockCode}
                  disabled={uq.isLoading}
                  spot={spot}
                  loading={chainLoading}
                  quoteMeta={chainQuoteMeta}
                  recentStockCodes={recentStockCodes}
                  onChange={(code) => {
                    setStockCode(code);
                    setExpiryDate("");
                    resetDownstream(downstreamSetters);
                  }}
                />
                )}
              </div>
              <div className="flex shrink-0 items-center px-3 py-2 sm:pe-3.5">
                <ExpirySelectPill
                  layout="toolbar"
                  fullDate
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
          </div>

          {section1Complete && chainLoading ? (
            <div className="p-5">
              <ChainBuildStatus visible={chainLoading} phase={chainBuildPhase} />
            </div>
          ) : null}

          <SectionGate locked={!section2Ready} dimWhenLocked={section1Complete}>
            <div
              id="strategy-builder-parameters"
              className="space-y-4 p-5"
            >
              <div className="flex flex-wrap items-center justify-between gap-3">
                <h2 className={sb.sectionTitle}>2. Choose a strategy path</h2>
                <label
                  className={`${sb.checkboxRow} gap-2 text-xs font-medium leading-snug text-muted`}
                >
                  <button
                    type="button"
                    role="switch"
                    aria-checked={provisionElm}
                    aria-label="Toggle Provision for ELM"
                    onClick={() => setProvisionElm(!provisionElm)}
                    className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition ${
                      provisionElm ? "bg-accent-strong" : "bg-border"
                    }`}
                  >
                    <span
                      className={`inline-block size-5 transform rounded-full bg-white shadow transition ${
                        provisionElm ? "translate-x-[22px]" : "translate-x-0.5"
                      }`}
                    />
                  </button>
                  Provision for ELM{" "}
                  <span className="text-faint">— applies to all paths</span>
                </label>
              </div>
              {showBuilderTip ? (
                <div className="flex items-start justify-between gap-3 rounded-md border border-accent/25 bg-accent-tint px-3 py-2.5 text-sm text-foreground">
                  <p>
                    Set margin, PoP, and max loss to discover strategies.{" "}
                    <HelpLink topicId="strategy-builder-overview" className="text-sm">
                      Overview
                    </HelpLink>
                  </p>
                  <button
                    type="button"
                    onClick={dismissBuilderTip}
                    className="shrink-0 text-xs font-medium text-muted hover:text-foreground"
                  >
                    Dismiss
                  </button>
                </div>
              ) : null}
              <div className="space-y-4">
                <div className="flex flex-wrap items-start justify-start gap-x-8 gap-y-4">
                  <label className={`${sb.fieldRow} w-auto`}>
                    <span className={`${sb.fieldLabelInline} min-w-[9.5rem]`}>
                      Margin to deploy (Lacs)
                    </span>
                    <input
                      type="number"
                      className={`${sb.input} !w-[16rem] max-w-full`}
                      value={marginLacs}
                      onChange={(e) => setMarginLacs(e.target.value)}
                      min={0}
                      max={MARGIN_LACS_MAX}
                      step={0.1}
                    />
                  </label>
                  <label className={`${sb.fieldRow} w-auto`}>
                    <span className={`${sb.fieldLabelInline} min-w-[9.5rem]`}>
                      Max loss (in lacs)
                    </span>
                    <input
                      type="number"
                      className={`${sb.input} !w-[16rem] max-w-full`}
                      value={maxLossLacs}
                      onChange={(e) => setMaxLossLacs(e.target.value)}
                      placeholder="∞ Unlimited"
                      min={0}
                      max={MARGIN_LACS_MAX}
                      step={0.1}
                      aria-label="Maximum loss in Lacs"
                    />
                  </label>
                </div>

                <div className="flex flex-wrap items-stretch justify-start gap-4">
                  <div className={`${sb.parameterCard} w-full max-w-[30rem]`}>
                    <h3 className={sb.parameterCardTitle}>Income strategies</h3>
                    <div className="flex flex-1 flex-col gap-4">
                      <div className="min-w-0 space-y-1">
                        <label className={sb.fieldRow}>
                          <span
                            className={`${sb.fieldLabelInline} flex min-w-[11.5rem] items-center gap-1.5 whitespace-nowrap`}
                          >
                            <PopLabel variant="field" />
                            {" (%)"}
                          </span>
                          <input
                            type="number"
                            className={`${sb.input} !w-[9.5rem] max-w-full`}
                            value={minPopPct}
                            onChange={(e) => setMinPopPct(e.target.value)}
                            min={1}
                            max={99}
                            step={1}
                          />
                        </label>
                        {minPopPctNum == null && minPopPct.trim() !== "" ? (
                          <p className="text-sm text-down">
                            Minimum probability of profit must be between 1 and 99.
                          </p>
                        ) : null}
                      </div>
                      <div className="min-w-0 space-y-1">
                        <label className={sb.fieldRow}>
                          <span
                            className={`${sb.fieldLabelInline} flex min-w-[9.5rem] items-center gap-1.5`}
                          >
                            Min ann. return (%)
                            <FieldHint text={MIN_ANN_RETURN_HINT} />
                          </span>
                          <input
                            type="number"
                            className={`${sb.input} !w-[9.5rem] max-w-full`}
                            value={minAnnReturnPct}
                            onChange={(e) => setMinAnnReturnPct(e.target.value)}
                            min={0}
                            max={100}
                            step={0.5}
                          />
                        </label>
                        {minAnnReturnPctNum == null &&
                        minAnnReturnPct.trim() !== "" ? (
                          <p className="text-sm text-down">
                            Minimum annualized return must be between 0 and 100.
                          </p>
                        ) : null}
                      </div>
                    </div>
                    <button
                      type="button"
                      className={`${sb.btnPrimary} mt-auto w-full`}
                      disabled={!canGenerateIncome || isGenerating}
                      onClick={() => startGenerateM.mutate("income")}
                    >
                      {isGenerating && generatingCategory === "income"
                        ? "Generating…"
                        : builderMode === "income" && isResultStale
                          ? `Regenerate ${CATEGORY_LABELS.income}`
                          : CATEGORY_LABELS.income}
                    </button>
                  </div>

                  <div className={`${sb.parameterCard} w-full max-w-[30rem]`}>
                    <h3 className={sb.parameterCardTitle}>Directional strategies</h3>
                    <div className="flex-1 space-y-2 text-sm text-muted">
                      <p>{DIRECTIONAL_HINT}</p>
                      <div className="inline-flex flex-wrap items-center gap-1 text-xs text-muted">
                        <span>Est. probability of profit is shown on each trade for reference.</span>
                        <PopHelpTrigger />
                      </div>
                    </div>
                    <div className="mt-auto grid gap-2 sm:grid-cols-2">
                      {(["bullish", "bearish"] as const).map((category) => (
                        <button
                          key={category}
                          type="button"
                          className={sb.btnPrimary}
                          disabled={!canGenerateDirectional || isGenerating}
                          onClick={() => startGenerateM.mutate(category)}
                        >
                          {isGenerating && generatingCategory === category
                            ? "Generating…"
                            : builderMode === category && isResultStale
                              ? `Regenerate ${CATEGORY_LABELS[category]}`
                              : CATEGORY_LABELS[category]}
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
              </div>
              {generateError ? (
                <p className="mt-4 text-sm text-down">
                  {generateError}
                </p>
              ) : null}
              {isGenerating &&
              jobProgress &&
              (jobProgress.status === "queued" ||
                jobProgress.status === "running") ? (
                <ProposeTradesProgressBar
                  message={jobProgress.message}
                  progressPct={jobProgress.progress_pct}
                  progressCurrent={jobProgress.progress_current}
                  progressTotal={jobProgress.progress_total}
                />
              ) : null}
            </div>
          </SectionGate>

          <SectionGate locked={!section3Ready}>
            <div
              id="strategy-builder-proposed-trades"
              className="space-y-4 p-5"
            >
              <div className="flex flex-wrap items-baseline justify-between gap-3">
                <h2 className={sb.sectionTitle}>
                  3. Proposed Trades
                  {builderMode === "income" ||
                  builderMode === "bullish" ||
                  builderMode === "bearish" ? (
                    <span className="ml-2 text-sm font-normal text-muted">
                      — {CATEGORY_LABELS[builderMode]}
                    </span>
                  ) : null}
                  {isResultStale ? (
                    <span className="ml-2 inline-flex items-center rounded-full border border-accent/30 bg-accent-tint px-2 py-0.5 align-middle text-xs font-medium text-foreground">
                      Inputs changed — click Regenerate
                    </span>
                  ) : null}
                </h2>
                {trades.some((t) => t.status === "ok") ||
                relaxedTrades.length > 0 ? (
                  <div className="flex shrink-0 flex-wrap items-center gap-3 text-heading">
                    <OutlookFilterButtons
                      selected={outlookFilter}
                      onChange={setOutlookFilter}
                    />
                    <span
                      className="text-faint"
                      aria-hidden
                    >
                      ·
                    </span>
                    <TradeSortLink value={tradeSort} onChange={setTradeSort} />
                  </div>
                ) : null}
              </div>
              <div
                className={isResultStale ? "opacity-60 transition-opacity" : undefined}
              >
              {!trades.some((t) => t.status === "ok") &&
              !relaxedTrades.length ? (
                <p className="text-sm text-muted">
                  Fill parameters and choose a strategy category to see
                  proposals.
                </p>
              ) : displayedTrades.length === 0 &&
                displayedRelaxedTrades.length === 0 ? (
                <p className="text-sm text-muted">
                  No strategies match the selected outlook filters.{" "}
                  <HelpLink topicId="strategy-builder-constraints" className="text-sm">
                    Troubleshooting
                  </HelpLink>
                </p>
              ) : (
                <>
                  {displayedTrades.length > 0 ? (
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
                          sigmaSmiles={sigmaSmiles}
                          expiryDate={expiryDate}
                          selected={selectedTradeId === tradeSelectionKey(trade)}
                          onSelect={() => selectTrade(trade)}
                        />
                      )}
                    />
                  ) : null}
                  {displayedRelaxedTrades.length > 0 ? (
                    <div className="space-y-3 pt-2">
                      <div>
                        <h3 className="text-sm font-semibold text-foreground">
                          Near-threshold alternatives
                          {builderMode === "income" ||
                          builderMode === "bullish" ||
                          builderMode === "bearish" ? (
                            <span className="ml-1.5 font-normal text-muted">
                              — {CATEGORY_LABELS[builderMode]} strategies
                            </span>
                          ) : null}
                        </h3>
                        <p className="mt-1 max-w-3xl text-sm text-muted">
                          The strategies below did not meet your PoP and/or
                          annualized return thresholds, or are unlimited-loss
                          structures excluded while a max-loss limit is set.
                          They are the closest matches if those
                          constraints were relaxed.{" "}
                          <HelpLink
                            topicId="near-threshold-alternatives"
                            className="text-sm"
                          >
                            Learn more
                          </HelpLink>
                        </p>
                      </div>
                      <MasonryGrid
                        items={displayedRelaxedTrades}
                        gapClassName="gap-4"
                        getKey={(trade) => `relaxed-${tradeSelectionKey(trade)}`}
                        renderItem={(trade) => (
                          <ProposedStrategyTradeCard
                            trade={trade}
                            lotSize={lotSize}
                            spot={spot}
                            atmIv={atmIv}
                            sigmaSmiles={sigmaSmiles}
                            expiryDate={expiryDate}
                            selected={
                              selectedTradeId === tradeSelectionKey(trade)
                            }
                            onSelect={() => selectTrade(trade)}
                          />
                        )}
                      />
                    </div>
                  ) : null}
                </>
              )}
              </div>
              {proposedData?.user_report ? (
                <div>
                  <button
                    type="button"
                    onClick={() => setShowTransparency((v) => !v)}
                    aria-expanded={showTransparency}
                    className="flex items-center gap-1.5 text-body text-faint transition hover:text-muted"
                  >
                    <ChevronIcon
                      className={`transition-transform ${showTransparency ? "rotate-180" : ""}`}
                    />
                    How were these trades chosen?
                  </button>
                  {showTransparency ? (
                    <div className="mt-2.5">
                      <StrategyExplainabilityPanel
                        report={proposedData.user_report}
                        auditSessionId={proposedData.audit_session_id}
                      />
                    </div>
                  ) : null}
                </div>
              ) : null}
            </div>
          </SectionGate>

          <SectionGate locked={!section4Ready}>
            <div id="strategy-builder-legs" className="p-5">
            <StrategyLegsPanel
              sectionTitle={`4. Legs${selectedTradeName ? ` — ${selectedTradeName}` : ""}`}
              lotSize={lotSize}
              legs={legs}
              onLegsChange={setLegs}
              onAddLeg={onAddLeg}
              strikes={strikes}
              chainBusy={chainLoading}
              onStrikeChange={onStrikeChange}
              onRightChange={onRightChange}
              onSideChange={onSideChange}
              onPriceChange={onPriceChange}
              onAggressiveChange={onAggressiveChange}
              legMargins={marginCalc.legMargins}
              totalsNetPremium={totalsNetPremium}
              totalsMargin={marginCalc.totalsMargin}
              onExecute={() => setExecutePreviewOpen(true)}
              executeDisabled={
                !legs.length ||
                legs.some((x) => x.lots <= 0) ||
                !stockCode ||
                !expiryDate
              }
              marginWarnings={marginCalc.error ? [marginCalc.error] : []}
              onCalculateMargins={marginCalc.calculate}
              calculatingMargins={marginCalc.isCalculating}
              calculateMarginsDisabled={marginCalc.calculateDisabled}
            />
            <AggressiveModeControl
              controls={aggressiveControls}
              visible={anyAggressiveLeg}
              className="mt-3 px-5"
            />
            </div>

            <div className="p-5">
            <StrategyPayoffPanel
              legs={legs}
              spot={spot}
              atmIv={atmIv}
              sigmaSmiles={sigmaSmiles}
              expiryDate={expiryDate}
              lotSize={lotSize}
            />
            </div>
          </SectionGate>
          </section>
        </div>

        <OrderExecutionConfirmDialog
          open={executePreviewOpen}
          onClose={() => setExecutePreviewOpen(false)}
          stockCode={stockCode}
          exchangeCode={segmentExchange}
          expiryDisplay={expiryDate}
          legs={strategyExecuteLegs}
          quoteMeta={chainQuoteMeta}
          controlledChunk={{
            chunkQty,
            onChunkQtyChange: setChunkQty,
            defaultsQuery: chunkDefaultsQ,
            chunkReady,
          }}
        />

        <Modal
          open={Boolean(unlimitedRiskTrade)}
          onClose={() => setUnlimitedRiskTrade(null)}
          titleId="unlimited-risk-title"
          panelClassName={`${sb.modalPanel} w-full max-w-md`}
        >
          <h3
            id="unlimited-risk-title"
            className="text-base font-semibold text-down"
          >
            Risk warning — unlimited loss
          </h3>
          <p className="text-sm leading-relaxed text-muted">
            {unlimitedRiskTrade?.strategy_name} can have large or unlimited
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
                if (unlimitedRiskTrade) applySelectedTrade(unlimitedRiskTrade);
                setUnlimitedRiskTrade(null);
              }}
            >
              I understand — select
            </button>
          </div>
        </Modal>
      </RevokedTradingPageGuard>
    </AppShell>
  );
}
