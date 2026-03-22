"use client";

import type { ReactNode } from "react";
import { useCallback, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AppShell } from "@/components/layout/AppShell";
import { useOrderConfirm } from "@/components/order/OrderConfirmProvider";
import { OptionChainUnderlyingSearch } from "@/components/order/OptionChainUnderlyingSearch";
import { ExpirySelectPill } from "@/components/strategy-builder/ExpirySelectPill";
import { PayoffChart } from "@/components/strategy-builder/PayoffChart";
import { apiClient } from "@/lib/api-client";
import { ltpAsOrderPrice } from "@/lib/order-confirm";
import { impliedVolatility } from "@/lib/strategy-builder/blackScholes";
import {
  expiryDisplayToYears,
  sortExpiryDatesAsc,
} from "@/lib/strategy-builder/expiry";
import { sb } from "@/lib/strategy-builder/ui";
import {
  estimateProbabilityOfProfit,
  portfolioGreeks,
  scanMarkToModelCurve,
  scanPayoffCurve,
  summarizePayoffScan,
} from "@/lib/strategy-builder/payoff";
import {
  applyTemplate,
  buildTemplateContext,
  type TemplateId,
  STRATEGY_TEMPLATES,
} from "@/lib/strategy-builder/templates";
import type {
  ChainApiResponse,
  ChainSuccess,
  ExecuteApiResponse,
  MarginApiResponse,
  OptionRight,
  StrategyLeg,
  UnderlyingsApiResponse,
} from "@/lib/strategy-builder/types";
import { formatIndianMoneyCompact } from "@/lib/format-money-in";

/** Readymade card selection (Naked Shorts is separate from `STRATEGY_TEMPLATES`). */
type ReadymadeSelection = "naked-shorts" | TemplateId;

type UncoveredSidePayload = {
  Status?: number;
  Success?: Record<string, unknown>[];
  Error?: string | null;
};

type UncoveredScanResponse = {
  ce_options: UncoveredSidePayload;
  pe_options: UncoveredSidePayload;
};

function parseNum(v: unknown): number {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string") {
    const n = parseFloat(v.replace(/,/g, ""));
    return Number.isFinite(n) ? n : NaN;
  }
  return NaN;
}

function snapToStrike(raw: number, strikes: number[]): number {
  if (!strikes.length) return raw;
  let best = strikes[0];
  let bd = Infinity;
  for (const s of strikes) {
    const d = Math.abs(s - raw);
    if (d < bd) {
      bd = d;
      best = s;
    }
  }
  return best;
}

function atmSigmaFromChain(chain: ChainSuccess, T: number): number {
  const spot = chain.spot_price;
  const atm = chain.atm_strike;
  if (spot == null || atm == null) return 0.22;
  const row = chain.chain_rows.find((r) => r.strike_price === atm);
  if (!row) return 0.22;
  const ivs: number[] = [];
  const c = parseNum(row.call?.ltp);
  const p = parseNum(row.put?.ltp);
  if (c > 0) {
    const iv = impliedVolatility("call", c, spot, atm, T);
    if (iv != null) ivs.push(iv);
  }
  if (p > 0) {
    const iv = impliedVolatility("put", p, spot, atm, T);
    if (iv != null) ivs.push(iv);
  }
  if (!ivs.length) return 0.22;
  return ivs.reduce((a, b) => a + b, 0) / ivs.length;
}

const OTM_SLIDER_MIN = 1;
const OTM_SLIDER_MAX = 20;
const MARGIN_LACS_MAX = 999_999;

function UncoveredNumberStepper({
  label,
  value,
  onChange,
  min,
  max,
  suffix,
  compact = false,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
  min: number;
  max: number;
  suffix?: ReactNode;
  /** Inline / grid layouts (e.g. Naked Shorts parameters). */
  compact?: boolean;
}) {
  const stepBtn = compact
    ? "flex h-8 w-8 shrink-0 items-center justify-center border-0 bg-zinc-100 text-base font-light leading-none text-zinc-700 transition hover:bg-zinc-200 active:bg-zinc-300 disabled:cursor-not-allowed disabled:opacity-40 dark:bg-zinc-800 dark:text-zinc-200 dark:hover:bg-zinc-700 dark:active:bg-zinc-600"
    : "flex h-11 w-11 shrink-0 items-center justify-center border-0 bg-zinc-100 text-xl font-light leading-none text-zinc-700 transition hover:bg-zinc-200 active:bg-zinc-300 disabled:cursor-not-allowed disabled:opacity-40 dark:bg-zinc-800 dark:text-zinc-200 dark:hover:bg-zinc-700 dark:active:bg-zinc-600";

  const round = compact ? "rounded-lg" : "rounded-xl";
  const roundL = compact ? "rounded-none rounded-l-lg" : "rounded-none rounded-l-xl";
  const roundR = compact ? "rounded-none rounded-r-lg" : "rounded-none rounded-r-xl";
  const minH = compact ? "min-h-8" : "min-h-11";
  const inputPad = compact ? "py-1" : "py-2";
  const inputText = "text-center text-sm font-semibold tabular-nums";

  return (
    <div className="min-w-0">
      <span
        className={
          compact
            ? "mb-1 block text-[11px] font-medium text-zinc-500 dark:text-zinc-400"
            : sb.fieldLabel
        }
      >
        {label}
      </span>
      <div
        className={`flex overflow-hidden border border-zinc-200/90 shadow-sm dark:border-zinc-700 ${round}`}
      >
        <button
          type="button"
          className={`${stepBtn} ${roundL}`}
          onClick={() => onChange(Math.max(min, value - 1))}
          disabled={value <= min}
          aria-label={`Decrease ${label}`}
        >
          −
        </button>
        <div
          className={`flex ${minH} min-w-0 flex-1 items-stretch border-x border-zinc-200/90 bg-white dark:border-zinc-700 dark:bg-zinc-950`}
        >
          <input
            type="number"
            min={min}
            max={max}
            value={value}
            onChange={(e) => {
              const n = parseInt(e.target.value, 10);
              if (!Number.isFinite(n)) return;
              onChange(Math.min(max, Math.max(min, n)));
            }}
            className={`min-w-0 flex-1 border-0 bg-transparent ${inputPad} ${inputText} text-zinc-900 outline-none focus:ring-0 dark:text-zinc-100 [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none`}
            aria-label={label}
          />
          {suffix != null ? (
            <div
              className={
                compact
                  ? "flex shrink-0 items-center border-l border-zinc-200/90 bg-zinc-50/90 px-2 text-[10px] font-semibold uppercase tracking-wide text-zinc-500 dark:border-zinc-700 dark:bg-zinc-900/50 dark:text-zinc-400"
                  : "flex shrink-0 items-center border-l border-zinc-200/90 bg-zinc-50/90 px-3 text-xs font-semibold tracking-wide text-zinc-500 dark:border-zinc-700 dark:bg-zinc-900/50 dark:text-zinc-400"
              }
            >
              {suffix}
            </div>
          ) : null}
        </div>
        <button
          type="button"
          className={`${stepBtn} ${roundR}`}
          onClick={() => onChange(Math.min(max, value + 1))}
          disabled={value >= max}
          aria-label={`Increase ${label}`}
        >
          +
        </button>
      </div>
    </div>
  );
}

