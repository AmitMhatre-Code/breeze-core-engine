"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AppShell } from "@/components/layout/AppShell";
import { HelpLink } from "@/components/help/HelpLink";
import { NewFeatureBadge } from "@/components/ui/NewFeatureBadge";
import { RevokedTradingPageGuard } from "@/components/license/RevokedTradingPageGuard";
import { BasketLegsPanel } from "@/components/basket-order/BasketLegsPanel";
import { OptionChainUnderlyingSearch } from "@/components/order/OptionChainUnderlyingSearch";
import { OrderExecutionConfirmDialog } from "@/components/order/OrderExecutionConfirmDialog";
import { BuildYourOwnChainSection } from "@/components/strategy-builder/BuildYourOwnChainSection";
import { ExpirySelectPill } from "@/components/strategy-builder/ExpirySelectPill";
import { SectionGate } from "@/components/strategy-builder/SectionGate";
import { StrategyPayoffPanel } from "@/components/strategy-builder/StrategyPayoffPanel";
import { apiClient } from "@/lib/api-client";
import { chainQueryOptions } from "@/lib/strategy-builder/chain-query";
import { quoteMetaFromChain } from "@/lib/quote-source";
import { useWsSubscriptionHolder } from "@/lib/use-ws-subscription-holder";
import {
  appendLegFromChainRow,
  buildYourOwnAddedSlots,
  buildYourOwnSlotKey,
} from "@/lib/strategy-builder/build-your-own";
import {
  createBlankLeg,
  premiumFromChainRow,
  strikesFromChain,
} from "@/lib/strategy-builder/chain-quote";
import { atmSigmaFromChain } from "@/lib/strategy-builder/chainIv";
import { expiryDisplayToYears, sortExpiryDatesAsc } from "@/lib/strategy-builder/expiry";
import {
  buildLegMarginsFromPortfolio,
  computeNetSpanMargin,
  fetchSpanBaselineSheet,
  fetchSpanPortfolioMargin,
  portfolioMarginFromResponse,
} from "@/lib/strategy-builder/span-baseline";
import { sb } from "@/lib/strategy-builder/ui";
import type {
  ChainRow,
  OptionRight,
  OrderSide,
  StrategyLeg,
  UnderlyingsApiResponse,
} from "@/lib/strategy-builder/types";
import { useBreakChunkQty } from "@/lib/use-break-chunk-qty";

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
  const [ivShockPct, setIvShockPct] = useState(0);
  const [showGreeks, setShowGreeks] = useState(false);
  const [showToday, setShowToday] = useState(true);
  const [showOptionChain, setShowOptionChain] = useState(false);
  const optionChainRef = useRef<HTMLElement>(null);

  const resetBasket = useCallback(() => {
    setLegs([]);
    setPriceManuallyEdited(new Set());
    setShowOptionChain(false);
  }, []);

  const uq = useQuery({
    queryKey: ["strategy-builder", "underlyings", segmentExchange],
    queryFn: () =>
      apiClient.get<UnderlyingsApiResponse>(
        `/strategy-builder/underlyings?exchange_code=${segmentExchange}`,
      ),
  });

  const chainQ = useQuery({
    ...chainQueryOptions({
      queryKeyPrefix: ["strategy-builder"],
      stock_code: stockCode,
      expiry_date: expiryDate,
      exchange_code: segmentExchange,
      subscription_holder: subscriptionHolder,
    }),
  });

  const chainSuccess =
    chainQ.data?.Status === 200 ? chainQ.data.Success : null;
  const chainRows = chainSuccess?.chain_rows ?? [];
  const strikes = useMemo(() => strikesFromChain(chainRows), [chainRows]);
  const strikeSet = useMemo(() => new Set(strikes), [strikes]);
  const spot = chainSuccess?.spot_price ?? null;
  const atmStrike = chainSuccess?.atm_strike ?? null;
  const chainQuoteMeta = useMemo(
    () => quoteMetaFromChain(chainSuccess),
    [chainSuccess],
  );

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

  const section1Complete = Boolean(stockCode.trim() && expiryDate.trim());
  const section2Ready =
    section1Complete && spot != null && !chainQ.isFetching && chainRows.length > 0;

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

  const legsWithQtyForMargin = useMemo(
    () => legs.filter((l) => l.lots > 0),
    [legs],
  );

  const spanBaselineQ = useQuery({
    queryKey: [
      "basket-order",
      "span-baseline",
      segmentExchange,
      stockCode,
      expiryDate,
    ],
    queryFn: ({ signal }) =>
      fetchSpanBaselineSheet(segmentExchange, stockCode, expiryDate, signal),
    enabled: Boolean(stockCode.trim() && expiryDate.trim()),
    staleTime: Infinity,
  });

  const spanSheet = spanBaselineQ.data;

  const portfolioMarginLegKey = useMemo(
    () =>
      JSON.stringify(
        legsWithQtyForMargin.map((l) => [
          l.id,
          l.strike,
          l.right,
          l.side,
          l.lots,
        ]),
      ),
    [legsWithQtyForMargin],
  );

  const portfolioMarginQ = useQuery({
    queryKey: [
      "basket-order",
      "span-portfolio-margin",
      segmentExchange,
      stockCode,
      expiryDate,
      portfolioMarginLegKey,
      spot,
      atmIv,
    ],
    queryFn: ({ signal }) =>
      fetchSpanPortfolioMargin(
        {
          exchange_code: segmentExchange,
          stock_code: stockCode.trim(),
          expiry_date: expiryDate.trim(),
          legs: legsWithQtyForMargin.map((l) => ({
            strike_price: String(l.strike),
            right: l.right,
            action: l.side,
            quantity: String(Math.round(l.lots * lotSize)),
          })),
          spot: spot!,
          iv: atmIv,
        },
        signal,
      ),
    enabled:
      Boolean(stockCode.trim() && expiryDate.trim()) &&
      legsWithQtyForMargin.length > 0 &&
      spot != null &&
      spot > 0,
    staleTime: 30_000,
  });

  const fallbackSpanMargin = useMemo(
    () => computeNetSpanMargin(spanSheet, legs, lotSize),
    [spanSheet, legs, lotSize],
  );

  const spanMargin = useMemo(
    () =>
      portfolioMarginFromResponse(portfolioMarginQ.data, fallbackSpanMargin),
    [portfolioMarginQ.data, fallbackSpanMargin],
  );

  const legMargins = useMemo(
    () =>
      buildLegMarginsFromPortfolio(
        spanSheet,
        legs,
        lotSize,
        portfolioMarginQ.data?.Success ?? null,
        spanBaselineQ.isFetching || portfolioMarginQ.isFetching,
      ),
    [
      spanSheet,
      legs,
      lotSize,
      portfolioMarginQ.data,
      spanBaselineQ.isFetching,
      portfolioMarginQ.isFetching,
    ],
  );

  const basketMarginWarnings = useMemo(() => {
    const warnings: string[] = [];
    if (
      spanSheet &&
      !spanSheet.found &&
      legsWithQtyForMargin.length > 0
    ) {
      warnings.push("Contract missing in Exchange Risk Baseline.");
    }
    const apiWarnings = portfolioMarginQ.data?.Success?.warnings ?? [];
    for (const w of apiWarnings) {
      if (w && !warnings.includes(w)) warnings.push(w);
    }
    return warnings;
  }, [spanSheet, legsWithQtyForMargin.length, portfolioMarginQ.data]);

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

  const totalsMargin = useMemo(() => {
    const hasPositiveLots = legsWithQtyForMargin.length > 0;
    return {
      hasPositiveLots,
      isFetching: spanBaselineQ.isFetching || portfolioMarginQ.isFetching,
      netMargin: hasPositiveLots ? spanMargin : null,
      marginBenefit: portfolioMarginQ.data?.Success?.margin_benefit ?? null,
    };
  }, [
    legsWithQtyForMargin.length,
    spanBaselineQ.isFetching,
    portfolioMarginQ.isFetching,
    portfolioMarginQ.data,
    spanMargin,
  ]);

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
          premiumPerUnit: l.aggressiveLimit ? 0 : (l.premiumPerUnit ?? 0),
          aggressiveLimit: l.aggressiveLimit ?? false,
        })),
    [legs, lotSize],
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

  const legsSectionNumber = showOptionChain ? 3 : 2;
  const payoffSectionNumber = showOptionChain ? 4 : 3;

  const handleShowOptionChain = useCallback(() => {
    setShowOptionChain(true);
  }, []);

  const handleHideOptionChain = useCallback(() => {
    setShowOptionChain(false);
  }, []);

  useEffect(() => {
    if (!showOptionChain) return;
    const id = requestAnimationFrame(() => {
      optionChainRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
    return () => cancelAnimationFrame(id);
  }, [showOptionChain]);

  return (
    <AppShell>
      <RevokedTradingPageGuard>
        <div className="space-y-5">
          <header>
            <h1 className="flex items-center gap-2 text-xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
              Basket Order
              <NewFeatureBadge />
            </h1>
            <p className="mt-2 max-w-2xl text-sm leading-relaxed text-zinc-600 dark:text-zinc-400">
              Build a multi-leg option basket, simulate payoff, and execute all
              legs at once.{" "}
              <HelpLink topicId="basket-order" className="text-sm">
                Learn more
              </HelpLink>
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
                {uq.isLoading ? (
                  <span className="text-xs app-text-muted animate-pulse">
                    Loading underlyings…
                  </span>
                ) : (
                  <OptionChainUnderlyingSearch
                    variant="ticker"
                    chainBar
                    underlyings={uq.data?.underlyings ?? []}
                    value={stockCode}
                    disabled={uq.isLoading}
                    spot={spot}
                    quoteMeta={chainQuoteMeta}
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
                  tone="darkToolbar"
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
          </section>

          {showOptionChain && section2Ready ? (
            <section
              ref={optionChainRef}
              id="basket-option-chain"
              className={`${sb.section} space-y-4`}
            >
              <h2 className={sb.sectionTitle}>2. Option chain</h2>
              <BuildYourOwnChainSection
                chainSuccess={chainSuccess ?? null}
                isFetching={chainQ.isFetching}
                isError={chainQ.isError}
                error={chainQ.error}
                chainStatus={chainQ.data?.Status}
                chainError={chainQ.data?.Error}
                stockCode={stockCode}
                expiryDate={expiryDate}
                onStrategyBuySell={handleStrategyChainBuySell}
                isStrategySlotAdded={(strike, right, side) =>
                  buildYourOwnSlots.has(
                    buildYourOwnSlotKey(
                      stockCode,
                      expiryDate,
                      strike,
                      right,
                      side,
                    ),
                  )
                }
              />
            </section>
          ) : null}

          <SectionGate locked={!section1Complete}>
            <BasketLegsPanel
              sectionNumber={legsSectionNumber}
              strikes={strikes}
              chainBusy={chainQ.isFetching}
              chainReady={section2Ready}
              showOptionChain={showOptionChain}
              onShowOptionChain={handleShowOptionChain}
              onHideOptionChain={handleHideOptionChain}
              lotSize={lotSize}
              legs={legs}
              onLegsChange={setLegs}
              onAddLeg={onAddLeg}
              onStrikeChange={onStrikeChange}
              onRightChange={onRightChange}
              onSideChange={onSideChange}
              onPriceChange={onPriceChange}
              legMargins={legMargins}
              spanBaselineLoading={spanBaselineQ.isFetching}
              totalsNetPremium={totalsNetPremium}
              totalsMargin={totalsMargin}
              onExecute={() => setExecutePreviewOpen(true)}
              executeDisabled={executeDisabled}
              addLegDisabled={!section2Ready}
              quoteMeta={chainQuoteMeta}
            />

            <StrategyPayoffPanel
              sectionTitle={`${payoffSectionNumber}. Payoff simulation`}
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
              marginFetching={spanBaselineQ.isFetching || portfolioMarginQ.isFetching}
              marginQtyStale={false}
              onRefreshMargin={() => {
                void spanBaselineQ.refetch();
                void portfolioMarginQ.refetch();
              }}
              marginError={
                spanBaselineQ.isError
                  ? String(spanBaselineQ.error ?? "SPAN baseline unavailable")
                  : portfolioMarginQ.isError
                    ? String(
                        portfolioMarginQ.error ??
                          "Portfolio SPAN unavailable",
                      )
                    : null
              }
              marginWarnings={basketMarginWarnings}
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
          quoteMeta={chainQuoteMeta}
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
