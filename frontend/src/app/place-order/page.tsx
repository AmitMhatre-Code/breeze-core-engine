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
import { QuoteSourceBadge } from "@/components/market-data/QuoteSourceBadge";
import { useOrderConfirm } from "@/components/order/OrderConfirmProvider";
import { OptionChainUnderlyingSearch } from "@/components/order/OptionChainUnderlyingSearch";
import { ExpirySelectPill } from "@/components/strategy-builder/ExpirySelectPill";
import { StrikeSelectPill } from "@/components/strategy-builder/StrikeSelectPill";
import { apiClient } from "@/lib/api-client";
import { formatIndianMoneyCompact } from "@/lib/format-money-in";
import { isValidExpiryDisplay, sortExpiryDatesAsc } from "@/lib/strategy-builder/expiry";
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
} from "@/components/market-data/ChainBuildStatus";
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
  // for scrip details below, so no separate manual "fetch" round-trip is needed.
  const chainQ = useQuery({
    ...chainQueryOptions({
      queryKeyPrefix: ["place-order"],
      stock_code: stockCode,
      expiry_date: expiryDate,
      exchange_code: segment,
      subscription_holder: subscriptionHolder,
      enabled: !contractFieldsLocked,
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

  return (
    <AppShell>
      <RevokedTradingPageGuard>
        <div className="mx-auto max-w-[1040px] space-y-5">
          <header>
            <h1 className="text-[22px] font-bold tracking-tight text-foreground">
              Place order
            </h1>
            <p className="mt-1 text-sm leading-relaxed text-muted">
              F&amp;O (NFO / BFO): select a contract — scrip details and margin update
              automatically from the live chain.
            </p>
          </header>

          <div className="grid grid-cols-1 gap-5 lg:grid-cols-[1.5fr_1fr] lg:items-start">
            <section
              className={`${sb.section} relative z-20 space-y-4`}
              aria-label="Options order entry"
            >
              {contractFieldsLocked ? (
                <p className="rounded-md border border-accent/30 bg-accent-tint px-3 py-2 text-xs leading-snug text-accent-strong">
                  Contract details are fixed. Change quantity, limit price, or tap Buy
                  / Sell when ready.
                </p>
              ) : null}
              <div className="space-y-4">
                <div className="flex flex-wrap items-end justify-between gap-3">
                  <div
                    className="flex flex-wrap items-center gap-2"
                    role="group"
                    aria-label="Exchange segment"
                  >
                    <div className={sb.segmentGroup}>
                      <button
                        type="button"
                        disabled={contractFieldsLocked}
                        onClick={() => {
                          setSegment("NFO");
                          resetOrderForm();
                        }}
                        className={[
                          sb.segmentBtn,
                          segment === "NFO" ? sb.segmentBtnActive : sb.segmentBtnInactive,
                          "disabled:pointer-events-none disabled:opacity-50",
                        ].join(" ")}
                        aria-pressed={segment === "NFO"}
                      >
                        NSE
                      </button>
                      <button
                        type="button"
                        disabled={contractFieldsLocked}
                        onClick={() => {
                          setSegment("BFO");
                          resetOrderForm();
                        }}
                        className={[
                          sb.segmentBtn,
                          segment === "BFO" ? sb.segmentBtnActive : sb.segmentBtnInactive,
                          "disabled:pointer-events-none disabled:opacity-50",
                        ].join(" ")}
                        aria-pressed={segment === "BFO"}
                      >
                        BSE
                      </button>
                    </div>
                  </div>
                  <div
                    className="flex flex-wrap items-center gap-2"
                    role="group"
                    aria-label="Call or Put"
                  >
                    <div className={sb.segmentGroup}>
                      <button
                        type="button"
                        disabled={contractFieldsLocked}
                        className={[
                          sb.segmentBtn,
                          right === "Call" ? sb.segmentBtnActive : sb.segmentBtnInactive,
                          "disabled:pointer-events-none disabled:opacity-50",
                        ].join(" ")}
                        aria-pressed={right === "Call"}
                        onClick={() => setRight("Call")}
                      >
                        Call
                      </button>
                      <button
                        type="button"
                        disabled={contractFieldsLocked}
                        className={[
                          sb.segmentBtn,
                          right === "Put" ? sb.segmentBtnActive : sb.segmentBtnInactive,
                          "disabled:pointer-events-none disabled:opacity-50",
                        ].join(" ")}
                        aria-pressed={right === "Put"}
                        onClick={() => setRight("Put")}
                      >
                        Put
                      </button>
                    </div>
                  </div>
                </div>

                <div className="w-full min-w-0">
                  <span className={sb.fieldLabel}>Scrip</span>
                  <OptionChainUnderlyingSearch
                    variant="ticker"
                    chainBar
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

                <div className="w-full min-w-0">
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

                <ChainBuildStatus visible={chainLoading} phase={chainBuildPhase} />

                <div className="w-full min-w-0">
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

              <div className="space-y-4 border-t border-border-soft pt-4">
                <h2 className={sb.sectionTitle}>Quantity &amp; price</h2>
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
                    className="inline-flex w-full items-center justify-center rounded-lg bg-up-btn px-4 py-2.5 text-sm font-bold text-white transition hover:brightness-[1.06] focus:outline-none focus-visible:ring-2 focus-visible:ring-up/40 disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50"
                    disabled={!previewLeg || lockedOrderSide === "Sell"}
                    onClick={() => openPreview("Buy")}
                  >
                    Buy
                  </button>
                  <button
                    type="button"
                    className={`${sb.btnDanger} w-full px-4 py-2.5 text-sm`}
                    disabled={!previewLeg || lockedOrderSide === "Buy"}
                    onClick={() => openPreview("Sell")}
                  >
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
            </section>

            <div className="space-y-4 lg:sticky lg:top-[76px]">
              <div className={sb.section} aria-label="Scrip details">
                <p className="mb-3 flex flex-wrap items-center justify-between gap-2 text-[13px] font-bold uppercase tracking-[.07em] text-faint">
                  <span>Scrip details</span>
                  {chainQuoteMeta ? (
                    <QuoteSourceBadge meta={chainQuoteMeta} variant="compact" showAsOf />
                  ) : null}
                </p>
                {cellDetails ? (
                  <div className="grid grid-cols-2 gap-3 gap-y-3 sm:grid-cols-3">
                    <ScripStat
                      label="LTP (₹)"
                      value={
                        cellDetails.ltp != null
                          ? cellDetails.ltp.toLocaleString("en-IN", {
                              maximumFractionDigits: 2,
                            })
                          : "—"
                      }
                    />
                    <ScripStat
                      label="Buy qty"
                      value={cellDetails.totalBuyQty.toLocaleString("en-IN")}
                    />
                    <ScripStat
                      label="Sell qty"
                      value={cellDetails.totalSellQty.toLocaleString("en-IN")}
                    />
                    <ScripStat label="B:S ratio" value={cellDetails.buySellRatioLabel} />
                    <ScripStat
                      label="Lot"
                      value={
                        cellDetails.lotSize != null
                          ? cellDetails.lotSize.toLocaleString("en-IN")
                          : "—"
                      }
                    />
                    <div />
                    <div className="col-span-3 grid min-w-0 grid-cols-2 gap-3 border-t border-border-soft pt-3">
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
                  <p className="mt-2 text-[13px] leading-snug text-amber-accent">
                    {marginQ.data.marginError}
                  </p>
                ) : null}
              </div>

              <div className={sb.section} aria-label="Order summary">
                <p className="mb-3 text-[13px] font-bold uppercase tracking-[.07em] text-faint">
                  Order summary
                </p>
                <div className="divide-y divide-border-soft text-sm">
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
                        ? "Market"
                        : Number.isFinite(priceNum) && priceNum > 0
                          ? `₹${priceNum.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`
                          : "—"
                    }
                  />
                  <SummaryRow
                    label={`Est. margin (${previewSide})`}
                    value={estMargin != null ? formatIndianMoneyCompact(estMargin) : "—"}
                  />
                  <SummaryRow
                    label="Order value"
                    value={orderValue != null ? formatIndianMoneyCompact(orderValue) : "—"}
                  />
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

function ScripStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <div className="text-[12px] font-semibold uppercase tracking-wide text-faint">
        {label}
      </div>
      <div className="mt-0.5 font-mono text-xs font-medium tabular-nums text-foreground">
        {value}
      </div>
    </div>
  );
}

function SummaryRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3 py-2 first:pt-0 last:pb-0">
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
