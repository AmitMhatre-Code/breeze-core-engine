"use client";

import { useCallback, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AppShell } from "@/components/layout/AppShell";
import { HelpLink } from "@/components/help/HelpLink";
import { NewFeatureBadge } from "@/components/ui/NewFeatureBadge";
import { Modal } from "@/components/ui/Modal";
import { RevokedTradingPageGuard } from "@/components/license/RevokedTradingPageGuard";
import { BasketLegsPanel } from "@/components/basket-order/BasketLegsPanel";
import { BasketPayoffPanel } from "@/components/basket-order/BasketPayoffPanel";
import { OptionChainUnderlyingSearch } from "@/components/shared/order/OptionChainUnderlyingSearch";
import {
  filterRecentStockCodes,
  useRecentlyTradedScrips,
} from "@/lib/use-recently-traded-scrips";
import { ExchangeFlipToggle } from "@/components/shared/order/ExchangeFlipToggle";
import { OrderExecutionConfirmDialog } from "@/components/shared/order/OrderExecutionConfirmDialog";
import { BuildYourOwnChainSection } from "@/components/strategy-builder/BuildYourOwnChainSection";
import { ExpirySelectPill } from "@/components/shared/order/ExpirySelectPill";
import { SectionGate } from "@/components/strategy-builder/SectionGate";
import { apiClient } from "@/lib/api-client";
import {
  chainQueryOptions,
  chainSuccessForExpiry,
} from "@/lib/strategy-builder/chain-query";
import {
  chainIsLoading,
} from "@/lib/strategy-builder/chain-loading";
import { quoteMetaFromChain } from "@/lib/quote-source";
import { QuoteSourceBadge } from "@/components/shared/market-data/QuoteSourceBadge";
import {
  ChainBuildStatus,
  inferChainBuildPhase,
} from "@/components/shared/market-data/ChainBuildStatus";
import { useWsSubscriptionHolder } from "@/lib/use-ws-subscription-holder";
import {
  appendLegFromChainRow,
  buildYourOwnAddedSlots,
  buildYourOwnSlotKey,
} from "@/lib/strategy-builder/build-your-own";
import {
  buySellRatioFromChainRow,
  createBlankLeg,
  premiumFromChainRow,
  strikesFromChain,
} from "@/lib/strategy-builder/chain-quote";
import { atmSigmaFromChain, buildSigmaSmiles } from "@/lib/strategy-builder/chainIv";
import { expiryDisplayToYears, sortExpiryDatesAsc } from "@/lib/strategy-builder/expiry";
import {
  fetchRealBasketMargins,
  useOnDemandBasketMargin,
} from "@/lib/strategy-builder/real-margin";
import {
  activeLotsGcd,
  computeNetDebit,
  computeScaleMultiplier,
  hasUnpricedActiveLeg,
  suggestScaleMode,
  type ScaleLeg,
  type ScaleMode,
} from "@/lib/strategy-builder/basket-scale";
import { formatIndianMoneyCompact } from "@/lib/format-money-in";
import { sb } from "@/lib/strategy-builder/ui";
import type {
  ChainRow,
  ChainSuccess,
  OptionRight,
  OrderSide,
  StrategyLeg,
  UnderlyingsApiResponse,
} from "@/lib/strategy-builder/types";
import { useBreakChunkQty } from "@/lib/use-break-chunk-qty";
import { useAggressiveOrderControls } from "@/lib/use-aggressive-order-controls";
import { AggressiveModeControl } from "@/components/order/AggressiveModeControl";

function parseNum(raw: unknown): number {
  if (typeof raw === "number" && Number.isFinite(raw)) return raw;
  if (typeof raw === "string") {
    const n = parseFloat(raw.replace(/,/g, ""));
    return Number.isFinite(n) ? n : NaN;
  }
  return NaN;
}