function UncoveredOtmSlider({
  label,
  value,
  onChange,
  ariaLabel,
  compact = false,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
  ariaLabel: string;
  compact?: boolean;
}) {
  const pct =
    ((value - OTM_SLIDER_MIN) / (OTM_SLIDER_MAX - OTM_SLIDER_MIN)) * 100;

  const valueAboveKnobClass =
    "pointer-events-none absolute top-0 z-10 -translate-x-1/2 whitespace-nowrap rounded-full border border-zinc-200/80 bg-white px-2 py-0.5 text-xs font-semibold tabular-nums text-zinc-800 shadow-sm ring-1 ring-zinc-950/[0.04] dark:border-zinc-600/90 dark:bg-zinc-800 dark:text-zinc-100 dark:ring-white/10";

  if (compact) {
    return (
      <div className="min-w-0 w-full">
        <div className="mb-1.5 text-[11px] font-medium leading-snug text-zinc-500 dark:text-zinc-400">
          {label}
        </div>
        <div className="relative pt-6">
          <div
            className={valueAboveKnobClass}
            style={{
              left: `clamp(0.75rem, ${pct}%, calc(100% - 0.75rem))`,
            }}
            aria-hidden
          >
            {value}%
          </div>
          <input
            type="range"
            min={OTM_SLIDER_MIN}
            max={OTM_SLIDER_MAX}
            value={value}
            onChange={(e) => onChange(Number(e.target.value))}
            className="sb-range-slim relative z-0 w-full min-w-0"
            aria-label={ariaLabel}
            aria-valuetext={`${value} percent OTM`}
          />
        </div>
      </div>
    );
  }

  return (
    <div className="min-w-0">
      <div className="mb-2 text-xs font-medium text-zinc-600 dark:text-zinc-400">
        {label}
      </div>
      <div className="relative pt-6">
        <div
          className="pointer-events-none absolute top-0 z-10 -translate-x-1/2 whitespace-nowrap rounded-full border border-zinc-200/90 bg-white px-2 py-1 text-[11px] font-semibold tabular-nums text-zinc-800 shadow-sm ring-1 ring-zinc-950/5 dark:border-zinc-600 dark:bg-zinc-800 dark:text-zinc-100 dark:ring-white/10"
          style={{
            left: `clamp(1.25rem, ${pct}%, calc(100% - 1.25rem))`,
          }}
          aria-hidden
        >
          {value}%
        </div>
        <input
          type="range"
          min={OTM_SLIDER_MIN}
          max={OTM_SLIDER_MAX}
          value={value}
          onChange={(e) => onChange(Number(e.target.value))}
          className="sb-range-slim relative z-0 w-full"
          aria-label={ariaLabel}
          aria-valuetext={`${value} percent OTM`}
        />
      </div>
    </div>
  );
}

const BOOK_LAKH = 100_000;

function formatBookQtyInL(n: number): string {
  if (!Number.isFinite(n)) return "—";
  return `${(n / BOOK_LAKH).toLocaleString("en-IN", {
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  })} L`;
}

