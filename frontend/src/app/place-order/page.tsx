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
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AppShell } from "@/components/layout/AppShell";
import { RevokedTradingPageGuard } from "@/components/license/RevokedTradingPageGuard";
import { useOrderConfirm } from "@/components/order/OrderConfirmProvider";
import { OptionChainUnderlyingSearch } from "@/components/order/OptionChainUnderlyingSearch";
import { ExpirySelectPill } from "@/components/strategy-builder/ExpirySelectPill";
import { StrikeSelectPill } from "@/components/strategy-builder/StrikeSelectPill";
import { apiClient } from "@/lib/api-client";
import { formatIndianMoneyCompact } from "@/lib/format-money-in";
import { sortExpiryDatesAsc } from "@/lib/strategy-builder/expiry";
import {
  consumePlaceOrderClonePayload,
  placeOrderPrefillFromSearchParams,
} from "@/lib/place-order-clone";
import { sb } from "@/lib/strategy-builder/ui";
import type {
  ChainApiResponse,
  ChainRow,
  ChainSuccess,
  MarginApiResponse,
  OptionRight,
  OrderSide,
  UnderlyingsApiResponse,
} from "@/lib/strategy-builder/types";

type Segment = "NFO" | "BFO";

type ScripDetailsState = {
  ltp: number | null;
  totalBuyQty: number;
  totalSellQty: number;
  buySellRatioLabel: string;
  lotSize: number | null;
  marginPerLotBuy: number | null;
  marginPerLotSell: number | null;
  marginError?: string;
};

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
  const searchParams = useSearchParams();
  const { openExecutionConfirm } = useOrderConfirm();
  const queryClient = useQueryClient();
  const prefillConsumedRef = useRef(false);
  const [segment, setSegment] = useState<Segment>("NFO");
  const [stockCode, setStockCode] = useState("");
  const [expiryDate, setExpiryDate] = useState("");
  const [right, setRight] = useState<OptionRight>("Call");
  /** `null` = follow ATM / first strike from chain until user picks explicitly. */
  const [strikeSelection, setStrikeSelection] = useState<number | null>(null);
  const [scripDetails, setScripDetails] = useState<ScripDetailsState | null>(null);
  const [quantity, setQuantity] = useState("");
  const [price, setPrice] = useState("");
  const [previewSide, setPreviewSide] = useState<OrderSide>("Buy");
  /** When set, the opposite side button is disabled (clone / square-off / URL prefill). */
  const [lockedOrderSide, setLockedOrderSide] = useState<OrderSide | null>(null);
  /** Exchange, scrip, expiry, strike, option fixed — only qty, price, and locked side editable. */
  const [contractFieldsLocked, setContractFieldsLocked] = useState(false);

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
      setScripDetails(null);
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

  const chainQ = useQuery({
    queryKey: ["place-order", "chain", segment, stockCode, expiryDate],
    queryFn: () =>
      apiClient.get<ChainApiResponse>(
        `/strategy-builder/chain?${new URLSearchParams({
          stock_code: stockCode,
          expiry_date: expiryDate,
          exchange_code: segment,
        }).toString()}`,
      ),
    enabled: Boolean(
      !contractFieldsLocked && stockCode.trim() && expiryDate.trim(),
    ),
    staleTime: 5_000,
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

  const effectiveStrike = strikeSelection ?? defaultStrike;

  function resetOrderForm() {
    setStockCode("");
    setExpiryDate("");
    setStrikeSelection(null);
    setScripDetails(null);
    setQuantity("");
    setPrice("");
    setLockedOrderSide(null);
    setContractFieldsLocked(false);
  }

  const fetchDetailsMut = useMutation({
    mutationFn: async (): Promise<ScripDetailsState> => {
      const data = await apiClient.get<ChainApiResponse>(
        `/strategy-builder/chain?${new URLSearchParams({
          stock_code: stockCode,
          expiry_date: expiryDate,
          exchange_code: segment,
        }).toString()}`,
      );
      if (data.Status !== 200 || !data.Success) {
        throw new Error(data.Error ?? "Could not load option chain");
      }
      const success = data.Success;
      const row = success.chain_rows.find(
        (r) =>
          Math.round(r.strike_price) === Math.round(effectiveStrike ?? NaN),
      );
      const cell = pickOptionCell(row, right);
      if (!cell) {
        throw new Error("No chain data for this strike and option type");
      }
      const ltp = parseNum(cell.ltp);
      const totalBuyQty = Math.round(parseNum(cell.total_buy_qty) || 0);
      const totalSellQty = Math.round(parseNum(cell.total_sell_qty) || 0);
      const ratioLabel = formatRatio(cell.buy_sell_ratio);
      const lotFromCell = parseNum(cell.lot_size);
      const lotFromSeries = success.lot_size != null ? Number(success.lot_size) : NaN;
      const lotSize =
        Number.isFinite(lotFromCell) && lotFromCell > 0
          ? Math.round(lotFromCell)
          : Number.isFinite(lotFromSeries) && lotFromSeries > 0
            ? Math.round(lotFromSeries)
            : null;

      const expiryForMargin = (success.expiry_display || expiryDate).trim();
      const strikeStr = String(effectiveStrike ?? "").trim();
      if (!lotSize || lotSize <= 0) {
        return {
          ltp: Number.isFinite(ltp) ? ltp : null,
          totalBuyQty,
          totalSellQty,
          buySellRatioLabel: ratioLabel,
          lotSize: null,
          marginPerLotBuy: null,
          marginPerLotSell: null,
          marginError: "Lot size unavailable",
        };
      }

      const prem = Number.isFinite(ltp) && ltp > 0 ? ltp : 0;
      const legBase = {
        stock_code: stockCode,
        exchange_code: segment,
        expiry_date: expiryForMargin,
        product_type: "Options",
        right,
        strike_price: strikeStr,
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

      return {
        ltp: Number.isFinite(ltp) ? ltp : null,
        totalBuyQty,
        totalSellQty,
        buySellRatioLabel: ratioLabel,
        lotSize,
        marginPerLotBuy,
        marginPerLotSell,
        marginError,
      };
    },
    onSuccess: (d) => {
      setScripDetails(d);
      if (d.ltp != null && d.ltp > 0) {
        setPrice(String(Number(d.ltp.toFixed(2))));
      }
      void queryClient.invalidateQueries({
        queryKey: ["place-order", "chain", segment, stockCode, expiryDate],
      });
    },
  });

  const canFetchDetails =
    Boolean(stockCode.trim() && expiryDate.trim() && effectiveStrike != null) &&
    !fetchDetailsMut.isPending;

  const fetchDetailsDisabled =
    fetchDetailsMut.isPending ||
    (contractFieldsLocked &&
      scripDetails != null &&
      !fetchDetailsMut.isError) ||
    (!contractFieldsLocked && !canFetchDetails);

  const pillStretch = "!max-w-none w-full min-w-0";

  const qtyNum = Math.round(parseNum(quantity));
  const priceNum = parseNum(price);
  const lotSizeForHints = scripDetails?.lotSize ?? chainSuccess?.lot_size ?? null;

  const previewLeg = useMemo(() => {
    if (
      effectiveStrike == null ||
      !stockCode.trim() ||
      !expiryDate.trim() ||
      qtyNum <= 0 ||
      !Number.isFinite(priceNum) ||
      priceNum < 0
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
      premiumPerUnit: priceNum,
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
      qtyNum <= 0 ||
      !Number.isFinite(priceNum) ||
      priceNum < 0
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
      legs: [
        {
          strike: effectiveStrike,
          right,
          side,
          quantity: qtyNum,
          premiumPerUnit: priceNum,
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

  return (
    <AppShell contentWidth="default">
      <RevokedTradingPageGuard>
      <div className="mx-auto max-w-md space-y-5">
        <header className="flex flex-col gap-3">
          <div>
            <h1 className="text-xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
              Place order
            </h1>
            <p className="mt-2 text-sm leading-relaxed text-zinc-600 dark:text-zinc-400">
              F&amp;O (NFO / BFO): select contract and fetch details before placing order.
            </p>
          </div>
        </header>

        <section
          className={`${sb.section} relative z-20 space-y-4`}
          aria-label="Options order entry"
        >
          {contractFieldsLocked ? (
            <p className="rounded-md border border-sky-500/25 bg-sky-500/10 px-3 py-2 text-xs leading-snug text-sky-950 dark:border-sky-400/20 dark:bg-sky-950/40 dark:text-sky-100">
              Contract details are fixed. Change quantity, limit price, or tap Buy
              / Sell when ready.
            </p>
          ) : null}
          <div className="space-y-4">
            <div
              className="flex flex-wrap items-center gap-2"
              role="group"
              aria-label="Exchange segment"
            >
              {/* <span className={`${sb.fieldLabel} mb-0`}>Exchange</span> */}
              <div className="inline-flex rounded-lg bg-zinc-200/70 p-0.5 ring-1 ring-zinc-300/70 dark:bg-black/30 dark:ring-zinc-700/70">
                <button
                  type="button"
                  disabled={contractFieldsLocked}
                  onClick={() => {
                    setSegment("NFO");
                    resetOrderForm();
                  }}
                  className={`rounded-md px-3 py-1.5 text-xs font-semibold transition focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-500/45 sm:text-sm disabled:pointer-events-none disabled:opacity-50 ${
                    segment === "NFO"
                      ? "bg-white text-zinc-900 shadow-sm dark:bg-zinc-700 dark:text-white"
                      : "text-zinc-600 hover:bg-zinc-300/50 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-800/80 dark:hover:text-zinc-200"
                  }`}
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
                  className={`rounded-md px-3 py-1.5 text-xs font-semibold transition focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-500/45 sm:text-sm disabled:pointer-events-none disabled:opacity-50 ${
                    segment === "BFO"
                      ? "bg-white text-zinc-900 shadow-sm dark:bg-zinc-700 dark:text-white"
                      : "text-zinc-600 hover:bg-zinc-300/50 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-800/80 dark:hover:text-zinc-200"
                  }`}
                  aria-pressed={segment === "BFO"}
                >
                  BSE
                </button>
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
                onChange={(code) => {
                  setStockCode(code);
                  setExpiryDate("");
                  setStrikeSelection(null);
                  setScripDetails(null);
                  setQuantity("");
                  setPrice("");
                  setLockedOrderSide(null);
                }}
              />
            </div>

            <div className="w-full min-w-0">
              <span className={sb.fieldLabel}>Expiry</span>
              <ExpirySelectPill
                layout="default"
                tone="darkToolbar"
                hideLabel
                rootClassName={pillStretch}
                dates={expiryOptions}
                value={expiryDate}
                disabled={!stockCode || contractFieldsLocked}
                onChange={(d) => {
                  setExpiryDate(d);
                  setStrikeSelection(null);
                  setScripDetails(null);
                  setQuantity("");
                  setPrice("");
                  setLockedOrderSide(null);
                }}
              />
            </div>

            <div className="w-full min-w-0">
              <span className={sb.fieldLabel}>Strike</span>
              <StrikeSelectPill
                layout="default"
                tone="darkToolbar"
                hideLabel
                rootClassName={pillStretch}
                strikes={strikes}
                value={effectiveStrike}
                busy={Boolean(
                  stockCode && expiryDate && chainQ.isFetching && !strikes.length,
                )}
                disabled={
                  !stockCode ||
                  !expiryDate ||
                  (chainQ.isFetching && !strikes.length) ||
                  !strikes.length ||
                  contractFieldsLocked
                }
                onChange={(k) => {
                  setStrikeSelection(k);
                  setScripDetails(null);
                }}
              />
            </div>

            <div
              className="flex flex-wrap items-center gap-2"
              role="group"
              aria-label="Call or Put"
            >
              {/* <span className={`${sb.fieldLabel} mb-0`}>Option</span> */}
              <div className="inline-flex rounded-lg bg-zinc-200/70 p-0.5 ring-1 ring-zinc-300/70 dark:bg-black/30 dark:ring-zinc-700/70">
                <button
                  type="button"
                  disabled={contractFieldsLocked}
                  className={`rounded-md px-3 py-1.5 text-xs font-semibold transition focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-500/45 sm:text-sm disabled:pointer-events-none disabled:opacity-50 ${
                    right === "Call"
                      ? "bg-white text-zinc-900 shadow-sm dark:bg-zinc-700 dark:text-white"
                      : "text-zinc-600 hover:bg-zinc-300/50 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-800/80 dark:hover:text-zinc-200"
                  }`}
                  aria-pressed={right === "Call"}
                  onClick={() => {
                    setRight("Call");
                    setScripDetails(null);
                  }}
                >
                  Call
                </button>
                <button
                  type="button"
                  disabled={contractFieldsLocked}
                  className={`rounded-md px-3 py-1.5 text-xs font-semibold transition focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-500/45 sm:text-sm disabled:pointer-events-none disabled:opacity-50 ${
                    right === "Put"
                      ? "bg-white text-zinc-900 shadow-sm dark:bg-zinc-700 dark:text-white"
                      : "text-zinc-600 hover:bg-zinc-300/50 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-800/80 dark:hover:text-zinc-200"
                  }`}
                  aria-pressed={right === "Put"}
                  onClick={() => {
                    setRight("Put");
                    setScripDetails(null);
                  }}
                >
                  Put
                </button>
              </div>
            </div>

            <button
              type="button"
              className={`${sb.btnPrimary} relative w-full min-h-[2.75rem] px-4 py-2.5 text-sm`}
              disabled={fetchDetailsDisabled}
              aria-busy={fetchDetailsMut.isPending}
              onClick={() => void fetchDetailsMut.mutate()}
            >
              <span className="grid place-items-center">
                <span
                  className="col-start-1 row-start-1 invisible select-none"
                  aria-hidden
                >
                  Fetch scrip details
                </span>
                <span className="col-start-1 row-start-1">
                  {fetchDetailsMut.isPending
                    ? "Fetching…"
                    : "Fetch scrip details"}
                </span>
              </span>
            </button>
          </div>

          {scripDetails ? (
            <div
              className="rounded-lg border border-zinc-200/85 bg-zinc-50/90 p-3 shadow-sm dark:border-zinc-700/80 dark:bg-zinc-950/55"
              aria-label="Scrip details from fetch"
            >
              <p className="mb-2 text-[11px] font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                Scrip details
              </p>
              <div className="grid grid-cols-2 gap-2 gap-y-2.5 sm:grid-cols-3">
                <div className="min-w-0">
                  <div className="text-[10px] font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                    LTP (₹)
                  </div>
                  <div className="mt-0.5 font-mono text-xs font-medium tabular-nums text-zinc-900 dark:text-zinc-50">
                    {scripDetails.ltp != null &&
                    Number.isFinite(scripDetails.ltp)
                      ? scripDetails.ltp.toLocaleString("en-IN", {
                          maximumFractionDigits: 2,
                        })
                      : "—"}
                  </div>
                </div>
                <div className="min-w-0">
                  <div className="text-[10px] font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                    Buy qty
                  </div>
                  <div className="mt-0.5 font-mono text-xs font-medium tabular-nums text-zinc-900 dark:text-zinc-50">
                    {scripDetails.totalBuyQty.toLocaleString("en-IN")}
                  </div>
                </div>
                <div className="min-w-0">
                  <div className="text-[10px] font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                    Sell qty
                  </div>
                  <div className="mt-0.5 font-mono text-xs font-medium tabular-nums text-zinc-900 dark:text-zinc-50">
                    {scripDetails.totalSellQty.toLocaleString("en-IN")}
                  </div>
                </div>
                <div className="min-w-0">
                  <div className="text-[10px] font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                    B:S ratio
                  </div>
                  <div className="mt-0.5 font-mono text-xs font-medium tabular-nums text-zinc-900 dark:text-zinc-50">
                    {scripDetails.buySellRatioLabel}
                  </div>
                </div>
                <div className="min-w-0">
                  <div className="text-[10px] font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                    Lot
                  </div>
                  <div className="mt-0.5 font-mono text-xs font-medium tabular-nums text-zinc-900 dark:text-zinc-50">
                    {scripDetails.lotSize != null
                      ? scripDetails.lotSize.toLocaleString("en-IN")
                      : "—"}
                  </div>
                </div>
                <div className="min-w-0" />
                <div className="col-span-3 grid min-w-0 grid-cols-2 gap-2 border-t border-zinc-200/80 pt-2.5 dark:border-zinc-700/60">
                  <div className="min-w-0">
                    <div className="text-[10px] font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                      Margin / lot · Buy
                    </div>
                    <div className="mt-0.5 font-mono text-xs font-medium tabular-nums text-zinc-900 dark:text-zinc-50">
                      {scripDetails.marginPerLotBuy != null
                        ? formatIndianMoneyCompact(scripDetails.marginPerLotBuy)
                        : "—"}
                    </div>
                  </div>
                  <div className="min-w-0">
                    <div className="text-[10px] font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                      Margin / lot · Sell
                    </div>
                    <div className="mt-0.5 font-mono text-xs font-medium tabular-nums text-zinc-900 dark:text-zinc-50">
                      {scripDetails.marginPerLotSell != null
                        ? formatIndianMoneyCompact(
                            scripDetails.marginPerLotSell,
                          )
                        : "—"}
                    </div>
                  </div>
                </div>
              </div>
              {scripDetails.marginError ? (
                <p className="mt-2 text-[11px] leading-snug text-amber-800 dark:text-amber-200/90">
                  {scripDetails.marginError}
                </p>
              ) : null}
            </div>
          ) : null}

          <div className="space-y-4 border-t border-zinc-200/80 pt-4 dark:border-zinc-700/60">
            <h2 className={sb.sectionTitle}>Quantity &amp; price</h2>
            <div className="grid grid-cols-1 gap-4">
              <label className={sb.fieldLabel}>
                Quantity (units)
                {lotSizeForHints != null &&
                typeof lotSizeForHints === "number" &&
                Number.isFinite(lotSizeForHints) ? (
                  <span className="font-normal text-zinc-500 dark:text-zinc-400">
                    {" "}
                    · lot {lotSizeForHints.toLocaleString("en-IN")}
                  </span>
                ) : null}
                <input
                  type="number"
                  min={1}
                  className={sb.input}
                  value={quantity}
                  onChange={(e) => setQuantity(e.target.value)}
                  placeholder="e.g. 65"
                />
              </label>
              <label className={sb.fieldLabel}>
                Limit price (₹)
                <input
                  type="number"
                  min={0}
                  step={0.05}
                  className={sb.input}
                  value={price}
                  onChange={(e) => setPrice(e.target.value)}
                  placeholder="0"
                />
              </label>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <button
                type="button"
                className="inline-flex w-full items-center justify-center rounded-lg border border-emerald-600 bg-emerald-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:border-emerald-500 hover:bg-emerald-500 focus:outline-none focus-visible:ring-4 focus-visible:ring-emerald-500/35 disabled:pointer-events-none disabled:cursor-not-allowed disabled:border-emerald-800 disabled:bg-emerald-800 dark:border-emerald-600 dark:bg-emerald-600 dark:hover:border-emerald-500 dark:hover:bg-emerald-500 dark:disabled:border-emerald-900 dark:disabled:bg-emerald-900"
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
              <p className="text-sm text-zinc-500 dark:text-zinc-400">
                Enter a positive quantity and valid price to enable the
                execution preview.
              </p>
            ) : null}
          </div>

          {chainError ? (
            <div className="app-alert-error text-xs">{chainError}</div>
          ) : null}
          {fetchDetailsMut.isError ? (
            <div className="app-alert-error text-xs">
              {fetchDetailsMut.error instanceof Error
                ? fetchDetailsMut.error.message
                : "Request failed"}
            </div>
          ) : null}
        </section>
      </div>
      </RevokedTradingPageGuard>

    </AppShell>
  );
}

export default function PlaceOrderPage() {
  return (
    <Suspense
      fallback={
        <AppShell contentWidth="default">
          <div className="mx-auto max-w-md p-6 text-sm app-text-muted">
            Loading…
          </div>
        </AppShell>
      }
    >
      <PlaceOrderPageInner />
    </Suspense>
  );
}
