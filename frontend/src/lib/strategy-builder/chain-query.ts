import type { Query } from "@tanstack/react-query";
import {
  fetchPortfolioPayoffQuote,
  fetchStrategyBuilderChain,
} from "@/lib/strategy-builder/api";
import type { ChainApiResponse, ChainSuccess } from "@/lib/strategy-builder/types";

export const CHAIN_STALE_MS = 5_000;
export const CHAIN_WS_REFETCH_MS = 2_000;

export type ChainQueryParams = {
  stock_code: string;
  expiry_date: string;
  exchange_code: string;
  subscription_holder?: string;
};

export type ChainQueryOptionsArgs = ChainQueryParams & {
  queryKeyPrefix: string | readonly unknown[];
  enabled?: boolean;
  /** Overrides CHAIN_WS_REFETCH_MS, e.g. to follow the user's P&L recalc interval setting. */
  refetchIntervalMs?: number;
};

export function chainQueryKey(
  prefix: string | readonly unknown[],
  params: ChainQueryParams,
): readonly unknown[] {
  const holder = params.subscription_holder?.trim() || "";
  return [
    ...(Array.isArray(prefix) ? prefix : [prefix]),
    "chain",
    params.exchange_code,
    params.stock_code.trim(),
    params.expiry_date.trim(),
    holder,
  ];
}

export function chainRefetchInterval(intervalMs: number = CHAIN_WS_REFETCH_MS) {
  return (query: Query<ChainApiResponse, Error>): number | false => {
    const src = query.state.data?.Success?.quote_source;
    return src === "websocket" ? intervalMs : false;
  };
}

/**
 * Keeps the previous chain (and its spot price) on screen while only the
 * expiry changes, so switching expiries within the same underlying doesn't
 * flicker. Discards it when the underlying or exchange changes so a stale
 * spot/chain from a different instrument never lingers on screen.
 */
function samePlaceholderInstrument(
  exchange_code: string,
  stock_code: string,
) {
  return (
    prev: ChainApiResponse | undefined,
    prevQuery: Query<ChainApiResponse, Error> | undefined,
  ): ChainApiResponse | undefined => {
    if (!prev || !prevQuery) return undefined;
    const key = prevQuery.queryKey;
    const prevExchange = key[key.length - 4];
    const prevStock = key[key.length - 3];
    return prevExchange === exchange_code && prevStock === stock_code.trim()
      ? prev
      : undefined;
  };
}

/**
 * Extracts a chain response's `Success` payload, but only when it actually
 * belongs to `expiryDate`. `samePlaceholderInstrument` above deliberately
 * carries the *previous* expiry's chain across an expiry switch (keyed on
 * exchange+stock only, not expiry) so the spot price doesn't flicker — but
 * that means `chainQ.data` can briefly hold a different expiry's chain_rows
 * (and thus a different contract's LTP/premium) while the real fetch for the
 * newly selected expiry is still in flight. Every caller that reads strikes,
 * premiums, or LTPs out of a chain response must go through this guard rather
 * than reading `data.Success` directly, or it risks pricing a leg off the
 * wrong expiry's contract.
 */
export function chainSuccessForExpiry(
  data: ChainApiResponse | undefined,
  expiryDate: string,
): ChainSuccess | null {
  if (data?.Status !== 200 || !data.Success) return null;
  return data.Success.expiry_display === expiryDate.trim() ? data.Success : null;
}

export function chainQueryOptions({
  queryKeyPrefix,
  stock_code,
  expiry_date,
  exchange_code,
  subscription_holder,
  enabled = true,
  refetchIntervalMs,
}: ChainQueryOptionsArgs) {
  const params: ChainQueryParams = {
    stock_code,
    expiry_date,
    exchange_code,
    subscription_holder,
  };
  const ready = Boolean(stock_code.trim() && expiry_date.trim());
  return {
    queryKey: chainQueryKey(queryKeyPrefix, params),
    queryFn: ({ signal }: { signal?: AbortSignal }) =>
      fetchStrategyBuilderChain(params, signal),
    enabled: enabled && ready,
    staleTime: CHAIN_STALE_MS,
    refetchInterval: chainRefetchInterval(refetchIntervalMs),
    placeholderData: samePlaceholderInstrument(exchange_code, stock_code),
  };
}

/** Same shape as chainQueryOptions but backed by the ATM-only payoff-quote
 * endpoint — use where the full chain isn't needed (see PortfolioGroupPayoffPanel). */
export function payoffQuoteQueryOptions({
  queryKeyPrefix,
  stock_code,
  expiry_date,
  exchange_code,
  subscription_holder,
  enabled = true,
  refetchIntervalMs,
}: ChainQueryOptionsArgs) {
  const params: ChainQueryParams = {
    stock_code,
    expiry_date,
    exchange_code,
    subscription_holder,
  };
  const ready = Boolean(stock_code.trim() && expiry_date.trim());
  return {
    queryKey: chainQueryKey(queryKeyPrefix, params),
    queryFn: ({ signal }: { signal?: AbortSignal }) =>
      fetchPortfolioPayoffQuote(params, signal),
    enabled: enabled && ready,
    staleTime: CHAIN_STALE_MS,
    refetchInterval: chainRefetchInterval(refetchIntervalMs),
    placeholderData: samePlaceholderInstrument(exchange_code, stock_code),
  };
}
