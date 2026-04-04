"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AppShell } from "@/components/layout/AppShell";
import { ExecutionPreviewModal } from "@/components/order/ExecutionPreviewModal";
import { apiClient } from "@/lib/api-client";
import { formatIndianMoneyCompact } from "@/lib/format-money-in";
import { sortExpiryDatesAsc } from "@/lib/strategy-builder/expiry";
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

export default function PlaceOrderPage() {
  const queryClient = useQueryClient();
  const [segment, setSegment] = useState<Segment>("NFO");
  const [stockCode, setStockCode] = useState("");
  const [expiryDate, setExpiryDate] = useState("");
  const [right, setRight] = useState<OptionRight>("Call");
  /** `null` = follow ATM / first strike from chain until user picks explicitly. */
  const [strikeSelection, setStrikeSelection] = useState<number | null>(null);
  const [scripDetails, setScripDetails] = useState<ScripDetailsState | null>(null);
  const [quantity, setQuantity] = useState("");
  const [price, setPrice] = useState("");
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewSide, setPreviewSide] = useState<OrderSide>("Buy");

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
    enabled: Boolean(stockCode.trim() && expiryDate.trim()),
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
    setPreviewSide(side);
    setPreviewOpen(true);
  }

  const chainError =
    chainQ.data && chainQ.data.Status !== 200
      ? chainQ.data.Error ?? "Chain request failed"
      : chainQ.isError
        ? "Could not load chain"
        : null;

  return (
    <AppShell>
      <div className="space-y-6">
        <header>
          <h1 className="text-xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
            Place order
          </h1>
          <p className="mt-1 text-sm text-zinc-600 dark:text-zinc-400">
            NSE and BSE labels refer to the F&amp;O segment (NFO and BFO). Pick an
            underlying, expiry, and strike, then fetch scrip details before
            trading.
          </p>
        </header>

        <section
          className={`${sb.section} space-y-4`}
          aria-label="Contract selection"
        >
          <h2 className={sb.sectionTitle}>1. Exchange &amp; contract</h2>
          <div
            className={`${sb.segmentGroup} flex flex-wrap`}
            role="group"
            aria-label="Exchange"
          >
            <button
              type="button"
              className={`${sb.segmentBtn} px-3 py-1.5 text-xs ${
                segment === "NFO" ? sb.segmentBtnActive : sb.segmentBtnInactive
              }`}
              aria-pressed={segment === "NFO"}
              onClick={() => {
                setSegment("NFO");
                resetOrderForm();
              }}
            >
              NSE (F&amp;O)
            </button>
            <button
              type="button"
              className={`${sb.segmentBtn} px-3 py-1.5 text-xs ${
                segment === "BFO" ? sb.segmentBtnActive : sb.segmentBtnInactive
              }`}
              aria-pressed={segment === "BFO"}
              onClick={() => {
                setSegment("BFO");
                resetOrderForm();
              }}
            >
              BSE (F&amp;O)
            </button>
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <label className="block text-xs font-medium text-zinc-600 dark:text-zinc-400">
              Stock code
              <select
                className="mt-1 w-full rounded-lg border border-zinc-200 bg-white px-2 py-2 text-sm text-zinc-900 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
                value={stockCode}
                onChange={(e) => {
                  setStockCode(e.target.value);
                  setExpiryDate("");
                  setStrikeSelection(null);
                  setScripDetails(null);
                  setQuantity("");
                  setPrice("");
                }}
                disabled={uq.isLoading}
              >
                <option value="">Select underlying…</option>
                {(uq.data?.underlyings ?? []).map((u) => (
                  <option key={u.stock_code} value={u.stock_code}>
                    {u.stock_code}
                    {u.long_name ? ` — ${u.long_name}` : ""}
                  </option>
                ))}
              </select>
            </label>
            <label className="block text-xs font-medium text-zinc-600 dark:text-zinc-400">
              Expiry
              <select
                className="mt-1 w-full rounded-lg border border-zinc-200 bg-white px-2 py-2 text-sm text-zinc-900 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
                value={expiryDate}
                onChange={(e) => {
                  setExpiryDate(e.target.value);
                  setStrikeSelection(null);
                  setScripDetails(null);
                  setQuantity("");
                  setPrice("");
                }}
                disabled={!stockCode}
              >
                <option value="">Select expiry…</option>
                {expiryOptions.map((ex) => (
                  <option key={ex} value={ex}>
                    {ex}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <span className="block text-xs font-medium text-zinc-600 dark:text-zinc-400">
                Option type
              </span>
              <div
                className={`${sb.segmentGroup} mt-1 inline-flex`}
                role="group"
                aria-label="Call or Put"
              >
                <button
                  type="button"
                  className={`${sb.segmentBtn} px-3 py-1.5 text-xs ${
                    right === "Call"
                      ? sb.segmentBtnActive
                      : sb.segmentBtnInactive
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
                  className={`${sb.segmentBtn} px-3 py-1.5 text-xs ${
                    right === "Put"
                      ? sb.segmentBtnActive
                      : sb.segmentBtnInactive
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
            <label className="block text-xs font-medium text-zinc-600 dark:text-zinc-400">
              Strike
              <select
                className="mt-1 w-full rounded-lg border border-zinc-200 bg-white px-2 py-2 text-sm text-zinc-900 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
                value={
                  effectiveStrike != null ? String(effectiveStrike) : ""
                }
                onChange={(e) => {
                  const v = e.target.value;
                  setStrikeSelection(v === "" ? null : Number(v));
                  setScripDetails(null);
                }}
                disabled={
                  !stockCode || !expiryDate || chainQ.isLoading || !strikes.length
                }
              >
                {!strikes.length ? (
                  <option value="">
                    {chainQ.isFetching ? "Loading strikes…" : "—"}
                  </option>
                ) : null}
                {strikes.map((k) => (
                  <option key={k} value={k}>
                    {k.toLocaleString("en-IN")}
                  </option>
                ))}
              </select>
            </label>
          </div>

          {chainError ? (
            <p className="text-xs text-amber-700 dark:text-amber-200">
              {chainError}
            </p>
          ) : null}

          <div>
            <button
              type="button"
              className={sb.btnPrimary}
              disabled={!canFetchDetails}
              onClick={() => void fetchDetailsMut.mutate()}
            >
              {fetchDetailsMut.isPending ? "Fetching…" : "Fetch scrip details"}
            </button>
            {fetchDetailsMut.isError ? (
              <p className="mt-2 text-xs text-red-600 dark:text-red-400">
                {fetchDetailsMut.error instanceof Error
                  ? fetchDetailsMut.error.message
                  : "Request failed"}
              </p>
            ) : null}
          </div>
        </section>

        {scripDetails ? (
          <section
            className={`${sb.section} space-y-3`}
            aria-label="Scrip details"
          >
            <h2 className={sb.sectionTitle}>2. Scrip details</h2>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              <div className="rounded-lg border border-zinc-200/80 bg-zinc-50/80 px-3 py-2 dark:border-zinc-700/80 dark:bg-zinc-900/50">
                <div className="text-[10px] font-semibold uppercase tracking-wide text-zinc-500">
                  LTP (₹)
                </div>
                <div className="mt-0.5 font-mono text-sm tabular-nums text-zinc-900 dark:text-zinc-50">
                  {scripDetails.ltp != null && Number.isFinite(scripDetails.ltp)
                    ? scripDetails.ltp.toLocaleString("en-IN", {
                        maximumFractionDigits: 2,
                      })
                    : "—"}
                </div>
              </div>
              <div className="rounded-lg border border-zinc-200/80 bg-zinc-50/80 px-3 py-2 dark:border-zinc-700/80 dark:bg-zinc-900/50">
                <div className="text-[10px] font-semibold uppercase tracking-wide text-zinc-500">
                  Total buy qty
                </div>
                <div className="mt-0.5 font-mono text-sm tabular-nums text-zinc-900 dark:text-zinc-50">
                  {scripDetails.totalBuyQty.toLocaleString("en-IN")}
                </div>
              </div>
              <div className="rounded-lg border border-zinc-200/80 bg-zinc-50/80 px-3 py-2 dark:border-zinc-700/80 dark:bg-zinc-900/50">
                <div className="text-[10px] font-semibold uppercase tracking-wide text-zinc-500">
                  Total sell qty
                </div>
                <div className="mt-0.5 font-mono text-sm tabular-nums text-zinc-900 dark:text-zinc-50">
                  {scripDetails.totalSellQty.toLocaleString("en-IN")}
                </div>
              </div>
              <div className="rounded-lg border border-zinc-200/80 bg-zinc-50/80 px-3 py-2 dark:border-zinc-700/80 dark:bg-zinc-900/50">
                <div className="text-[10px] font-semibold uppercase tracking-wide text-zinc-500">
                  Buy : sell ratio
                </div>
                <div className="mt-0.5 font-mono text-sm tabular-nums text-zinc-900 dark:text-zinc-50">
                  {scripDetails.buySellRatioLabel}
                </div>
              </div>
              <div className="rounded-lg border border-zinc-200/80 bg-zinc-50/80 px-3 py-2 dark:border-zinc-700/80 dark:bg-zinc-900/50">
                <div className="text-[10px] font-semibold uppercase tracking-wide text-zinc-500">
                  Lot size
                </div>
                <div className="mt-0.5 font-mono text-sm tabular-nums text-zinc-900 dark:text-zinc-50">
                  {scripDetails.lotSize != null
                    ? scripDetails.lotSize.toLocaleString("en-IN")
                    : "—"}
                </div>
              </div>
              <div className="rounded-lg border border-zinc-200/80 bg-zinc-50/80 px-3 py-2 dark:border-zinc-700/80 dark:bg-zinc-900/50 sm:col-span-2 lg:col-span-1">
                <div className="text-[10px] font-semibold uppercase tracking-wide text-zinc-500">
                  Margin / lot (SPAN)
                </div>
                <div className="mt-0.5 text-sm tabular-nums text-zinc-900 dark:text-zinc-50">
                  <span className="mr-3 inline-block">
                    <span className="text-zinc-500">Buy: </span>
                    {scripDetails.marginPerLotBuy != null
                      ? formatIndianMoneyCompact(scripDetails.marginPerLotBuy)
                      : "—"}
                  </span>
                  <span className="inline-block">
                    <span className="text-zinc-500">Sell: </span>
                    {scripDetails.marginPerLotSell != null
                      ? formatIndianMoneyCompact(scripDetails.marginPerLotSell)
                      : "—"}
                  </span>
                </div>
                {scripDetails.marginError ? (
                  <p className="mt-1 text-[11px] text-amber-700 dark:text-amber-200">
                    {scripDetails.marginError}
                  </p>
                ) : null}
              </div>
            </div>
          </section>
        ) : null}

        <section className={`${sb.section} space-y-4`} aria-label="Order entry">
          <h2 className={sb.sectionTitle}>
            {scripDetails ? "3. " : "2. "}Quantity &amp; price
          </h2>
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="block text-xs font-medium text-zinc-600 dark:text-zinc-400">
              Quantity (units)
              {lotSizeForHints != null &&
              typeof lotSizeForHints === "number" &&
              Number.isFinite(lotSizeForHints) ? (
                <span className="font-normal text-zinc-500">
                  {" "}
                  · lot {lotSizeForHints.toLocaleString("en-IN")}
                </span>
              ) : null}
              <input
                type="number"
                min={1}
                className="mt-1 w-full rounded-lg border border-zinc-200 bg-white px-2 py-2 text-sm text-zinc-900 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
                value={quantity}
                onChange={(e) => setQuantity(e.target.value)}
                placeholder="e.g. 65"
              />
            </label>
            <label className="block text-xs font-medium text-zinc-600 dark:text-zinc-400">
              Limit price (₹)
              <input
                type="number"
                min={0}
                step={0.05}
                className="mt-1 w-full rounded-lg border border-zinc-200 bg-white px-2 py-2 text-sm text-zinc-900 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
                value={price}
                onChange={(e) => setPrice(e.target.value)}
                placeholder="0"
              />
            </label>
          </div>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              className="rounded-lg border border-emerald-600/80 bg-emerald-600 px-4 py-2 text-sm font-semibold text-white shadow-sm transition hover:bg-emerald-700 disabled:cursor-not-allowed disabled:border-emerald-900 disabled:bg-emerald-900 dark:border-emerald-500/80 dark:bg-emerald-600 dark:hover:bg-emerald-500 dark:disabled:border-emerald-900 dark:disabled:bg-emerald-950"
              disabled={!previewLeg}
              onClick={() => openPreview("Buy")}
            >
              Buy
            </button>
            <button
              type="button"
              className="rounded-lg border border-red-600/80 bg-red-600 px-4 py-2 text-sm font-semibold text-white shadow-sm transition hover:bg-red-700 disabled:cursor-not-allowed disabled:border-red-900 disabled:bg-red-900 dark:border-red-500/80 dark:bg-red-600 dark:hover:bg-red-500 dark:disabled:border-red-900 dark:disabled:bg-red-950"
              disabled={!previewLeg}
              onClick={() => openPreview("Sell")}
            >
              Sell
            </button>
          </div>
          {!previewLeg &&
          stockCode &&
          expiryDate &&
          effectiveStrike != null ? (
            <p className="text-xs text-zinc-500">
              Enter a positive quantity and valid price to enable review.
            </p>
          ) : null}
        </section>
      </div>

      {previewLeg ? (
        <ExecutionPreviewModal
          open={previewOpen}
          onClose={() => setPreviewOpen(false)}
          stockCode={previewLeg.stockCode}
          exchangeCode={previewLeg.exchangeCode}
          expiryDisplay={previewLeg.expiryDisplay}
          legs={[
            {
              strike: previewLeg.strike,
              right: previewLeg.right,
              side: previewLeg.side,
              quantity: previewLeg.quantity,
              premiumPerUnit: previewLeg.premiumPerUnit,
            },
          ]}
        />
      ) : null}
    </AppShell>
  );
}
