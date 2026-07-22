"use client";

import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@/lib/api-client";
import { fetchMarketStatus } from "@/lib/market-status";
import { useQuoteFlushRefetchMs } from "@/lib/settings/useQuoteFlushRefetchMs";

export type IndexQuote = {
  ltp: number;
  previous_close: number | null;
  change: number | null;
  change_pct: number | null;
  updated_at: number;
};

export type IndexQuotesResponse = {
  quotes: {
    nifty: IndexQuote | null;
    sensex: IndexQuote | null;
  };
};

/** Live NIFTY/SENSEX spot + day's change for the navbar ticker.
 *
 * Poll cadence follows the user's WS quote-flush-interval setting (see
 * `useQuoteFlushRefetchMs`) instead of a fixed interval. Once the market is
 * closed the backend serves a one-off REST EOD quote that won't change again
 * today, so polling stops entirely -- the query still fires once on mount to
 * pick that value up. */
export function useIndexQuotes() {
  const flushMs = useQuoteFlushRefetchMs();
  const marketStatus = useQuery({
    queryKey: ["settings", "market-status"],
    queryFn: fetchMarketStatus,
    staleTime: 30_000,
    refetchInterval: 30_000,
  });
  const marketOpen = marketStatus.data?.is_open ?? true;

  return useQuery({
    queryKey: ["dashboard", "index-quotes"],
    queryFn: () => apiClient.get<IndexQuotesResponse>("/dashboard/index-quotes"),
    refetchInterval: marketOpen ? flushMs : false,
    staleTime: 0,
  });
}