export default function BasketOrderPage() {
  const subscriptionHolder = useWsSubscriptionHolder();
  const [segmentExchange, setSegmentExchange] = useState<"NFO" | "BFO">("NFO");
  const [stockCode, setStockCode] = useState("");
  const [expiryDate, setExpiryDate] = useState("");
  const [legs, setLegs] = useState<StrategyLeg[]>([]);
  const [priceManuallyEdited, setPriceManuallyEdited] = useState<Set<string>>(
    () => new Set(),
  );
  const [executePreviewOpen, setExecutePreviewOpen] = useState(false);
  /** null until the user flips the mode toggle — until then it follows basket composition. */
  const [userScaleMode, setUserScaleMode] = useState<ScaleMode | null>(null);
  const [scaleMarginLakh, setScaleMarginLakh] = useState("");
  const [scalePremiumRupees, setScalePremiumRupees] = useState("");
  const [scaleIncludeElm, setScaleIncludeElm] = useState(true);
  const [scaling, setScaling] = useState(false);
  const [scaleWarning, setScaleWarning] = useState<string | null>(null);
  const [ivShockPct, setIvShockPct] = useState(0);
  const [showGreeks, setShowGreeks] = useState(false);
  const [showToday, setShowToday] = useState(true);
  const [chainModalOpen, setChainModalOpen] = useState(false);
  /** Frozen at the moment the modal opens — doesn't track subsequent live-quote polls. */
  const [chainSnapshot, setChainSnapshot] = useState<{
    chainSuccess: ChainSuccess;
    stockCode: string;
    expiryDate: string;
  } | null>(null);

  const resetBasket = useCallback(() => {
    setLegs([]);
    setPriceManuallyEdited(new Set());
    setChainModalOpen(false);
    setScaleWarning(null);
  }, []);

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
  const strikeSet = useMemo(() => new Set(strikes), [strikes]);
  const spot = chainSuccess?.spot_price ?? null;
  const atmStrike = chainSuccess?.atm_strike ?? null;
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
    if (!chainRows.length) return 1;
    const row = chainRows[0];
    const ls = parseNum(row.call?.lot_size) || parseNum(row.put?.lot_size);
    return Number.isFinite(ls) && ls > 0 ? Math.round(ls) : 1;
  }, [chainRows]);

  const lotSize =
    chainSuccess?.lot_size != null && chainSuccess.lot_size > 0
      ? Math.round(chainSuccess.lot_size)
      : chainLotSize;

  const atmIv = useMemo(() => {
    if (!chainSuccess) return null;
    const T = expiryDisplayToYears(expiryDate || "01-Jan-2099");
    return atmSigmaFromChain(chainSuccess, T);
  }, [chainSuccess, expiryDate]);

  const sigmaSmiles = useMemo(() => {
    if (!chainSuccess) return null;
    const T = expiryDisplayToYears(expiryDate || "01-Jan-2099");
    return buildSigmaSmiles(chainSuccess, T);
  }, [chainSuccess, expiryDate]);

  const section1Complete = Boolean(stockCode.trim() && expiryDate.trim());
  const chainInitiallyLoaded = !chainQ.isPending && chainQ.data != null;
  const section2Ready =
    section1Complete &&
    spot != null &&
    chainInitiallyLoaded &&
    chainRows.length > 0;

  const onStrikeChange = useCallback(
    (legId: string, strike: number) => {
      setLegs((prev) => {
        const updated = prev.map((x) =>
          x.id === legId ? { ...x, strike } : x,
        );
        if (priceManuallyEdited.has(legId)) return updated;
        const leg = updated.find((l) => l.id === legId);
        if (!leg || leg.aggressiveLimit) return updated;
        const prem = premiumFromChainRow(chainRows, strike, leg.right);
        if (prem == null) return updated;
        return updated.map((x) =>
          x.id === legId ? { ...x, premiumPerUnit: prem } : x,
        );
      });
    },
    [chainRows, priceManuallyEdited],
  );

  const onRightChange = useCallback(
    (legId: string, right: OptionRight) => {
      setLegs((prev) => {
        const updated = prev.map((x) =>
          x.id === legId ? { ...x, right } : x,
        );
        if (priceManuallyEdited.has(legId)) return updated;
        const leg = updated.find((l) => l.id === legId);
        if (!leg || leg.aggressiveLimit) return updated;
        const prem = premiumFromChainRow(chainRows, leg.strike, right);
        if (prem == null) return updated;
        return updated.map((x) =>
          x.id === legId ? { ...x, premiumPerUnit: prem } : x,
        );
      });
    },
    [chainRows, priceManuallyEdited],
  );

  const onSideChange = useCallback((legId: string, side: OrderSide) => {
    setLegs((prev) =>
      prev.map((x) => (x.id === legId ? { ...x, side } : x)),
    );
  }, []);

  const onPriceChange = useCallback(
    (legId: string, premiumPerUnit: number | undefined) => {
      setPriceManuallyEdited((prev) => {
        const next = new Set(prev);
        next.add(legId);
        return next;
      });
      setLegs((prev) =>
        prev.map((x) =>
          x.id === legId ? { ...x, premiumPerUnit } : x,
        ),
      );
    },
    [],
  );

  const onAggressiveChange = useCallback(
    (legId: string, checked: boolean) => {
      setPriceManuallyEdited((prev) => {
        if (!prev.has(legId)) return prev;
        const next = new Set(prev);
        next.delete(legId);
        return next;
      });
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

  const onAddLeg = useCallback(() => {
    const leg = createBlankLeg(atmStrike, strikes);
    const prem = premiumFromChainRow(chainRows, leg.strike, leg.right);
    if (prem != null) {
      leg.premiumPerUnit = prem;
    }
    setLegs((prev) => [...prev, leg]);
  }, [atmStrike, strikes, chainRows]);

  const handleStrategyChainBuySell = useCallback(
    (side: OrderSide, row: ChainRow, right: OptionRight) => {
      setLegs((prev) => appendLegFromChainRow(prev, side, row, right));
    },
    [],
  );

  const buildYourOwnSlots = useMemo(
    () => buildYourOwnAddedSlots(legs, stockCode, expiryDate),
    [legs, stockCode, expiryDate],
  );

  const legBuySellRatios = useMemo(() => {
    const map: Record<string, number | string | null> = {};
    for (const l of legs) {
      map[l.id] = buySellRatioFromChainRow(chainRows, l.strike, l.right);
    }
    return map;
  }, [legs, chainRows]);

  const marginCalc = useOnDemandBasketMargin({
    legs,
    lotSize,
    stockCode,
    exchangeCode: segmentExchange,
    expiryDate,
    spot,
  });

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

  /** Legs with a resolved per-unit price — aggressive legs fall back to the last-known chain mid. */
  const scaleLegs = useMemo<ScaleLeg[]>(
    () =>
      legs.map((l) => ({
        lots: l.lots,
        side: l.side,
        unitPrice: l.aggressiveLimit
          ? premiumFromChainRow(chainRows, l.strike, l.right)
          : (l.premiumPerUnit ?? premiumFromChainRow(chainRows, l.strike, l.right)),
      })),
    [legs, chainRows],
  );

  const baseNetDebit = useMemo(
    () => computeNetDebit(scaleLegs, lotSize),
    [scaleLegs, lotSize],
  );

  const activeLegCount = useMemo(
    () => legs.filter((l) => l.lots > 0).length,
    [legs],
  );
  const hasActiveSellLeg = useMemo(
    () => legs.some((l) => l.lots > 0 && l.side === "Sell"),
    [legs],
  );
  // Premium is a mid estimate whenever an active leg is aggressive (no fixed
  // price) or otherwise unpriced — surface that so the overshoot risk is visible.
  const premiumEstimated = useMemo(
    () =>
      hasUnpricedActiveLeg(scaleLegs) ||
      legs.some((l) => l.lots > 0 && l.aggressiveLimit),
    [scaleLegs, legs],
  );

  const suggestedScaleMode = suggestScaleMode(baseNetDebit);
  const scaleMode: ScaleMode = userScaleMode ?? suggestedScaleMode;
  const marginModeAvailable = hasActiveSellLeg;
  const premiumModeAvailable = baseNetDebit > 0;

  // Set every active leg to `k` copies of the strategy's irreducible unit
  // (its lots ÷ the active-leg GCD), so scaling snaps to the finest lot ratio
  // that preserves the strategy — letting the basket scale down as well as up.
  const applyUnitMultiplier = useCallback(
    (gcdLots: number, k: number): StrategyLeg[] => {
      const scaled = legs.map((l) =>
        l.lots > 0 ? { ...l, lots: Math.round(l.lots / gcdLots) * k } : l,
      );
      setLegs(scaled);
      return scaled;
    },
    [legs],
  );

  const handleScale = useCallback(async () => {
    setScaleWarning(null);
    if (activeLegCount === 0) return;

    // Scale in units of the strategy's irreducible basket (lots ÷ GCD), so the
    // target can be met by scaling down as well as up. gcdLots ≥ 1 here.
    const gcdLots = activeLotsGcd(legs);

    if (scaleMode === "premium") {
      if (!premiumModeAvailable) {
        setScaleWarning(
          "This basket collects net premium (a credit) — switch to Margin to scale it.",
        );
        return;
      }
      const target = parseNum(scalePremiumRupees);
      const unitDebit = baseNetDebit / gcdLots;
      const res = computeScaleMultiplier(unitDebit, target);
      if (!res.ok) {
        setScaleWarning(
          res.reason === "underflow"
            ? `A single basket already costs ${formatIndianMoneyCompact(unitDebit)} in premium, which exceeds your target of ${formatIndianMoneyCompact(target)}.`
            : "Enter a target premium debit greater than zero.",
        );
        return;
      }
      applyUnitMultiplier(gcdLots, res.k);
      return;
    }

    // Margin mode.
    if (!marginModeAvailable) {
      setScaleWarning(
        "This basket has no short (margin-bearing) leg — switch to Premium to scale it.",
      );
      return;
    }
    const target = parseNum(scaleMarginLakh) * 100000;
    if (!(Number.isFinite(target) && target > 0)) {
      setScaleWarning("Enter a target margin greater than zero.");
      return;
    }
    setScaling(true);
    try {
      const data = await fetchRealBasketMargins({
        legs,
        stockCode,
        exchangeCode: segmentExchange,
        expiryDate,
        lotSize,
        spot,
      });
      const elmUnavailable = scaleIncludeElm && data.elmRequirement == null;
      const base =
        data.spanMargin +
        (scaleIncludeElm && data.elmRequirement != null ? data.elmRequirement : 0);
      // Per-unit margin from the current basket's real margin — the same
      // linear approximation used when scaling up by whole multiples.
      const unitBase = base / gcdLots;
      const res = computeScaleMultiplier(unitBase, target);
      if (!res.ok) {
        setScaleWarning(
          res.reason === "underflow"
            ? `A single basket already needs ${formatIndianMoneyCompact(unitBase)} in margin, which exceeds your target of ${formatIndianMoneyCompact(target)}.`
            : "Could not compute a base margin for this basket.",
        );
        return;
      }
      const scaled = applyUnitMultiplier(gcdLots, res.k);
      // Confirm-recalc against the scaled legs so the totals show true deployed margin.
      marginCalc.calculateFor(scaled);
      if (elmUnavailable) {
        setScaleWarning(
          "Basket ELM wasn't available, so this was scaled against SPAN margin alone.",
        );
      }
    } catch (err) {
      setScaleWarning(
        err instanceof Error ? err.message : "Failed to calculate margin for scaling.",
      );
    } finally {
      setScaling(false);
    }
  }, [
    activeLegCount,
    scaleMode,
    premiumModeAvailable,
    marginModeAvailable,
    scalePremiumRupees,
    scaleMarginLakh,
    baseNetDebit,
    scaleIncludeElm,
    legs,
    stockCode,
    segmentExchange,
    expiryDate,
    lotSize,
    spot,
    applyUnitMultiplier,
    marginCalc,
  ]);

  const scaleDisabled =
    scaling ||
    activeLegCount === 0 ||
    (scaleMode === "premium" ? !premiumModeAvailable : !marginModeAvailable);

  const { chunkQty, setChunkQty, defaultsQuery: chunkDefaultsQ, chunkReady } =
    useBreakChunkQty({
      stockCode,
      exchangeCode: segmentExchange,
      expiryDisplay: expiryDate,
      enabled: executePreviewOpen,
    });

  const aggressiveControls = useAggressiveOrderControls();
  const anyAggressiveLeg = legs.some((l) => l.lots > 0 && l.aggressiveLimit);

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

  const executeDisabled = useMemo(() => {
    if (!stockCode || !expiryDate) return true;
    const active = legs.filter((l) => l.lots > 0);
    if (!active.length) return true;
    return active.some((l) => {
      if (!strikeSet.has(l.strike)) return true;
      if (l.aggressiveLimit) return false;
      const p = l.premiumPerUnit;
      return !(Number.isFinite(p) && (p ?? 0) > 0);
    });
  }, [stockCode, expiryDate, legs, strikeSet]);

  const onSegmentChange = (ex: "NFO" | "BFO") => {
    setSegmentExchange(ex);
    setStockCode("");
    setExpiryDate("");
    resetBasket();
  };

  const openChainModal = useCallback(() => {
    if (!chainSuccess) return;
    setChainSnapshot({ chainSuccess, stockCode, expiryDate });
    setChainModalOpen(true);
  }, [chainSuccess, stockCode, expiryDate]);

  const closeChainModal = useCallback(() => {
    setChainModalOpen(false);
  }, []);

  return (
    <AppShell>
      <RevokedTradingPageGuard>
        <div className="mx-auto max-w-[1240px] space-y-5">
          <header className="flex items-end justify-between gap-4">
            <div>
              <h1 className="flex items-center gap-2 text-[22px] font-bold tracking-tight text-foreground">
                Basket Order
                <NewFeatureBadge />
              </h1>
              <p className="mt-2 max-w-2xl text-sm leading-relaxed text-muted">
                Build a multi-leg option basket, simulate payoff, and execute all
                legs at once.{" "}
                <HelpLink topicId="basket-order" className="text-sm">
                  Learn more
                </HelpLink>
              </p>
            </div>

            {chainQuoteMeta ? (
              <QuoteSourceBadge meta={chainQuoteMeta} variant="compact" />
            ) : null}
          </header>

          <section className="relative z-20 divide-y divide-border-soft rounded-[14px] border border-border bg-panel">
            <div className="space-y-4 p-5">
              <h2 className={sb.sectionTitle}>1. Underlying &amp; Expiry</h2>
              <div
                className="flex min-h-[2.75rem] flex-col overflow-visible rounded-[9px] sm:flex-row sm:items-start"
                role="toolbar"
              >
                <div className="flex shrink-0 items-center border-b border-border-soft px-2 py-2 sm:border-b-0 sm:border-r sm:py-0 sm:ps-2.5 sm:pe-2">
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
                        resetBasket();
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
                      resetBasket();
                    }}
                  />
                </div>
              </div>
            </div>

            <SectionGate locked={!section1Complete}>
              <div className="border-b border-border-soft">
                <BasketLegsPanel
                  sectionLabel="2. Legs"
                  strikes={strikes}
                  chainBusy={chainQ.isFetching}
                  chainReady={section2Ready}
                  onPickFromChain={openChainModal}
                  lotSize={lotSize}
                  legs={legs}
                  onLegsChange={setLegs}
                  onAddLeg={onAddLeg}
                  onStrikeChange={onStrikeChange}
                  onRightChange={onRightChange}
                  onSideChange={onSideChange}
                  onPriceChange={onPriceChange}
                  onAggressiveChange={onAggressiveChange}
                  legMargins={marginCalc.legMargins}
                  legBuySellRatios={legBuySellRatios}
                  totalsNetPremium={totalsNetPremium}
                  totalsMargin={marginCalc.totalsMargin}
                  onExecute={() => setExecutePreviewOpen(true)}
                  executeDisabled={executeDisabled}
                  addLegDisabled={!section2Ready}
                  marginError={marginCalc.error}
                  onCalculateMargins={marginCalc.calculate}
                  calculatingMargins={marginCalc.isCalculating}
                  calculateMarginsDisabled={marginCalc.calculateDisabled}
                  scaleControls={{
                    mode: scaleMode,
                    onModeChange: setUserScaleMode,
                    marginModeAvailable,
                    premiumModeAvailable,
                    marginLakh: scaleMarginLakh,
                    onMarginLakhChange: setScaleMarginLakh,
                    premiumRupees: scalePremiumRupees,
                    onPremiumRupeesChange: setScalePremiumRupees,
                    includeElm: scaleIncludeElm,
                    onIncludeElmChange: setScaleIncludeElm,
                    premiumEstimated,
                    onScale: handleScale,
                    scaling,
                    disabled: scaleDisabled,
                    warning: scaleWarning,
                  }}
                />
                <AggressiveModeControl
                  controls={aggressiveControls}
                  visible={anyAggressiveLeg}
                  className="mt-3"
                />
              </div>

              <BasketPayoffPanel
                sectionLabel="3. Payoff simulation"
                legs={legs}
                spot={spot}
                atmIv={atmIv}
                sigmaSmiles={sigmaSmiles}
                expiryDate={expiryDate}
                lotSize={lotSize}
                ivShockPct={ivShockPct}
                onIvShockChange={setIvShockPct}
                showToday={showToday}
                onShowTodayChange={setShowToday}
                showGreeks={showGreeks}
                onShowGreeksChange={setShowGreeks}
              />
            </SectionGate>
          </section>

          <ChainBuildStatus visible={chainLoading} phase={chainBuildPhase} />
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
          open={chainModalOpen}
          onClose={closeChainModal}
          titleId="basket-option-chain-title"
          zIndexClass="z-[120]"
          panelClassName="flex max-h-[88vh] w-full !max-w-[min(96vw,74rem)] flex-col overflow-hidden rounded-[13px] border border-border bg-elevated shadow-pop"
        >
          <div className="flex items-start justify-between gap-3 border-b border-border-soft px-5 py-4">
            <div className="min-w-0 flex-1">
              <h3 id="basket-option-chain-title" className="text-[15px] font-bold text-foreground">
                Pick from option chain
              </h3>
              <p className="mt-1 text-xs text-muted">
                {chainSnapshot
                  ? `${chainSnapshot.stockCode} · ${chainSnapshot.expiryDate} — snapshot as of when you opened this. Close and reopen for fresh quotes.`
                  : "Close and reopen for fresh quotes."}
              </p>
            </div>
            <button
              type="button"
              onClick={closeChainModal}
              aria-label="Close"
              className="-m-1 flex size-8 shrink-0 items-center justify-center rounded-lg text-muted transition hover:bg-border-soft hover:text-foreground"
            >
              <CloseIcon />
            </button>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
            {chainSnapshot ? (
              <BuildYourOwnChainSection
                chainSuccess={chainSnapshot.chainSuccess}
                isFetching={false}
                isError={false}
                error={null}
                chainStatus={200}
                chainError={undefined}
                stockCode={chainSnapshot.stockCode}
                expiryDate={chainSnapshot.expiryDate}
                onStrategyBuySell={handleStrategyChainBuySell}
                isStrategySlotAdded={(strike, right, side) =>
                  buildYourOwnSlots.has(
                    buildYourOwnSlotKey(
                      chainSnapshot.stockCode,
                      chainSnapshot.expiryDate,
                      strike,
                      right,
                      side,
                    ),
                  )
                }
              />
            ) : null}
          </div>
        </Modal>
      </RevokedTradingPageGuard>
    </AppShell>
  );
}

function CloseIcon() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M18 6 6 18M6 6l12 12" />
    </svg>
  );
}
