"use client";

import {
  startTransition,
  Suspense,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { AppShell } from "@/components/layout/AppShell";
import { RevokedTradingPageGuard } from "@/components/license/RevokedTradingPageGuard";
import { ManualContractFieldWarningDialog } from "@/components/order/ManualContractFieldWarningDialog";
import { AggressiveLimitOrderField } from "@/components/order/AggressiveLimitOrderField";
import { OptionTypeBadge } from "@/components/shared/badges/OptionTypeBadge";
import { OrderSideBadge } from "@/components/shared/badges/OrderSideBadge";
import { QuoteSourceBadge } from "@/components/shared/market-data/QuoteSourceBadge";
import { useOrderConfirm } from "@/components/shared/order/OrderConfirmProvider";
import { OptionChainUnderlyingSearch } from "@/components/shared/order/OptionChainUnderlyingSearch";
import { ExchangeFlipToggle } from "@/components/shared/order/ExchangeFlipToggle";
import { ExpirySelectPill } from "@/components/shared/order/ExpirySelectPill";
import { StrikeSelectPill } from "@/components/shared/order/StrikeSelectPill";
import { apiClient } from "@/lib/api-client";
import { formatIndianMoneyCompact } from "@/lib/format-money-in";
import { isValidExpiryDisplay, sortExpiryDatesAsc } from "@/lib/strategy-builder/expiry";
import { snapQuantityToLotMultiple } from "@/lib/strategy-builder/leg-ui-helpers";
import {
  consumePlaceOrderClonePayload,
  placeOrderPrefillFromSearchParams,
} from "@/lib/place-order-clone";
import { chainQueryOptions } from "@/lib/strategy-builder/chain-query";
import {
  chainIsLoading,
} from "@/lib/strategy-builder/chain-loading";
import { quoteMetaFromChain } from "@/lib/quote-source";
import {
  ChainBuildStatus,
  inferChainBuildPhase,
} from "@/components/shared/market-data/ChainBuildStatus";
import { sb } from "@/lib/strategy-builder/ui";
import { useWsSubscriptionHolder } from "@/lib/use-ws-subscription-holder";
import type {
  ChainRow,
  ChainSuccess,
  MarginApiResponse,
  OptionRight,
  OrderSide,
  UnderlyingsApiResponse,
} from "@/lib/strategy-builder/types";

type Segment = "NFO" | "BFO";
type FieldMode = "dropdown" | "manual";

const fieldModeBtnClass =
  "flex shrink-0 items-center justify-center self-center rounded-md p-2 text-muted transition hover:bg-panel2 hover:text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/35 disabled:pointer-events-none disabled:opacity-40";

function EditIcon() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M12 20h9" />
      <path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" />
    </svg>
  );
}

function ListIcon() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M8 6h13" />
      <path d="M8 12h13" />
      <path d="M8 18h13" />
      <path d="M3 6h.01" />
      <path d="M3 12h.01" />
      <path d="M3 18h.01" />
    </svg>
  );
}

function RefreshIcon({ spinning }: { spinning?: boolean }) {
  return (
    <svg
      width="12"
      height="12"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      className={spinning ? "animate-spin" : ""}
    >
      <path d="M21 12a9 9 0 1 1-3-6.7L21 8" />
      <path d="M21 3v5h-5" />
    </svg>
  );
}

function BuyArrowIcon() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.4"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M12 19V5" />
      <path d="m5 12 7-7 7 7" />
    </svg>
  );
}

function SellArrowIcon() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.4"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M12 5v14" />
      <path d="m19 12-7 7-7-7" />
    </svg>
  );
}

function parseNum(v: unknown): number {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string") {
    const n = parseFloat(v.replace(/,/g, ""));
    return Number.isFinite(n) ? n : NaN;
  }
  return NaN;
}

function parseSpanMargin(m: MarginApiResponse | undefined): number | null {
  if (m?.Status !== 200 || !m.Success) return null;
  const v = parseNum(
    (m.Success as { span_margin_required?: unknown }).span_margin_required,
  );
  return Number.isFinite(v) ? v : null;
}

function pickOptionCell(
  row: ChainRow | undefined,
  right: OptionRight,
): Record<string, unknown> | null {
  if (!row) return null;
  const cell = right === "Call" ? row.call : row.put;
  if (cell && typeof cell === "object") return cell as Record<string, unknown>;
  return null;
}