function formatPremiumFullInr(p: number): string {
  if (!Number.isFinite(p)) return "—";
  return `₹${p.toLocaleString("en-IN", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

function buildUncoveredScanRoiTooltip(
  premium: number,
  carryPct: number,
  dte: number,
  spanTotal: number,
): string {
  const lines = [
    "Annualised ROI on SPAN (%):",
    "(premium ÷ DTE) × 365 ÷ span × 100",
    "",
    `Premium (bid × sized qty) = ${formatPremiumFullInr(premium)}`,
    `DTE (days to expiry) = ${dte}`,
    `span (total SPAN for this line) = ${formatPremiumFullInr(spanTotal)}`,
    "",
    `(${premium.toLocaleString("en-IN", { maximumFractionDigits: 2 })} ÷ ${dte}) × 365 ÷ ${spanTotal.toLocaleString("en-IN", { maximumFractionDigits: 2 })} × 100`,
    `≈ ${carryPct.toFixed(2)}%`,
  ];
  return lines.join("\n");
}

/** Backend sends DD-Mon-YYYY; show compact DD-Mmm for titles (e.g. 09-Mar-2026 → 09-Mar). */
function formatExpiryDdMmm(raw: unknown): string | null {
  const s = String(raw ?? "").trim();
  if (!s) return null;
  const parts = s.split("-");
  if (parts.length >= 2 && /^\d{1,2}$/.test(parts[0]!) && /^[A-Za-z]{3}$/.test(parts[1]!)) {
    const day = parts[0]!.padStart(2, "0");
    const mon =
      parts[1]!.slice(0, 1).toUpperCase() + parts[1]!.slice(1).toLowerCase();
    return `${day}-${mon}`;
  }
  return null;
}

function formatPremiumIntegerInr(p: number): string {
  if (!Number.isFinite(p)) return "—";
  const r = Math.round(p);
  return `₹${r.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
}

function UncoveredScanOptionCard({
  opt,
  onSell,
  onAddToBasket,
}: {
  opt: Record<string, unknown>;
  onSell: () => void;
  onAddToBasket: () => void;
}) {
  const prem = parseNum(opt.premium);
  const cr = parseNum(opt.carry_returns);
  const strike = parseNum(opt.strike_price);
  const qty = parseNum(opt.quantity);
  const ltp = parseNum(opt.ltp);
  const bid = parseNum(opt.best_bid_price);
  const ask = parseNum(opt.best_offer_price);
  const buyQty = parseNum(opt.total_buy_qty);
  const sellQty = parseNum(opt.total_sell_qty);
  const ratioRaw = opt.buy_sell_ratio;
  const ratioStr =
    typeof ratioRaw === "number" && Number.isFinite(ratioRaw)
      ? ratioRaw.toLocaleString("en-IN", { maximumFractionDigits: 2 })
      : typeof ratioRaw === "string" && ratioRaw.trim()
        ? ratioRaw.trim()
        : "—";

  const stock = String(opt.stock_code ?? "").trim();
  const rightRaw = String(opt.right ?? "");
  const abbr = /^p/i.test(rightRaw) ? "PE" : "CE";
  const expShort = formatExpiryDdMmm(opt.expiry_date);
  const strikeLine = Number.isFinite(strike)
    ? (() => {
        const expSeg = expShort ? `${expShort} ` : "";
        const strikeSeg = `${strike.toLocaleString("en-IN")} ${abbr}`;
        return stock
          ? `${stock} ${expSeg}${strikeSeg}`.trim()
          : `${expSeg}${strikeSeg}`.trim();
      })()
    : expShort
      ? `${expShort} — ${abbr}`
      : `— ${abbr}`;

  const qtyLabel = Number.isFinite(qty)
    ? qty.toLocaleString("en-IN", { maximumFractionDigits: 0 })
    : "—";

  const premLabel = formatPremiumIntegerInr(prem);
  const roiLabel = Number.isFinite(cr) ? `${cr.toFixed(1)}%` : "—";
  const dteRoi = parseNum(opt.days_to_expiry);
  const spanRoi = parseNum(opt.span_margin_total);
  const roiTooltip =
    Number.isFinite(prem) &&
    Number.isFinite(cr) &&
    Number.isFinite(dteRoi) &&
    Number.isFinite(spanRoi) &&
    dteRoi > 0 &&
    spanRoi > 0
      ? buildUncoveredScanRoiTooltip(prem, cr, dteRoi, spanRoi)
      : [
          "Annualised ROI on SPAN (%): (premium ÷ DTE) × 365 ÷ span × 100.",
          "Run an uncovered scan to attach premium, DTE, and span to this card.",
        ].join("\n");

  const fmtPx = (n: number) =>
    Number.isFinite(n) ? n.toLocaleString("en-IN", { maximumFractionDigits: 2 }) : "—";

  const vBar = (
    <span
      className="mx-1.5 h-4 w-px shrink-0 self-center bg-zinc-200 dark:bg-zinc-600"
      aria-hidden
    />
  );

  return (
    <div className="w-fit min-w-0 max-w-[min(100%,25rem)] rounded-xl border border-zinc-200/80 bg-white/90 p-2.5 shadow-sm backdrop-blur-sm dark:border-zinc-700/80 dark:bg-zinc-950/60">
      <div className="space-y-2">
        <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-sm leading-snug">
          <div className="min-w-0 max-w-[18rem] shrink truncate font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
            {strikeLine}
          </div>
          {vBar}
          <div className="shrink-0 tabular-nums font-medium text-zinc-600 dark:text-zinc-300">
            ×{qtyLabel}
          </div>
          {vBar}
          <div
            className="shrink-0 rounded-full bg-emerald-500/14 px-2.5 py-1 text-xs font-semibold tabular-nums text-emerald-800 ring-1 ring-emerald-600/20 dark:bg-emerald-500/12 dark:text-emerald-300 dark:ring-emerald-400/30"
            title={roiTooltip}
          >
            {premLabel}{" "}
            <span className="font-normal text-emerald-700/90 dark:text-emerald-400/90">
              ({roiLabel})
            </span>
          </div>
        </div>

        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 text-xs text-zinc-500 dark:text-zinc-400">
          <span className="shrink-0 tabular-nums whitespace-nowrap">
            <span className="font-medium text-zinc-400 dark:text-zinc-500">
              LTP
            </span>{" "}
            <span className="font-semibold text-zinc-800 dark:text-zinc-200">
              {fmtPx(ltp)}
            </span>
          </span>
          <span className="shrink-0 tabular-nums whitespace-nowrap">
            <span className="font-medium text-zinc-400 dark:text-zinc-500">
              Bid
            </span>{" "}
            {fmtPx(bid)}
            <span className="mx-1 text-zinc-300 dark:text-zinc-600">/</span>
            <span className="font-medium text-zinc-400 dark:text-zinc-500">
              Offer
            </span>{" "}
            {fmtPx(ask)}
          </span>
        </div>

        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 border-t border-zinc-100 pt-1.5 text-xs text-zinc-500 dark:border-zinc-800 dark:text-zinc-400">
          <span className="min-w-0 shrink-0 tabular-nums whitespace-nowrap">
            <span className="font-medium text-zinc-400 dark:text-zinc-500">
              Buy
            </span>{" "}
            <span className="font-semibold text-zinc-800 dark:text-zinc-200">
              {formatBookQtyInL(buyQty)}
            </span>
            <span className="mx-1 text-zinc-300 dark:text-zinc-600">/</span>
            <span className="font-medium text-zinc-400 dark:text-zinc-500">
              Sell
            </span>{" "}
            <span className="font-semibold text-zinc-800 dark:text-zinc-200">
              {formatBookQtyInL(sellQty)}
            </span>
          </span>
          <span className="shrink-0 tabular-nums whitespace-nowrap">
            <span className="font-medium text-zinc-400 dark:text-zinc-500">
              B:S
            </span>{" "}
            <span className="font-semibold text-zinc-800 dark:text-zinc-200">
              {ratioStr}
            </span>
          </span>
        </div>
      </div>

      <div className="mt-2.5 space-y-2">
        <button
          type="button"
          className="w-full rounded-lg border border-sky-600 bg-transparent py-2.5 text-sm font-semibold text-sky-700 transition hover:bg-sky-600 hover:text-white focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-500/50 dark:border-sky-500 dark:text-sky-300 dark:hover:bg-sky-600 dark:hover:text-white"
          onClick={onSell}
        >
          Sell
        </button>
        <button
          type="button"
          className="w-full rounded-lg border border-transparent py-1 text-sm font-medium text-zinc-500 underline-offset-2 transition hover:text-zinc-800 hover:underline dark:text-zinc-400 dark:hover:text-zinc-200"
          onClick={onAddToBasket}
        >
          Add to basket
        </button>
      </div>
    </div>
  );
}

export default function StrategyBuilderPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const { openOrderConfirm } = useOrderConfirm();
  const [stockCode, setStockCode] = useState("");
  const [expiryDate, setExpiryDate] = useState("");
  const [legs, setLegs] = useState<StrategyLeg[]>([]);
  const [globalMult, setGlobalMult] = useState(1);
  const [ivShockPct, setIvShockPct] = useState(0);
  const [showGreeks, setShowGreeks] = useState(false);
  const [showToday, setShowToday] = useState(true);
  const [nakedPrompt, setNakedPrompt] = useState<TemplateId | null>(null);
  const [executeSummary, setExecuteSummary] = useState<ExecuteApiResponse | null>(null);
  const [selectedReadymade, setSelectedReadymade] =
    useState<ReadymadeSelection | null>(null);
  const [scanLimits, setScanLimits] = useState(5);
  const [scanTop, setScanTop] = useState(10);
  const [scanOtmCall, setScanOtmCall] = useState(10);
  const [scanOtmPut, setScanOtmPut] = useState(10);
  const [scanProvisionElm, setScanProvisionElm] = useState(false);
  const [uncoveredScanResult, setUncoveredScanResult] =
    useState<UncoveredScanResponse | null>(null);
  const [uncoveredResultExchange, setUncoveredResultExchange] = useState<
    "NFO" | "BFO"
  >("NFO");
  const [scanError, setScanError] = useState<string | null>(null);
  const [segmentExchange, setSegmentExchange] = useState<"NFO" | "BFO">("NFO");

  const onSegmentChange = (ex: "NFO" | "BFO") => {
    if (ex === segmentExchange) return;
    setSegmentExchange(ex);
    setStockCode("");
    setExpiryDate("");
    setLegs([]);
    setSelectedReadymade(null);
    setUncoveredScanResult(null);
  };

  const uq = useQuery({
    queryKey: ["strategy-builder", "underlyings", segmentExchange],
    queryFn: () =>
      apiClient.get<UnderlyingsApiResponse>(
        `/strategy-builder/underlyings?exchange_code=${segmentExchange}`,
      ),
  });

  const expiryOptions = useMemo(() => {
    const raw = uq.data?.underlyings ?? [];
    const u = raw.find((x) => x.stock_code === stockCode);
    return sortExpiryDatesAsc(u?.expiry_dates ?? []);
  }, [uq.data?.underlyings, stockCode]);

  const uncoveredScanMut = useMutation({
    mutationFn: async () => {
      const ex = segmentExchange;
      const q = new URLSearchParams({
        stock_code: stockCode.trim(),
        expiry_date: expiryDate.trim(),
        limits: String(scanLimits),
        top: String(scanTop),
        otm_call_distance: String(scanOtmCall),
        otm_put_distance: String(scanOtmPut),
        exchange_code: ex,
      });
      if (scanProvisionElm) q.set("provision_elm", "on");
      const data = await apiClient.get<UncoveredScanResponse>(
        `/uncovered-shorts/scan?${q.toString()}`,
      );
      return { data, exchange: ex };
    },
    onSuccess: ({ data, exchange }) => {
      setUncoveredScanResult(data);
      setUncoveredResultExchange(exchange);
      setScanError(null);
    },
    onError: (e) => {
      setScanError(e instanceof Error ? e.message : "Scan failed");
    },
  });

  const cq = useQuery({
    queryKey: [
      "strategy-builder",
      "chain",
      stockCode,
      expiryDate,
      segmentExchange,
    ],
    queryFn: ({ signal }) => {
      const q = new URLSearchParams({
        stock_code: stockCode,
        exchange_code: segmentExchange,
        expiry_date: expiryDate,
      });
      return apiClient.get<ChainApiResponse>(
        `/strategy-builder/chain?${q.toString()}`,
        signal,
      );
    },
    enabled: Boolean(stockCode && expiryDate),
  });

  const chainSuccess = cq.data?.Status === 200 ? cq.data?.Success : null;
  const lotSize = useMemo(() => {
    if (!chainSuccess?.chain_rows?.length) return 1;
    const row = chainSuccess.chain_rows[0];
    const ls = parseNum(row.call?.lot_size) || parseNum(row.put?.lot_size);
    return Number.isFinite(ls) && ls > 0 ? Math.round(ls) : 1;
  }, [chainSuccess]);

  const strikes = useMemo(
    () =>
      (chainSuccess?.chain_rows ?? [])
        .map((r) => r.strike_price)
        .sort((a, b) => a - b),
    [chainSuccess],
  );

  const T = expiryDisplayToYears(expiryDate || "01-Jan-2099");
  const baseSigma = chainSuccess ? atmSigmaFromChain(chainSuccess, T) : 0.22;
  const sigma = baseSigma * (1 + ivShockPct / 100);

  const scaledLegs = useMemo(
    () =>
      legs.map((l) => ({
        ...l,
        lots: Math.max(1, Math.round(l.lots * globalMult)),
      })),
    [legs, globalMult],
  );

  const spot = chainSuccess?.spot_price ?? null;
  const minS = useMemo(() => {
    if (!strikes.length) return spot != null ? spot * 0.85 : 0;
    const lo = Math.min(...strikes);
    const hi = Math.max(...strikes);
    const pad = (hi - lo) * 0.15;
    return Math.max(0, lo - pad);
  }, [strikes, spot]);

  const maxS = useMemo(() => {
    if (!strikes.length) return spot != null ? spot * 1.15 : 1;
    const lo = Math.min(...strikes);
    const hi = Math.max(...strikes);
    const pad = (hi - lo) * 0.15;
    return hi + pad;
  }, [strikes, spot]);

  const { xs, ys, summary, xsToday, ysToday, pop, greeks } = useMemo(() => {
    const steps = 80;
    const { xs: x1, ys: y1 } = scanPayoffCurve(
      minS,
      maxS,
      steps,
      scaledLegs,
      lotSize,
    );
    const sum = summarizePayoffScan(x1, y1);
    let xt: number[] = [];
    let yt: number[] = [];
    if (showToday && spot != null && T > 0 && scaledLegs.length) {
      const r = scanMarkToModelCurve(
        minS,
        maxS,
        steps,
        scaledLegs,
        lotSize,
        T,
        sigma,
      );
      xt = r.xs;
      yt = r.ys;
    }
    const pop =
      spot != null && scaledLegs.length
        ? estimateProbabilityOfProfit(
            spot,
            T,
            sigma,
            scaledLegs,
            lotSize,
          )
        : 0;
    const greeks =
      spot != null && T > 0 && scaledLegs.length
        ? portfolioGreeks(spot, scaledLegs, lotSize, T, sigma)
        : { delta: 0, gamma: 0, vega: 0, thetaPerDay: 0 };
    return {
      xs: x1,
      ys: y1,
      summary: sum,
      xsToday: xt,
      ysToday: yt,
      pop,
      greeks,
    };
  }, [
    minS,
    maxS,
    scaledLegs,
    lotSize,
    showToday,
    spot,
    T,
    sigma,
  ]);

  const marginLegKey = useMemo(
    () =>
      JSON.stringify({
        segmentExchange,
        stockCode,
        expiryDate,
        lotSize,
        legs: scaledLegs.map((l) => ({
          id: l.id,
          strike: l.strike,
          right: l.right,
          side: l.side,
          lots: l.lots,
          prem: l.premiumPerUnit,
        })),
      }),
    [segmentExchange, stockCode, expiryDate, lotSize, scaledLegs],
  );

  const marginQ = useQuery({
    queryKey: ["strategy-builder", "margin", marginLegKey],
    queryFn: () =>
      apiClient.post<MarginApiResponse>("/strategy-builder/margin", {
        legs: scaledLegs.map((l) => ({
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
    enabled: Boolean(stockCode && expiryDate && scaledLegs.length),
    staleTime: 5000,
  });

  const marginState = marginQ.data;
  const spanMargin =
    marginState?.Status === 200 && marginState.Success
      ? parseNum(
          (marginState.Success as { span_margin_required?: unknown })
            .span_margin_required,
        )
      : null;

  const chainReady = Boolean(chainSuccess);
  const hasStrategyLegs = scaledLegs.length > 0;

  const applyTemplateId = useCallback(
    (id: TemplateId) => {
      if (!chainSuccess) return;
      const ctx = buildTemplateContext(chainSuccess.chain_rows, spot);
      if (!ctx) return;
      setLegs(applyTemplate(id, ctx));
    },
    [chainSuccess, spot],
  );

  const onPickTemplate = (id: TemplateId) => {
    setSelectedReadymade(id);
    const meta = STRATEGY_TEMPLATES.find((t) => t.id === id);
    if (meta?.naked) {
      setNakedPrompt(id);
      return;
    }
    applyTemplateId(id);
  };

  const confirmNaked = () => {
    if (nakedPrompt) applyTemplateId(nakedPrompt);
    setNakedPrompt(null);
  };

  const strikeCommit = useCallback(
    (fromStrike: number, raw: number) => {
      const to = snapToStrike(raw, strikes);
      if (to === fromStrike) return;
      setLegs((prev) =>
        prev.map((l) =>
          l.strike === fromStrike ? { ...l, strike: to } : l,
        ),
      );
    },
    [strikes],
  );

  const execMut = useMutation({
    mutationFn: async () => {
      const legsPayload = scaledLegs.map((l) => ({
        stock_code: stockCode,
        exchange_code: segmentExchange,
        expiry_date: expiryDate,
        product_type: "Options",
        right: l.right,
        strike_price: String(l.strike),
        quantity: String(Math.round(l.lots * lotSize)),
        price: String(l.premiumPerUnit ?? 0),
        action: l.side,
        idempotency_key:
          typeof crypto !== "undefined" && crypto.randomUUID
            ? crypto.randomUUID()
            : undefined,
      }));
      return apiClient.post<ExecuteApiResponse>(
        "/strategy-builder/execute",
        { legs: legsPayload },
      );
    },
    onSuccess: (data) => {
      setExecuteSummary(data);
      if (data.placed_count > 0) {
        void queryClient.invalidateQueries({ queryKey: ["book"] });
        router.push("/orders");
      }
    },
  });

  const sellUncoveredOption = useCallback(
    (opt: Record<string, unknown>, ex: string) => {
      openOrderConfirm({
        product_type: "Options",
        stock_code: String(opt.stock_code ?? "").trim(),
        exchange_code: ex,
        expiry_date: String(opt.expiry_date ?? "").trim(),
        right: String(opt.right ?? "").trim(),
        strike_price: String(opt.strike_price ?? "").trim(),
        quantity: String(opt.quantity ?? "").trim(),
        price: ltpAsOrderPrice(opt.best_bid_price),
        action: "Sell",
      });
    },
    [openOrderConfirm],
  );

  const addUncoveredToBasket = useCallback(
    (opt: Record<string, unknown>) => {
      const k = parseNum(opt.strike_price);
      if (!Number.isFinite(k)) return;
      const qty = parseNum(opt.quantity);
      const rightStr = String(opt.right ?? "Call");
      const right: OptionRight = /^p/i.test(rightStr) ? "Put" : "Call";
      const ls = lotSize > 0 ? lotSize : 1;
      const lots =
        Number.isFinite(qty) && ls > 0
          ? Math.max(1, Math.round(qty / ls))
          : 1;
      const ltpV = parseNum(opt.ltp);
      const bidV = parseNum(opt.best_bid_price);
      const premiumPerUnit =
        Number.isFinite(ltpV) && ltpV > 0
          ? ltpV
          : Number.isFinite(bidV) && bidV > 0
            ? bidV
            : undefined;
      setLegs((prev) => [
        ...prev,
        {
          id: `leg-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`,
          side: "Sell",
          right,
          strike: k,
          lots,
          premiumPerUnit,
        },
      ]);
    },
    [lotSize],
  );

  const profitClass =
    "text-emerald-700 dark:text-emerald-400";
  const lossClass = "text-red-700 dark:text-red-400";

  return (
    <AppShell contentWidth="wide">
      <div className="space-y-5">
        <header className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <h1 className="text-xl font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
              Strategy Builder
            </h1>
            <p className="mt-2 max-w-2xl text-sm leading-relaxed text-zinc-600 dark:text-zinc-400">
              Readymade templates and orchestrated multi-leg placement
            </p>
          </div>
          <Link href="/strategies" className={`${sb.btnSecondary} shrink-0`}>
            Strategies hub
          </Link>
        </header>

        <section className={`${sb.section} relative z-20 space-y-5`}>
          <h2 className={sb.sectionTitle}>1. Underlying &amp; expiry</h2>
          <div
            className="flex min-h-[2.75rem] flex-col overflow-visible rounded-xl bg-[#1b1c1f] sm:flex-row sm:items-center"
            role="toolbar"
            aria-label="Underlying and expiry"
          >
            <div
              className="flex shrink-0 items-center border-b border-zinc-700/70 px-2 py-2 sm:border-b-0 sm:border-r sm:border-zinc-700/70 sm:py-0 sm:ps-2.5 sm:pe-2"
              role="group"
              aria-label="Exchange segment"
            >
              <div className="inline-flex rounded-lg bg-black/30 p-0.5 ring-1 ring-zinc-700/70">
                <button
                  type="button"
                  onClick={() => onSegmentChange("NFO")}
                  className={`rounded-md px-3 py-1.5 text-xs font-semibold transition focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-500/45 sm:text-sm ${
                    segmentExchange === "NFO"
                      ? "bg-zinc-700 text-white shadow-sm"
                      : "text-zinc-400 hover:bg-zinc-800/80 hover:text-zinc-200"
                  }`}
                >
                  NSE
                </button>
                <button
                  type="button"
                  onClick={() => onSegmentChange("BFO")}
                  className={`rounded-md px-3 py-1.5 text-xs font-semibold transition focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-500/45 sm:text-sm ${
                    segmentExchange === "BFO"
                      ? "bg-zinc-700 text-white shadow-sm"
                      : "text-zinc-400 hover:bg-zinc-800/80 hover:text-zinc-200"
                  }`}
                >
                  BSE
                </button>
              </div>
            </div>

            <div className="relative z-30 flex min-w-0 max-w-[min(100%,26rem)] flex-1 items-center overflow-visible border-b border-zinc-700/70 px-3 py-2 sm:border-b-0 sm:border-r sm:border-zinc-700/70 sm:py-2.5">
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
                  setLegs([]);
                  setSelectedReadymade(null);
                  setUncoveredScanResult(null);
                }}
              />
            </div>

            <div className="relative z-20 flex shrink-0 items-center overflow-visible px-3 py-2 sm:py-2.5 sm:pe-3.5">
              <ExpirySelectPill
                layout="toolbar"
                tone="darkToolbar"
                dates={expiryOptions}
                value={expiryDate}
                disabled={!stockCode}
                onChange={(d) => {
                  setExpiryDate(d);
                  setLegs([]);
                  setSelectedReadymade(null);
                  setUncoveredScanResult(null);
                }}
              />
            </div>
          </div>
        </section>

        <section className={`${sb.section} relative z-10 space-y-4`}>
          <h2 className={sb.sectionTitle}>2. Readymade strategies</h2>
          <div className="flex flex-wrap gap-3">
            <button
              type="button"
              disabled={
                uq.isLoading || !stockCode.trim() || !expiryDate.trim()
              }
              onClick={() => {
                setScanError(null);
                setSelectedReadymade("naked-shorts");
              }}
              className={`${sb.cardTemplateAmber} ${
                selectedReadymade === "naked-shorts"
                  ? "ring-2 ring-amber-500 ring-offset-2 ring-offset-white dark:ring-offset-zinc-900"
                  : ""
              }`}
              aria-pressed={selectedReadymade === "naked-shorts"}
            >
              <div className="font-semibold">Naked Shorts</div>
              <div className="mt-1 text-zinc-800 dark:text-zinc-200">
                Sell Calls or Puts
              </div>
            </button>
            {STRATEGY_TEMPLATES.map((t) => (
              <button
                key={t.id}
                type="button"
                disabled={!chainSuccess}
                onClick={() => onPickTemplate(t.id)}
                className={`${sb.cardTemplate} ${
                  selectedReadymade === t.id
                    ? "ring-2 ring-sky-500 ring-offset-2 ring-offset-white dark:ring-offset-zinc-900"
                    : ""
                }`}
                aria-pressed={selectedReadymade === t.id}
              >
                <div className="font-semibold">{t.label}</div>
                <div className="mt-1 text-zinc-800 dark:text-zinc-200">
                  {t.description}
                </div>
              </button>
            ))}
          </div>
          {cq.isError ? (
            <div className="app-alert-error text-xs">
              {cq.error instanceof Error ? cq.error.message : "Chain failed"}
            </div>
          ) : null}
        </section>

        <section
          className={`${sb.section} ${
            selectedReadymade == null
              ? "border-dashed border-zinc-300/90 bg-zinc-50/40 py-6 dark:border-zinc-600/80 dark:bg-zinc-950/30"
              : "space-y-4"
          }`}
        >
          <h2 className={sb.sectionTitle}>3. Parameters</h2>
          {selectedReadymade == null ? (
            <p className="text-sm text-zinc-500 dark:text-zinc-400">
              Select a readymade strategy in section 2 to configure parameters.
            </p>
          ) : selectedReadymade === "naked-shorts" ? (
            <div className="flex flex-col gap-3">
              <p className="mt-1 flex flex-wrap items-baseline gap-x-1.5 gap-y-0.5 text-xs text-zinc-500 dark:text-zinc-400">
                <span className="font-medium tabular-nums text-zinc-700 dark:text-zinc-300">
                  {segmentExchange === "NFO" ? "NSE" : "BSE"}
                  <span className="mx-1 text-zinc-400 dark:text-zinc-500">
                    ›
                  </span>
                  {stockCode || "—"}
                  <span className="mx-1 text-zinc-400 dark:text-zinc-500">
                    ›
                  </span>
                  {expiryDate || "—"}
                </span>
              </p>

              <div className="grid w-full min-w-0 grid-cols-4 gap-3 sm:gap-4">
                <div className="min-w-0">
                  <UncoveredNumberStepper
                    compact
                    label="Margin to deploy"
                    value={scanLimits}
                    onChange={setScanLimits}
                    min={1}
                    max={MARGIN_LACS_MAX}
                    suffix="Lacs"
                  />
                  <label
                    className={`${sb.checkboxRow} mt-2 gap-2 text-xs font-medium leading-snug text-zinc-600 dark:text-zinc-400`}
                  >
                    <input
                      type="checkbox"
                      className={sb.checkbox}
                      checked={scanProvisionElm}
                      onChange={(e) => setScanProvisionElm(e.target.checked)}
                    />
                    Provision for ELM
                  </label>
                </div>
                <div className="min-w-0">
                  <UncoveredNumberStepper
                    compact
                    label="Options to list"
                    value={scanTop}
                    onChange={setScanTop}
                    min={1}
                    max={500}
                  />
                </div>
                <div className="min-w-0">
                  <UncoveredOtmSlider
                    compact
                    label="Call OTM safety"
                    value={scanOtmCall}
                    onChange={setScanOtmCall}
                    ariaLabel="Call OTM safety margin percent"
                  />
                </div>
                <div className="min-w-0">
                  <UncoveredOtmSlider
                    compact
                    label="Put OTM safety"
                    value={scanOtmPut}
                    onChange={setScanOtmPut}
                    ariaLabel="Put OTM safety margin percent"
                  />
                </div>
              </div>

              {scanError ? (
                <div className="app-alert-error text-xs">{scanError}</div>
              ) : null}
              <div className="flex justify-start">
                <button
                  type="button"
                  disabled={
                    !stockCode.trim() ||
                    !expiryDate.trim() ||
                    uncoveredScanMut.isPending
                  }
                  className={`${sb.btnPrimary} shrink-0 px-4 py-2 text-sm`}
                  onClick={() => {
                    setScanError(null);
                    uncoveredScanMut.mutate();
                  }}
                >
                  {uncoveredScanMut.isPending ? "Loading…" : "Get options"}
                </button>
              </div>
            </div>
          ) : (
            <p className="text-sm text-zinc-600 dark:text-zinc-400">
              No additional parameters for this strategy yet. Strategy-specific
              inputs will appear here as we add them.
            </p>
          )}
        </section>

        <section className={`${sb.section} space-y-4`}>
          <h2 className={sb.sectionTitle}>4. Proposed legs</h2>
          {!uncoveredScanResult ? (
            <p className="text-sm text-zinc-500 dark:text-zinc-400">
              {selectedReadymade === "naked-shorts"
                ? "Configure parameters in section 3 and run “Get options” to see uncovered short candidates."
                : "Proposed legs from readymade strategies will appear here. For Naked Shorts, run a scan in section 3."}
            </p>
          ) : (
            <div className="space-y-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                  Uncovered short candidates
                </h3>
                <button
                  type="button"
                  className={`${sb.btnSecondary} text-[11px]`}
                  onClick={() => {
                    setUncoveredScanResult(null);
                  }}
                >
                  Clear results
                </button>
              </div>
              {(["CALL", "PUT"] as const).map((kind) => {
                const side =
                  kind === "CALL"
                    ? uncoveredScanResult.ce_options
                    : uncoveredScanResult.pe_options;
                const rows = Array.isArray(side?.Success) ? side.Success : [];
                const err = side?.Error;
                const ok = side?.Status === 200 && rows.length > 0;
                const groupShell =
                  kind === "CALL"
                    ? "rounded-xl border border-emerald-200/60 bg-emerald-50/50 p-3 dark:border-emerald-900/35 dark:bg-emerald-950/25"
                    : "rounded-xl border border-red-200/60 bg-red-50/45 p-3 dark:border-red-900/35 dark:bg-red-950/20";

                return (
                  <div key={kind} className={groupShell}>
                    <h4
                      className={
                        kind === "CALL"
                          ? "mb-2 border-b border-emerald-200/60 pb-1.5 text-xs font-semibold uppercase tracking-wide text-emerald-900/80 dark:border-emerald-800/40 dark:text-emerald-200/90"
                          : "mb-2 border-b border-red-200/60 pb-1.5 text-xs font-semibold uppercase tracking-wide text-red-900/80 dark:border-red-800/40 dark:text-red-200/90"
                      }
                    >
                      {kind} options
                    </h4>
                    {!ok ? (
                      <p className="text-xs text-zinc-600 dark:text-zinc-400">
                        {err ||
                          (rows.length === 0 && side?.Status === 200
                            ? "No candidates returned."
                            : "No data.")}
                      </p>
                    ) : (
                      <div className="flex flex-wrap gap-2">
                        {rows.map((opt, idx) => {
                          const cardKey = `${kind}-${String(opt.strike_price)}-${idx}`;
                          return (
                            <UncoveredScanOptionCard
                              key={cardKey}
                              opt={opt}
                              onSell={() =>
                                sellUncoveredOption(
                                  opt,
                                  uncoveredResultExchange,
                                )
                              }
                              onAddToBasket={() => addUncoveredToBasket(opt)}
                            />
                          );
                        })}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </section>

        <section className={`${sb.section} space-y-5`}>
          <h2 className={sb.sectionTitle}>5. Payoff simulation</h2>
          <div className="sticky top-0 z-10 -mx-0.5 py-1">
            <div
              className={`${sb.stickyBar} flex flex-wrap gap-x-6 gap-y-3 text-xs`}
            >
              <div>
                <div className="text-[11px] font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                  Max profit
                </div>
                <div className={`font-semibold tabular-nums ${profitClass}`}>
                  {hasStrategyLegs
                    ? formatIndianMoneyCompact(summary.maxProfit)
                    : "—"}
                </div>
              </div>
              <div>
                <div className="text-[11px] font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                  Max loss
                </div>
                <div className={`font-semibold tabular-nums ${lossClass}`}>
                  {hasStrategyLegs
                    ? formatIndianMoneyCompact(summary.maxLoss)
                    : "—"}
                </div>
              </div>
              <div>
                <div className="text-[11px] font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                  Breakevens
                </div>
                <div className="font-medium text-zinc-800 dark:text-zinc-200">
                  {hasStrategyLegs && summary.breakevens.length
                    ? summary.breakevens.map((b) => b.toFixed(0)).join(", ")
                    : "—"}
                </div>
              </div>
              <div>
                <div className="text-[11px] font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                  POP (model)
                </div>
                <div className="font-medium tabular-nums text-zinc-800 dark:text-zinc-200">
                  {scaledLegs.length ? `${pop.toFixed(1)}%` : "—"}
                </div>
              </div>
              <div>
                <div className="text-[11px] font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                  Margin (SPAN)
                </div>
                <div className="font-medium tabular-nums text-zinc-800 dark:text-zinc-200">
                  {marginQ.isFetching
                    ? "…"
                    : spanMargin != null && Number.isFinite(spanMargin)
                      ? formatIndianMoneyCompact(spanMargin)
                      : marginState?.Error ??
                        (marginQ.isError ? "Margin request failed" : "—")}
                </div>
              </div>
              <div>
                <div className="text-[11px] font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                  Spot / IV
                </div>
                <div className="font-medium tabular-nums text-zinc-800 dark:text-zinc-200">
                  {chainReady && spot != null ? (
                    <>
                      {spot.toFixed(2)} · {(sigma * 100).toFixed(1)}%
                    </>
                  ) : (
                    "—"
                  )}
                </div>
              </div>
            </div>
          </div>

          <div className="space-y-4 border-t border-zinc-200 pt-4 dark:border-zinc-800">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                Payoff chart
              </h3>
              <label
                className={`${sb.checkboxRow} text-xs font-medium text-zinc-700 dark:text-zinc-300`}
              >
                <input
                  type="checkbox"
                  className={sb.checkbox}
                  checked={showToday}
                  onChange={(e) => setShowToday(e.target.checked)}
                />
                Show today (model)
              </label>
            </div>
            <p className="text-[11px] leading-relaxed text-zinc-500 dark:text-zinc-400">
              Solid green = P&amp;L at expiry; dotted violet = mark-to-model now.
              Amber dashes = breakevens. Drag strike handles to snap to chain
              strikes.
            </p>
            <PayoffChart
              idle={!hasStrategyLegs}
              xs={xs}
              ys={ys}
              xsToday={showToday ? xsToday : undefined}
              ysToday={showToday ? ysToday : undefined}
              spot={spot}
              breakevens={summary.breakevens}
              strikes={scaledLegs.map((l) => l.strike)}
              minS={minS}
              maxS={maxS}
              onStrikeCommit={strikes.length ? strikeCommit : undefined}
            />
          </div>

          <div className="space-y-4 border-t border-zinc-200 pt-4 dark:border-zinc-800">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                Greeks &amp; IV shock
              </h3>
              <label
                className={`${sb.checkboxRow} text-xs font-medium text-zinc-700 dark:text-zinc-300`}
              >
                <input
                  type="checkbox"
                  className={sb.checkbox}
                  checked={showGreeks}
                  onChange={(e) => setShowGreeks(e.target.checked)}
                />
                Show portfolio Greeks
              </label>
            </div>
            <div className="flex min-w-0 max-w-md flex-col gap-2 sm:flex-row sm:items-center">
              <span className="shrink-0 text-xs font-medium tabular-nums text-zinc-600 dark:text-zinc-400">
                IV shock: {ivShockPct >= 0 ? "+" : ""}
                {ivShockPct}%
              </span>
              <input
                type="range"
                min={-20}
                max={20}
                value={ivShockPct}
                onChange={(e) => setIvShockPct(Number(e.target.value))}
                className={`${sb.range} min-w-0 flex-1 sm:max-w-xs`}
              />
            </div>
            {showGreeks && scaledLegs.length ? (
              <div className="app-table-wrap">
                <table className="w-full min-w-[280px] border-collapse text-left text-xs">
                  <thead className="app-table-head">
                    <tr>
                      <th className="px-2 py-1.5 font-medium">Δ</th>
                      <th className="px-2 py-1.5 font-medium">Γ</th>
                      <th className="px-2 py-1.5 font-medium">Vega</th>
                      <th className="px-2 py-1.5 font-medium">Θ / day</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr className="app-table-row">
                      <td className="px-2 py-1.5 tabular-nums">
                        {greeks.delta.toFixed(4)}
                      </td>
                      <td className="px-2 py-1.5 tabular-nums">
                        {greeks.gamma.toFixed(6)}
                      </td>
                      <td className="px-2 py-1.5 tabular-nums">
                        {greeks.vega.toFixed(4)}
                      </td>
                      <td className="px-2 py-1.5 tabular-nums">
                        {greeks.thetaPerDay.toFixed(4)}
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
            ) : null}
          </div>
        </section>

        <section className={`${sb.section} space-y-4`}>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className={sb.sectionTitle}>6. Legs</h2>
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs font-medium text-zinc-600 dark:text-zinc-400">
                Global lots ×{globalMult}
              </span>
              {[1, 2, 5, 10].map((n) => (
                <button
                  key={n}
                  type="button"
                  className={`${sb.outlookChip} ${globalMult === n ? sb.outlookChipOn : sb.outlookChipOff}`}
                  onClick={() => setGlobalMult(n)}
                >
                  {n}×
                </button>
              ))}
            </div>
          </div>
          <p className="text-[11px] text-zinc-500 dark:text-zinc-400">
            Lot size from chain: {lotSize}. Order quantity = lots × {lotSize}.
          </p>
          <div className="app-table-wrap">
            <table className="w-full min-w-[520px] border-collapse text-left text-xs">
              <thead className="app-table-head">
                <tr>
                  <th className="px-2 py-1.5 font-medium">Side</th>
                  <th className="px-2 py-1.5 font-medium">Right</th>
                  <th className="px-2 py-1.5 font-medium">Strike</th>
                  <th className="px-2 py-1.5 font-medium">Lots</th>
                  <th className="px-2 py-1.5 font-medium">Prem.</th>
                  <th className="px-2 py-1.5 font-medium" />
                </tr>
              </thead>
              <tbody>
                {legs.map((l) => (
                  <tr key={l.id} className="app-table-row">
                    <td className="px-2 py-1.5">
                      <button
                        type="button"
                        className={sb.tableToggle}
                        onClick={() =>
                          setLegs((prev) =>
                            prev.map((x) =>
                              x.id === l.id
                                ? {
                                    ...x,
                                    side: x.side === "Buy" ? "Sell" : "Buy",
                                  }
                                : x,
                            ),
                          )
                        }
                      >
                        {l.side}
                      </button>
                    </td>
                    <td className="px-2 py-1.5">
                      <select
                        className={sb.tableSelect}
                        value={l.right}
                        onChange={(e) =>
                          setLegs((prev) =>
                            prev.map((x) =>
                              x.id === l.id
                                ? {
                                    ...x,
                                    right: e.target.value as "Call" | "Put",
                                  }
                                : x,
                            ),
                          )
                        }
                      >
                        <option value="Call">Call</option>
                        <option value="Put">Put</option>
                      </select>
                    </td>
                    <td className="px-2 py-1.5">
                      <select
                        className={`${sb.tableSelect} max-w-[7.5rem]`}
                        value={l.strike}
                        onChange={(e) =>
                          setLegs((prev) =>
                            prev.map((x) =>
                              x.id === l.id
                                ? {
                                    ...x,
                                    strike: Number(e.target.value),
                                  }
                                : x,
                            ),
                          )
                        }
                      >
                        {strikes.map((s) => (
                          <option key={s} value={s}>
                            {s}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td className="px-2 py-1.5">
                      <input
                        type="number"
                        min={1}
                        className={sb.tableInput}
                        value={l.lots}
                        onChange={(e) =>
                          setLegs((prev) =>
                            prev.map((x) =>
                              x.id === l.id
                                ? {
                                    ...x,
                                    lots: Math.max(
                                      1,
                                      parseInt(e.target.value, 10) || 1,
                                    ),
                                  }
                                : x,
                            ),
                          )
                        }
                      />
                    </td>
                    <td className="px-2 py-1.5 tabular-nums">
                      {l.premiumPerUnit != null
                        ? l.premiumPerUnit.toFixed(2)
                        : "—"}
                    </td>
                    <td className="px-2 py-1.5">
                      <button
                        type="button"
                        className="text-red-600 dark:text-red-400"
                        onClick={() =>
                          setLegs((prev) => prev.filter((x) => x.id !== l.id))
                        }
                      >
                        Remove
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              className={sb.btnSecondary}
              disabled={!chainSuccess || strikes.length === 0}
              onClick={() =>
                setLegs((prev) => [
                  ...prev,
                  {
                    id: `leg-${Date.now()}`,
                    side: "Buy",
                    right: "Call",
                    strike: strikes[0]!,
                    lots: 1,
                  },
                ])
              }
            >
              Add leg
            </button>
            <button
              type="button"
              disabled={
                !scaledLegs.length ||
                execMut.isPending ||
                !stockCode ||
                !expiryDate
              }
              onClick={() => execMut.mutate()}
              className={sb.btnPrimary}
            >
              {execMut.isPending ? "Placing…" : "Execute strategy (all legs)"}
            </button>
            <Link href="/orders" className={`${sb.btnSecondary} self-center`}>
              Orders
            </Link>
          </div>
          {execMut.isError ? (
            <div className="app-alert-error text-xs">
              {execMut.error instanceof Error
                ? execMut.error.message
                : "Execute failed"}
            </div>
          ) : null}
          {executeSummary ? (
            <div className="app-card-muted space-y-2 p-3 text-xs">
              <div className="font-medium text-zinc-800 dark:text-zinc-200">
                Last execution: {executeSummary.placed_count} placed,{" "}
                {executeSummary.failed_count} failed
              </div>
              <ul className="list-inside list-disc space-y-1 text-zinc-600 dark:text-zinc-400">
                {executeSummary.legs.map((r) => (
                  <li key={r.index}>
                    Leg {r.index + 1}:{" "}
                    {r.success ? (
                      <span className="text-emerald-700 dark:text-emerald-400">
                        OK
                      </span>
                    ) : (
                      <span className="text-red-700 dark:text-red-400">
                        {r.error ?? "Failed"}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </section>
      </div>

      {nakedPrompt ? (
        <div
          className="fixed inset-0 z-[120] flex items-center justify-center bg-black/50 p-4"
          role="presentation"
          onClick={() => setNakedPrompt(null)}
          onKeyDown={(e) => {
            if (e.key === "Escape") setNakedPrompt(null);
          }}
        >
          <div
            role="dialog"
            aria-modal="true"
            className={`${sb.modalPanel} max-w-md`}
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => e.stopPropagation()}
          >
            <h3 className="text-base font-semibold text-red-700 dark:text-red-400">
              Risk warning — uncovered short
            </h3>
            <p className="text-sm leading-relaxed text-zinc-600 dark:text-zinc-400">
              Short naked options can have large or unlimited loss. Confirm you
              understand margin and risk before adding this leg set.
            </p>
            <div className="flex justify-end gap-2 pt-1">
              <button
                type="button"
                className={sb.btnSecondary}
                onClick={() => setNakedPrompt(null)}
              >
                Cancel
              </button>
              <button
                type="button"
                className={sb.btnDanger}
                onClick={confirmNaked}
              >
                I understand — add
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </AppShell>
  );
}
