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
import { ExchangeFlipToggle } from "@/components/shared/order/ExchangeFlipToggle";
import { OrderExecutionConfirmDialog } from "@/components/shared/order/OrderExecutionConfirmDialog";
import { BuildYourOwnChainSection } from "@/components/strategy-builder/BuildYourOwnChainSection";
import { ExpirySelectPill } from "@/components/shared/order/ExpirySelectPill";
import { SectionGate } from "@/components/strategy-builder/SectionGate";
import { apiClient } from "@/lib/api-client";
import { chainQueryOptions } from "@/lib/strategy-builder/chain-query";
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
  ChainSuccess,
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

  const legsWithQtyForMargin = useMemo(
    () => legs.filter((l) => l.lots > 0),
    [legs],
  );

  const legBuySellRatios = useMemo(() => {
    const map: Record<string, number | string | null> = {};
    for (const l of legs) {
      map[l.id] = buySellRatioFromChainRow(chainRows, l.strike, l.right);
    }
    return map;
  }, [legs, chainRows]);

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
        }))
        .sort((a, b) => a.strike - b.strike),
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
          <header>
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
          </header>

          {chainQuoteMeta ? (
            <div className="flex justify-end">
              <QuoteSourceBadge meta={chainQuoteMeta} variant="compact" />
            </div>
          ) : null}

          <section className="rounded-[14px] border border-border bg-panel">
            <div className="relative z-20 flex flex-wrap items-center gap-3.5 border-b border-border-soft px-[18px] py-3.5">
              <span className="shrink-0 text-hint font-bold uppercase tracking-[.07em] text-faint">
                1. Underlying
              </span>
              {/* <div
                className="flex min-h-[2.75rem] w-full flex-1 flex-col overflow-visible rounded-[9px] bg-panel2 sm:w-auto sm:flex-row sm:items-center"
                role="toolbar"
              > */}
                <div className="flex shrink-0 items-center border-b border-border-soft px-2 py-2 sm:border-b-0 sm:border-r sm:py-0 sm:ps-2.5 sm:pe-2">
                  <ExchangeFlipToggle value={segmentExchange} onChange={onSegmentChange} />
                </div>
                <div className="relative z-30 flex min-w-0 max-w-[min(100%,34rem)] flex-1 items-center px-3 py-2 sm:border-r sm:border-border-soft">
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
                    dates={expiryOptions}
                    value={expiryDate}
                    disabled={!stockCode}
                    onChange={(d) => {
                      setExpiryDate(d);
                      resetBasket();
                    }}
                  />
                </div>
              {/* </div> */}
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
                  legMargins={legMargins}
                  legBuySellRatios={legBuySellRatios}
                  spanBaselineLoading={spanBaselineQ.isFetching}
                  totalsNetPremium={totalsNetPremium}
                  totalsMargin={totalsMargin}
                  onExecute={() => setExecutePreviewOpen(true)}
                  executeDisabled={executeDisabled}
                  addLegDisabled={!section2Ready}
                  marginWarnings={basketMarginWarnings}
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
                />
              </div>

              <BasketPayoffPanel
                sectionLabel="3. Payoff simulation"
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