function formatRatio(raw: unknown): string {
  if (raw === "NA" || raw === null || raw === undefined) return "—";
  const n = typeof raw === "number" ? raw : parseNum(raw);
  if (!Number.isFinite(n)) return "—";
  return n.toFixed(4);
}

function PlaceOrderPageInner() {
  const subscriptionHolder = useWsSubscriptionHolder();
  const searchParams = useSearchParams();
  const { openExecutionConfirm } = useOrderConfirm();
  const prefillConsumedRef = useRef(false);
  const [segment, setSegment] = useState<Segment>("NFO");
  const [stockCode, setStockCode] = useState("");
  const [expiryDate, setExpiryDate] = useState("");
  const [right, setRight] = useState<OptionRight>("Call");
  /** `null` = follow ATM / first strike from chain until user picks explicitly. */
  const [strikeSelection, setStrikeSelection] = useState<number | null>(null);
  const [quantity, setQuantity] = useState("");
  const [price, setPrice] = useState("");
  const [aggressiveLimit, setAggressiveLimit] = useState(false);
  const [previewSide, setPreviewSide] = useState<OrderSide>("Buy");
  /** When set, the opposite side button is disabled (clone / square-off / URL prefill). */
  const [lockedOrderSide, setLockedOrderSide] = useState<OrderSide | null>(null);
  /** Exchange, scrip, expiry, strike, option fixed — only qty, price, and locked side editable. */
  const [contractFieldsLocked, setContractFieldsLocked] = useState(false);
  const [expiryFieldMode, setExpiryFieldMode] = useState<FieldMode>("dropdown");
  const [strikeFieldMode, setStrikeFieldMode] = useState<FieldMode>("dropdown");
  const [manualEditPrompt, setManualEditPrompt] = useState<
    "expiry" | "strike" | null
  >(null);

  useEffect(() => {
    if (prefillConsumedRef.current) return;
    const fromStorage = consumePlaceOrderClonePayload();
    const fromUrl =
      fromStorage == null
        ? placeOrderPrefillFromSearchParams(searchParams)
        : null;
    const p = fromStorage ?? fromUrl;
    if (!p) return;
    prefillConsumedRef.current = true;
    startTransition(() => {
      setSegment(p.segment);
      setStockCode(p.stock_code);
      setExpiryDate(p.expiry_date);
      setRight(p.right);
      setStrikeSelection(p.strike_price);
      setQuantity(p.quantity);
      setPrice(p.price);
      setLockedOrderSide(p.action);
      setContractFieldsLocked(p.lock_contract_fields === true);
    });
  }, [searchParams]);

  const uq = useQuery({
    queryKey: ["place-order", "underlyings", segment],
    queryFn: () =>
      apiClient.get<UnderlyingsApiResponse>(
        `/strategy-builder/underlyings?exchange_code=${segment}`,
      ),
    staleTime: 60_000,
  });

  // Redis-cached chain, refetched on a WS-aware interval — the single source of truth
  // for scrip details below, so no separate manual "fetch" round-trip is needed. Kept
  // enabled even when contractFieldsLocked (clone / square-off / URL prefill): only the
  // strike/expiry *pickers* are locked, live LTP is still needed for scrip details,
  // margin, and the price auto-fill below.
  const chainQ = useQuery({
    ...chainQueryOptions({
      queryKeyPrefix: ["place-order"],
      stock_code: stockCode,
      expiry_date: expiryDate,
      exchange_code: segment,
      subscription_holder: subscriptionHolder,
    }),
  });

  const chainSuccess: ChainSuccess | null =
    chainQ.data?.Status === 200 && chainQ.data.Success
      ? chainQ.data.Success
      : null;

  const expiryOptions = useMemo(() => {
    const raw = uq.data?.underlyings ?? [];
    const u = raw.find((x) => x.stock_code === stockCode);
    return sortExpiryDatesAsc(u?.expiry_dates ?? []);
  }, [uq.data?.underlyings, stockCode]);

  const strikes = useMemo(() => {
    if (!chainSuccess?.chain_rows?.length) return [];
    return chainSuccess.chain_rows
      .map((r) => r.strike_price)
      .filter((n) => Number.isFinite(n))
      .sort((a, b) => a - b);
  }, [chainSuccess]);

  const defaultStrike = useMemo(() => {
    if (!chainSuccess?.chain_rows?.length) return null;
    const atm = chainSuccess.atm_strike;
    if (atm != null && Number.isFinite(Number(atm))) return Number(atm);
    const first = chainSuccess.chain_rows[0]?.strike_price;
    return first != null && Number.isFinite(Number(first)) ? Number(first) : null;
  }, [chainSuccess]);

  const effectiveStrike =
    strikeFieldMode === "manual"
      ? strikeSelection
      : strikeSelection ?? defaultStrike;

  // Scrip details (LTP / B:S / lot size) derive live from the polling chain — updates
  // automatically as the cache refreshes, no manual fetch button.
  const cellDetails = useMemo(() => {
    if (!chainSuccess?.chain_rows?.length || effectiveStrike == null) return null;
    const row = chainSuccess.chain_rows.find(
      (r) => Math.round(r.strike_price) === Math.round(effectiveStrike),
    );
    const cell = pickOptionCell(row, right);
    if (!cell) return null;
    const ltp = parseNum(cell.ltp);
    const totalBuyQty = Math.round(parseNum(cell.total_buy_qty) || 0);
    const totalSellQty = Math.round(parseNum(cell.total_sell_qty) || 0);
    const buySellRatioLabel = formatRatio(cell.buy_sell_ratio);
    const lotFromCell = parseNum(cell.lot_size);
    const lotFromSeries =
      chainSuccess.lot_size != null ? Number(chainSuccess.lot_size) : NaN;
    const lotSize =
      Number.isFinite(lotFromCell) && lotFromCell > 0
        ? Math.round(lotFromCell)
        : Number.isFinite(lotFromSeries) && lotFromSeries > 0
          ? Math.round(lotFromSeries)
          : null;
    return {
      ltp: Number.isFinite(ltp) ? ltp : null,
      totalBuyQty,
      totalSellQty,
      buySellRatioLabel,
      lotSize,
    };
  }, [chainSuccess, effectiveStrike, right]);

  const contractKey = `${segment}|${stockCode}|${expiryDate}|${effectiveStrike ?? ""}|${right}`;
  const priceAutoFillKeyRef = useRef<string | null>(null);

  // Auto-fill the limit price from LTP once per distinct contract selection — never
  // clobbers a price the user already typed for the same contract on a later poll tick.
  useEffect(() => {
    if (aggressiveLimit) return;
    if (cellDetails?.ltp == null || cellDetails.ltp <= 0) return;
    if (priceAutoFillKeyRef.current === contractKey) return;
    priceAutoFillKeyRef.current = contractKey;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- syncs local price field from the polling chain (external system) once per contract
    setPrice(String(Number(cellDetails.ltp.toFixed(2))));
  }, [contractKey, cellDetails?.ltp, aggressiveLimit]);

  const marginEnabled = Boolean(
    stockCode.trim() && expiryDate.trim() && effectiveStrike != null && cellDetails?.lotSize,
  );

  // Margin is a real ICICI-priced calculation (not Redis-cached), so it auto-fetches only
  // when the contract itself changes — not on every chain poll tick.
  const marginQ = useQuery({
    queryKey: [
      "place-order",
      "margin",
      segment,
      stockCode,
      expiryDate,
      effectiveStrike,
      right,
      cellDetails?.lotSize,
      chainSuccess?.expiry_display,
    ],
    queryFn: async () => {
      const lotSize = cellDetails?.lotSize;
      if (!lotSize) throw new Error("Lot size unavailable");
      const prem = cellDetails?.ltp != null && cellDetails.ltp > 0 ? cellDetails.ltp : 0;
      const expiryForMargin = (chainSuccess?.expiry_display || expiryDate).trim();
      const legBase = {
        stock_code: stockCode,
        exchange_code: segment,
        expiry_date: expiryForMargin,
        product_type: "Options",
        right,
        strike_price: String(effectiveStrike),
        quantity: String(lotSize),
        price: String(prem),
      };
      const [mb, ms] = await Promise.all([
        apiClient.post<MarginApiResponse>("/strategy-builder/margin", {
          legs: [{ ...legBase, action: "Buy" as const }],
        }),
        apiClient.post<MarginApiResponse>("/strategy-builder/margin", {
          legs: [{ ...legBase, action: "Sell" as const }],
        }),
      ]);
      const marginPerLotBuy = parseSpanMargin(mb);
      const marginPerLotSell = parseSpanMargin(ms);
      let marginError: string | undefined;
      if (marginPerLotBuy == null && marginPerLotSell == null) {
        marginError =
          (mb?.Error as string | undefined) ||
          (ms?.Error as string | undefined) ||
          "Margin not available";
      }
      return { marginPerLotBuy, marginPerLotSell, marginError };
    },
    enabled: marginEnabled,
    staleTime: 15_000,
  });

  function resetContractFieldModes() {
    setExpiryFieldMode("dropdown");
    setStrikeFieldMode("dropdown");
    setManualEditPrompt(null);
  }

  function resetOrderForm() {
    setStockCode("");
    setExpiryDate("");
    setStrikeSelection(null);
    setQuantity("");
    setPrice("");
    setLockedOrderSide(null);
    setContractFieldsLocked(false);
    resetContractFieldModes();
  }

  function confirmManualEdit() {
    if (manualEditPrompt === "expiry") {
      setExpiryFieldMode("manual");
    }
    if (manualEditPrompt === "strike") {
      const prefill = strikeSelection ?? defaultStrike;
      if (prefill != null) setStrikeSelection(prefill);
      setStrikeFieldMode("manual");
    }
    setManualEditPrompt(null);
  }

  function onExpiryChange(d: string) {
    setExpiryDate(d);
    setStrikeSelection(null);
    setQuantity("");
    setPrice("");
    setLockedOrderSide(null);
  }

  const pillStretch = "!max-w-none w-full min-w-0";

  const qtyNum = Math.round(parseNum(quantity));
  const priceNum = parseNum(price);
  const lotSizeForHints = cellDetails?.lotSize ?? chainSuccess?.lot_size ?? null;
  const lots =
    lotSizeForHints && lotSizeForHints > 0 && qtyNum > 0
      ? qtyNum / lotSizeForHints
      : null;

  const previewLeg = useMemo(() => {
    if (
      effectiveStrike == null ||
      !stockCode.trim() ||
      !expiryDate.trim() ||
      qtyNum <= 0
    ) {
      return null;
    }
    if (
      !aggressiveLimit &&
      (!Number.isFinite(priceNum) || priceNum < 0)
    ) {
      return null;
    }
    const expDisplay =
      chainSuccess?.expiry_display?.trim() || expiryDate.trim();
    return {
      strike: effectiveStrike,
      right,
      side: previewSide,
      quantity: qtyNum,
      premiumPerUnit: aggressiveLimit ? 0 : priceNum,
      aggressiveLimit,
      stockCode: stockCode.trim(),
      exchangeCode: segment,
      expiryDisplay: expDisplay,
    };
  }, [
    effectiveStrike,
    stockCode,
    expiryDate,
    qtyNum,
    priceNum,
    aggressiveLimit,
    right,
    previewSide,
    segment,
    chainSuccess?.expiry_display,
  ]);

  function openPreview(side: OrderSide) {
    if (
      effectiveStrike == null ||
      !stockCode.trim() ||
      !expiryDate.trim() ||
      qtyNum <= 0
    ) {
      return;
    }
    if (
      !aggressiveLimit &&
      (!Number.isFinite(priceNum) || priceNum < 0)
    ) {
      return;
    }
    setPreviewSide(side);
    const expDisplay =
      chainSuccess?.expiry_display?.trim() || expiryDate.trim();
    openExecutionConfirm({
      stockCode: stockCode.trim(),
      exchangeCode: segment,
      expiryDisplay: expDisplay,
      quoteMeta: quoteMetaFromChain(chainSuccess),
      legs: [
        {
          strike: effectiveStrike,
          right,
          side,
          quantity: qtyNum,
          premiumPerUnit: aggressiveLimit ? 0 : priceNum,
          aggressiveLimit,
        },
      ],
    });
  }

  const chainError =
    chainQ.data && chainQ.data.Status !== 200
      ? chainQ.data.Error ?? "Chain request failed"
      : chainQ.isError
        ? "Could not load chain"
        : null;

  const spot = chainSuccess?.spot_price ?? null;
  const chainQuoteMeta = useMemo(
    () => quoteMetaFromChain(chainSuccess),
    [chainSuccess],
  );

  const chainLoading = chainIsLoading(stockCode, expiryDate, chainQ);
  const chainBuildPhase = inferChainBuildPhase({
    quoteMeta: chainQuoteMeta,
    isInitialLoad: chainLoading,
  });

  const expiryInvalid =
    expiryFieldMode === "manual" &&
    expiryDate.trim().length > 0 &&
    !isValidExpiryDisplay(expiryDate);

  const strikeDropdownDisabled =
    !stockCode ||
    !expiryDate ||
    (chainQ.isFetching && !strikes.length) ||
    !strikes.length ||
    contractFieldsLocked;

  const manualStrikeValue =
    strikeSelection != null && Number.isFinite(strikeSelection)
      ? String(strikeSelection)
      : "";

  const marginPerLotForSide =
    previewSide === "Buy" ? marginQ.data?.marginPerLotBuy : marginQ.data?.marginPerLotSell;
  const estMargin =
    marginPerLotForSide != null && lots != null ? marginPerLotForSide * lots : null;
  const orderValue =
    !aggressiveLimit && Number.isFinite(priceNum) && qtyNum > 0
      ? priceNum * qtyNum
      : null;
  const expiryDisplay = chainSuccess?.expiry_display?.trim() || expiryDate.trim();
  const isAtmStrike =
    strikeFieldMode === "dropdown" &&
    defaultStrike != null &&
    effectiveStrike != null &&
    Math.round(effectiveStrike) === Math.round(defaultStrike);

  return (
    <AppShell>
      <RevokedTradingPageGuard>
        <div className="mx-auto max-w-[1040px] space-y-5">
          <header>
            <h1 className="text-[22px] font-bold tracking-tight text-foreground">
              Place Order
            </h1>
            <p className="mt-[3px] text-button text-muted">
              Single-leg F&amp;O order ·{" "}
              <span className="font-mono">{segment === "NFO" ? "NSE" : "BSE"}</span> segment
            </p>
          </header>
          {contractFieldsLocked ? (
            <p className="rounded-md border border-accent/30 bg-accent-tint px-3 py-2 text-xs leading-snug text-accent-strong">
              Contract details are fixed based on the order being cloned. Change quantity, limit price, or tap Buy
              / Sell when ready.
            </p>
          ) : null}          

          <div className="grid grid-cols-1 gap-5 lg:grid-cols-[1.5fr_1fr] lg:items-start">
            <section
              className="relative z-20 rounded-[13px] border border-border bg-panel"
              aria-label="Options order entry"
            >
              <div className="flex flex-col gap-4 p-[18px]">
                <div className="flex items-end gap-2.5">
                  <div className="shrink-0">
                    {/* <span className={sb.fieldLabel}>Exchange</span> */}
                    <ExchangeFlipToggle
                      value={segment}
                      disabled={contractFieldsLocked}
                      onChange={(next) => {
                        setSegment(next);
                        resetOrderForm();
                      }}
                    />
                  </div>
                  <div className="min-w-0 flex-1">
                    <span className={sb.fieldLabel}>Underlying</span>
                    <OptionChainUnderlyingSearch
                      variant="ticker"
                      underlyings={uq.data?.underlyings ?? []}
                      value={stockCode}
                      disabled={uq.isLoading || contractFieldsLocked}
                      spot={spot}
                      loading={chainLoading}
                      quoteMeta={chainQuoteMeta}
                      onChange={(code) => {
                        setStockCode(code);
                        setExpiryDate("");
                        setStrikeSelection(null);
                        setQuantity("");
                        setPrice("");
                        setLockedOrderSide(null);
                        resetContractFieldModes();
                      }}
                    />
                  </div>
                  <div className="shrink-0">
                    {/* <span className={sb.fieldLabel}>Type</span> */}
                    {/* <div className="flex h-11 items-center"> */}
                      <OptionTypeBadge
                        right={right}
                        disabled={contractFieldsLocked}
                        onToggle={setRight}
                        className="inline-flex h-10 items-center justify-center rounded-[9px] border-[1.5px] px-3.5 font-mono text-table font-bold uppercase tracking-[.04em] transition hover:brightness-110 focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 disabled:pointer-events-none disabled:opacity-50"
                      />
                    {/* </div> */}
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <span className={sb.fieldLabel}>Expiry</span>
                    <div className="flex items-stretch gap-1.5">
                      <div className="min-w-0 flex-1">
                        {expiryFieldMode === "dropdown" ? (
                          <ExpirySelectPill
                            layout="default"
                            hideLabel
                            rootClassName={pillStretch}
                            dates={expiryOptions}
                            value={expiryDate}
                            disabled={!stockCode || contractFieldsLocked}
                            onChange={onExpiryChange}
                          />
                        ) : (
                          <input
                            type="text"
                            className={sb.input}
                            value={expiryDate}
                            disabled={!stockCode || contractFieldsLocked}
                            placeholder="21-Mar-2026"
                            aria-label="Expiry date"
                            onChange={(e) => onExpiryChange(e.target.value)}
                          />
                        )}
                      </div>
                      {!contractFieldsLocked ? (
                        expiryFieldMode === "dropdown" ? (
                          <button
                            type="button"
                            className={fieldModeBtnClass}
                            disabled={!stockCode}
                            aria-label="Edit expiry manually"
                            onClick={() => setManualEditPrompt("expiry")}
                          >
                            <EditIcon />
                          </button>
                        ) : (
                          <button
                            type="button"
                            className={fieldModeBtnClass}
                            aria-label="Choose expiry from list"
                            onClick={() => setExpiryFieldMode("dropdown")}
                          >
                            <ListIcon />
                          </button>
                        )
                      ) : null}
                    </div>
                    {expiryInvalid ? (
                      <p className="mt-1 text-xs text-amber-accent">
                        Use DD-Mon-YYYY format (e.g. 21-Mar-2026).
                      </p>
                    ) : null}
                  </div>

                  <div>
                    <span className={sb.fieldLabel}>Strike</span>
                    <div className="flex items-stretch gap-1.5">
                      <div className="min-w-0 flex-1">
                        {strikeFieldMode === "dropdown" ? (
                          <StrikeSelectPill
                            layout="default"
                            hideLabel
                            rootClassName={pillStretch}
                            strikes={strikes}
                            value={effectiveStrike}
                            atmBadge={isAtmStrike}
                            busy={Boolean(
                              stockCode &&
                                expiryDate &&
                                chainQ.isFetching &&
                                !strikes.length,
                            )}
                            disabled={strikeDropdownDisabled}
                            onChange={(k) => {
                              setStrikeSelection(k);
                            }}
                          />
                        ) : (
                          <input
                            type="number"
                            min={0}
                            step={1}
                            className={sb.input}
                            value={manualStrikeValue}
                            disabled={!stockCode || contractFieldsLocked}
                            placeholder="24500"
                            aria-label="Strike price"
                            onChange={(e) => {
                              const raw = e.target.value;
                              if (raw === "") {
                                setStrikeSelection(null);
                              } else {
                                const n = parseNum(raw);
                                setStrikeSelection(Number.isFinite(n) ? n : null);
                              }
                            }}
                          />
                        )}
                      </div>
                      {!contractFieldsLocked ? (
                        strikeFieldMode === "dropdown" ? (
                          <button
                            type="button"
                            className={fieldModeBtnClass}
                            disabled={!stockCode}
                            aria-label="Edit strike manually"
                            onClick={() => setManualEditPrompt("strike")}
                          >
                            <EditIcon />
                          </button>
                        ) : (
                          <button
                            type="button"
                            className={fieldModeBtnClass}
                            aria-label="Choose strike from list"
                            onClick={() => setStrikeFieldMode("dropdown")}
                          >
                            <ListIcon />
                          </button>
                        )
                      ) : null}
                    </div>
                  </div>
                </div>

                <ChainBuildStatus visible={chainLoading} phase={chainBuildPhase} />

                {!chainLoading && stockCode && expiryDate ? (
                  <div className="flex items-center justify-between gap-2 px-0.5">
                    <span className="flex items-center gap-[7px] text-table text-muted">
                      <span
                        className={`size-1.5 shrink-0 rounded-full ${chainSuccess ? "bg-up" : "bg-faint"}`}
                        aria-hidden
                      />
                      Details loaded automatically
                    </span>
                    <button
                      type="button"
                      onClick={() => chainQ.refetch()}
                      disabled={chainQ.isFetching}
                      className="flex shrink-0 items-center gap-[5px] rounded-md px-1.5 py-1 text-table font-semibold text-accent-strong transition hover:bg-accent-tint disabled:pointer-events-none disabled:opacity-50"
                    >
                      <RefreshIcon spinning={chainQ.isFetching} />
                      Refresh
                    </button>
                  </div>
                ) : null}

                <div className="flex flex-col gap-4 pt-4">
                  {/* <h2 className={sb.sectionTitle}>Quantity &amp; price</h2> */}
                  <div className="grid grid-cols-1 gap-4">
                    <label className={sb.fieldLabel}>
                      Quantity (units)
                      {lotSizeForHints != null &&
                      typeof lotSizeForHints === "number" &&
                      Number.isFinite(lotSizeForHints) ? (
                        <span className="font-normal normal-case tracking-normal text-muted">
                          {" "}
                          · lot {lotSizeForHints.toLocaleString("en-IN")}
                        </span>
                      ) : null}
                      <input
                        type="number"
                        min={1}
                        className={`${sb.input} mt-1.5`}
                        value={quantity}
                        onChange={(e) => setQuantity(e.target.value)}
                        onBlur={() => {
                          if (!lotSizeForHints || lotSizeForHints <= 0) return;
                          const n = parseNum(quantity);
                          if (!Number.isFinite(n) || n <= 0) return;
                          setQuantity(String(snapQuantityToLotMultiple(n, lotSizeForHints)));
                        }}
                        placeholder="e.g. 65"
                      />
                    </label>
                    <AggressiveLimitOrderField
                      aggressive={aggressiveLimit}
                      price={price}
                      onAggressiveChange={(checked) => {
                        setAggressiveLimit(checked);
                        if (checked) setPrice("");
                      }}
                      onPriceChange={setPrice}
                    />
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    <button
                      type="button"
                      className="inline-flex w-full items-center justify-center gap-2 rounded-lg bg-up-btn px-4 py-3 text-[14.5px] font-bold tracking-[.02em] text-white transition hover:brightness-[1.06] focus:outline-none focus-visible:ring-2 focus-visible:ring-up/40 disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50"
                      disabled={!previewLeg || lockedOrderSide === "Sell"}
                      onClick={() => openPreview("Buy")}
                    >
                      <BuyArrowIcon />
                      Buy
                    </button>
                    <button
                      type="button"
                      className={`${sb.btnDanger} w-full gap-2 px-4 py-3 text-[14.5px] tracking-[.02em]`}
                      disabled={!previewLeg || lockedOrderSide === "Buy"}
                      onClick={() => openPreview("Sell")}
                    >
                      <SellArrowIcon />
                      Sell
                    </button>
                  </div>
                  {!previewLeg &&
                  stockCode &&
                  expiryDate &&
                  effectiveStrike != null ? (
                    <p className="text-sm text-muted">
                      {aggressiveLimit
                        ? "Enter a positive quantity to enable execution preview."
                        : "Enter a positive quantity and valid price to enable the execution preview."}
                    </p>
                  ) : null}
                </div>

                {chainError ? (
                  <div className="app-alert-error text-xs">{chainError}</div>
                ) : null}
              </div>
            </section>

            <div className="space-y-4 lg:sticky lg:top-[76px]">
              <div
                className="overflow-hidden rounded-[13px] border border-border bg-panel"
                aria-label="Scrip details"
              >
                <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border-soft px-4 py-[13px]">
                  <span className="text-hint font-bold uppercase tracking-[.07em] text-muted">
                    Scrip details
                  </span>
                  {chainQuoteMeta ? (
                    <QuoteSourceBadge meta={chainQuoteMeta} variant="default" showAsOf={false} />
                  ) : null}
                </div>
                <div className="p-4">
                {cellDetails ? (
                  <div className="grid grid-cols-2 gap-x-3 gap-y-[14px]">
                    <ScripStat
                      label="LTP ₹"
                      value={
                        cellDetails.ltp != null
                          ? cellDetails.ltp.toLocaleString("en-IN", {
                              maximumFractionDigits: 2,
                            })
                          : "—"
                      }
                      emphasize
                    />
                    <ScripStat
                      label="B:S ratio"
                      value={cellDetails.buySellRatioLabel}
                      emphasize
                    />
                    <ScripStat
                      label="Buy qty"
                      value={cellDetails.totalBuyQty.toLocaleString("en-IN")}
                      tone="up"
                    />
                    <ScripStat
                      label="Sell qty"
                      value={cellDetails.totalSellQty.toLocaleString("en-IN")}
                      tone="down"
                    />
                    <div className="col-span-2 grid min-w-0 grid-cols-2 gap-3 border-t border-border-soft pt-[13px]">
                      <ScripStat
                        label="Margin / lot · Buy"
                        value={
                          marginQ.isFetching
                            ? "…"
                            : marginQ.data?.marginPerLotBuy != null
                              ? formatIndianMoneyCompact(marginQ.data.marginPerLotBuy)
                              : "—"
                        }
                      />
                      <ScripStat
                        label="Margin / lot · Sell"
                        value={
                          marginQ.isFetching
                            ? "…"
                            : marginQ.data?.marginPerLotSell != null
                              ? formatIndianMoneyCompact(marginQ.data.marginPerLotSell)
                              : "—"
                        }
                      />
                    </div>
                  </div>
                ) : (
                  <p className="text-sm text-muted">
                    {stockCode && expiryDate && effectiveStrike != null
                      ? "Loading from live chain…"
                      : "Select a scrip, expiry, and strike to see live details."}
                  </p>
                )}
                {marginQ.data?.marginError ? (
                  <p className="mt-2 text-heading leading-snug text-amber-accent">
                    {marginQ.data.marginError}
                  </p>
                ) : null}
                </div>
              </div>

              <div
                className="rounded-[13px] border border-border bg-panel2 p-4"
                aria-label="Order summary"
              >
                <p className="mb-3 text-hint font-bold uppercase tracking-[.07em] text-muted">
                  Order summary
                </p>
                {stockCode && effectiveStrike != null ? (
                  <div className="mb-[14px] flex items-center gap-2">
                    <OrderSideBadge side={previewSide} />
                    <span className="font-mono text-sm font-semibold text-foreground">
                      {stockCode} {Math.round(effectiveStrike)} {right === "Call" ? "CE" : "PE"}
                    </span>
                  </div>
                ) : null}
                <div className="flex flex-col gap-[9px] text-button">
                  <SummaryRow label="Expiry" value={expiryDisplay || "—"} />
                  <SummaryRow
                    label="Quantity"
                    value={
                      qtyNum > 0
                        ? `${qtyNum.toLocaleString("en-IN")}${lots != null ? ` (${lots.toLocaleString("en-IN")} lot${lots === 1 ? "" : "s"})` : ""}`
                        : "—"
                    }
                  />
                  <SummaryRow
                    label="Limit price"
                    value={
                      aggressiveLimit
                        ? "Aggressive Limit"
                        : Number.isFinite(priceNum) && priceNum > 0
                          ? `₹${priceNum.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`
                          : "—"
                    }
                  />
                  <SummaryRow
                    label={`Est. margin (${previewSide})`}
                    value={estMargin != null ? formatIndianMoneyCompact(estMargin) : "—"}
                  />
                </div>
                <div className="mt-[3px] flex items-baseline justify-between border-t border-border pt-[11px]">
                  <span className="text-button font-semibold text-foreground">
                    Order value
                  </span>
                  <span className="font-mono text-base font-bold text-foreground">
                    {orderValue != null ? formatIndianMoneyCompact(orderValue) : "—"}
                  </span>
                </div>
              </div>
            </div>
          </div>
        </div>
        <ManualContractFieldWarningDialog
          open={manualEditPrompt != null}
          field={manualEditPrompt ?? "expiry"}
          onCancel={() => setManualEditPrompt(null)}
          onConfirm={confirmManualEdit}
        />
      </RevokedTradingPageGuard>
    </AppShell>
  );
}

function ScripStat({
  label,
  value,
  tone,
  emphasize,
}: {
  label: string;
  value: string;
  tone?: "up" | "down";
  emphasize?: boolean;
}) {
  return (
    <div className="min-w-0">
      <div className="text-micro font-semibold uppercase tracking-[.06em] text-faint">
        {label}
      </div>
      <div
        className={`mt-[3px] font-mono tabular-nums ${emphasize ? "text-base font-semibold" : "text-sm font-medium"} ${
          tone === "up" ? "text-up" : tone === "down" ? "text-down" : "text-foreground"
        }`}
      >
        {value}
      </div>
    </div>
  );
}

function SummaryRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="text-muted">{label}</span>
      <span className="font-mono font-semibold tabular-nums text-foreground">{value}</span>
    </div>
  );
}

export default function PlaceOrderPage() {
  return (
    <Suspense
      fallback={
        <AppShell>
          <div className="mx-auto max-w-[1040px] p-6 text-sm app-text-muted">
            Loading…
          </div>
        </AppShell>
      }
    >
      <PlaceOrderPageInner />
    </Suspense>
  );
}
