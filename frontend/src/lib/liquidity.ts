"use client";

import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@/lib/api-client";

/**
 * Order-book liquidity check (docs/liquidity-checks-plan.md).
 *
 * The backend walks each leg's live book for its quantity and compares the average fill with the
 * LTP. Manual tickets only warn: a ⚠ beside the quantity, and a red line on the confirmation
 * modal. `ok: null` means nothing could be judged (market closed, no book seen) — show nothing.
 */
export type LiquiditySide = "Buy" | "Sell";

export type LiquidityVerdict = {
  side: LiquiditySide;
  quantity: number;
  ok: boolean | null;
  size_ok: boolean | null;
  source: "depth" | "top_of_book" | "unknown";
  reasons: string[];
  ltp: number | null;
  est_avg_price: number | null;
  deviation_pct: number | null;
  visible_qty: number;
  max_quantity: number | null;
  message: string;
};

export type LiquidityLegQuery = {
  ref: string;
  strike: number;
  right: "Call" | "Put";
  /** Omit to judge both sides (Place Order before Buy/Sell is chosen). */
  side?: LiquiditySide | null;
  /** Contract units. */
  quantity: number;
};

export type LiquidityCheckResponse = {
  legs: Record<string, Partial<Record<LiquiditySide, LiquidityVerdict>>>;
};

export type LiquiditySettings = {
  max_deviation_pct: number;
  min_deviation_ticks: number;
  ltp_stale_seconds: number;
  bounds: {
    max_deviation_pct_min: number;
    max_deviation_pct_max: number;
    min_deviation_ticks_min: number;
    min_deviation_ticks_max: number;
    ltp_stale_seconds_min: number;
    ltp_stale_seconds_max: number;
  };
  depth: { capped: boolean; cap_error: string | null };
};

const BASE = "/api/settings/liquidity";
/** The book moves; re-ask while the ticket is open. */
const REFRESH_MS = 5_000;

export function checkLiquidity(body: {
  exchange_code: string;
  stock_code: string;
  expiry_display: string;
  lot_size?: number | null;
  legs: LiquidityLegQuery[];
}): Promise<LiquidityCheckResponse> {
  return apiClient.post<LiquidityCheckResponse>(`${BASE}/check`, {
    ...body,
    lot_size: body.lot_size && body.lot_size > 0 ? body.lot_size : null,
  });
}

export function getLiquiditySettings(): Promise<LiquiditySettings> {
  return apiClient.get<LiquiditySettings>(BASE);
}

export function putLiquiditySettings(
  body: Partial<Pick<LiquiditySettings, "max_deviation_pct" | "min_deviation_ticks" | "ltp_stale_seconds">>,
): Promise<LiquiditySettings> {
  return apiClient.put<LiquiditySettings>(BASE, body);
}

/** One leg's warning: the failing verdicts' messages, or null when there is nothing to say. */
export type LiquidityWarning = { messages: string[]; sides: LiquiditySide[] } | null;

export function warningFor(
  res: LiquidityCheckResponse | undefined,
  ref: string,
): LiquidityWarning {
  const sides = res?.legs?.[ref];
  if (!sides) return null;
  const failing = (Object.values(sides) as LiquidityVerdict[]).filter(
    (v) => v && v.ok === false && v.message,
  );
  if (!failing.length) return null;
  // Both sides failing for the same reason read as one message per side, named.
  const named = failing.length > 1;
  return {
    sides: failing.map((v) => v.side),
    messages: failing.map((v) => (named ? `${v.side}: ${v.message}` : v.message)),
  };
}

/**
 * Polls the check for a ticket's legs. Disabled for anything that is not an options ticket with
 * a positive quantity; the query key carries the legs, so editing a quantity re-asks at once.
 */
export function useLiquidityCheck(args: {
  exchangeCode: string;
  stockCode: string;
  expiryDisplay: string;
  lotSize?: number | null;
  legs: LiquidityLegQuery[];
  enabled?: boolean;
}) {
  const legs = args.legs.filter((l) => l.quantity > 0 && Number.isFinite(l.strike));
  const enabled =
    (args.enabled ?? true) &&
    legs.length > 0 &&
    Boolean(args.exchangeCode && args.stockCode.trim() && args.expiryDisplay.trim());
  return useQuery({
    queryKey: [
      "liquidity-check",
      args.exchangeCode,
      args.stockCode.trim(),
      args.expiryDisplay.trim(),
      args.lotSize ?? null,
      legs.map((l) => [l.ref, l.strike, l.right, l.side ?? null, l.quantity]),
    ],
    queryFn: () =>
      checkLiquidity({
        exchange_code: args.exchangeCode,
        stock_code: args.stockCode.trim(),
        expiry_display: args.expiryDisplay.trim(),
        lot_size: args.lotSize ?? null,
        legs,
      }),
    enabled,
    refetchInterval: REFRESH_MS,
    // A warning is advisory: a failed check must never block or nag.
    retry: false,
    placeholderData: (prev) => prev,
  });
}
