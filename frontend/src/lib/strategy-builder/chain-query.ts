import type { Query } from "@tanstack/react-query";
import {
  fetchPortfolioPayoffQuote,
  fetchStrategyBuilderChain,
} from "@/lib/strategy-builder/api";
import type { ChainApiResponse } from "@/lib/strategy-builder/types";

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
    placeholderData: (prev: any) => prev,
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
    placeholderData: (prev: any) => prev,
  };
}
